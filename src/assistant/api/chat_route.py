"""POST /v1/chat/completions (spec sections 16.3-16.6).

Routes chat turns through the one Deep Agent and utility requests
(Open WebUI title/tag/follow-up generation) through a plain model call
with no agent graph and no CUA (spec section 15.3).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from assistant.agent.context import (
    MAX_RUN_WALL_CLOCK_SECONDS,
    AgentContext,
    RunBudget,
)
from assistant.api.auth import SettingsDep, require_gateway_key
from assistant.api.identity import RequestIdentity, extract_identity
from assistant.api.schemas import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    DeltaMessage,
    GatewayError,
    UsageStats,
)
from assistant.api.streaming import sse_agent_stream
from assistant.api.turns import decide_turn, last_user_content, message_text, normalize_history
from assistant.models import build_chat_model
from assistant.observability.timing import RunTimeline
from assistant.observability.usage import LedgerCallbackHandler, UsageLedger
from assistant.runtime.router import RecipeRequest
from assistant.runtime.runs import RunActionLedger
from assistant.runtime.session import DesktopRunCancelled
from assistant.settings import Settings
from assistant.tools.policy import cua_run_scope

logger = logging.getLogger("assistant.api.chat")

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])


def _user_hash(user_id: str) -> str:
    return f"sha256:{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:12]}"


def _content_digest(messages: list[ChatMessage]) -> str:
    """Digest of the visible conversation content sent by the client."""
    parts: list[str] = []
    for message in messages:
        raw = message.content if isinstance(message.content, str) else str(message.content or "")
        parts.append(f"{message.role}:{raw}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _stream_recipe_response(
    response: ChatCompletionResponse,
    *,
    run_store: Any | None = None,
    run_id: str = "",
    timeline: RunTimeline | None = None,
    terminal_status: str = "completed",
) -> StreamingResponse:
    """Render a fast-path completion as OpenAI-compatible SSE ([DONE] ended).

    Open WebUI always expects the streaming contract when the request set
    stream=true (review P1-4). The terminal run_registry/timeline events
    are written in the generator's finally per master plan F06: returning
    the StreamingResponse object is not completion. ``terminal_status``
    carries the fast path's own truth (a failed recipe is NOT completed).
    """

    async def stream_one() -> AsyncIterator[str]:
        try:
            chunk = ChatCompletionChunk(
                id=response.id,
                created=response.created,
                model=response.model,
                choices=[ChatCompletionChunkChoice(delta=DeltaMessage(
                    role="assistant", content=str(response.choices[0].message.content)
                ))],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"
            chunk.choices[0].delta = DeltaMessage()
            chunk.choices[0].finish_reason = "stop"
            yield f"data: {chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if run_store is not None and run_id:
                await run_store.finish(run_id, terminal_status)
            if timeline is not None:
                timeline.mark_terminal(metadata={"status": terminal_status})

    return StreamingResponse(stream_one(), media_type="text/event-stream")


def _duplicate_response(
    settings: SettingsDep, claim: Any, run_id: str, started: float
) -> ChatCompletionResponse:
    """Observe-only reply for a duplicate delivery (master plan 11.1)."""
    status = claim.status or "running"
    return ChatCompletionResponse(
        id=f"chatcmpl-{run_id}",
        created=int(time.time()),
        model=settings.assistant_model_id,
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(
                    role="assistant",
                    content=(
                        "[This message was already received and is being handled; "
                        "no new actions were started. Current run status: "
                        f"{status}.]"
                    ),
                )
            )
        ],
        usage=UsageStats(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    )


def _final_assistant_text(result: dict[str, Any]) -> str:
    from assistant.api.turns import message_text

    for message in reversed(result.get("messages", [])):
        if getattr(message, "type", "") != "ai":
            continue
        content = message_text(message)
        if content.strip():
            return content
    return ""


def _provider_error(exc: Exception) -> GatewayError:
    try:
        import openai
    except ImportError:  # pragma: no cover - langchain-openai guarantees openai
        openai = None  # type: ignore[assignment]

    if openai is not None and isinstance(exc, openai.APITimeoutError):
        return GatewayError("provider_timeout", "Model provider timed out after retries.")
    if openai is not None and isinstance(exc, openai.APIConnectionError):
        return GatewayError("provider_unavailable", "Model provider is unreachable.")
    if openai is not None and isinstance(exc, openai.APIStatusError):
        return GatewayError("provider_unavailable", "Model provider returned an error.")
    if isinstance(exc, httpx.TimeoutException):
        return GatewayError("provider_timeout", "Model provider timed out after retries.")
    if isinstance(exc, (httpx.TransportError, httpx.HTTPStatusError)):
        return GatewayError("provider_unavailable", "Model provider is unavailable.")
    return GatewayError("agent_execution_failed", f"Agent run failed: {type(exc).__name__}")


async def _fork_config(
    agent: Any, config: dict[str, Any], user_content: str
) -> dict[str, Any] | None:
    """Checkpoint from which the model can safely re-run this user turn.

    Time-travel target: the newest snapshot whose pending node is the model
    and whose messages already contain the turn. Continuing from there
    re-runs the turn without duplicating it and without replaying any
    pending tool call (spec sections 16.5 and 23).
    """
    history = [snap async for snap in agent.aget_state_history(config, limit=200)]
    for snapshot in history:  # newest first
        next_nodes = tuple(snapshot.next or ())
        values = snapshot.values or {}
        contents = [
            message_text(m)
            for m in values.get("messages", [])
            if getattr(m, "type", "") == "human"
        ]
        if next_nodes == ("model",) and user_content in contents:
            return dict(snapshot.config)
    return None


def _run_log_fields(
    run_id: str, identity: RequestIdentity, settings: Settings, *, utility: bool
) -> dict[str, Any]:
    return {
        "event": "run_started",
        "run_id": run_id,
        "user_id_hash": _user_hash(identity.user_id),
        "chat_id": identity.chat_id,
        "thread_id": identity.thread_id,
        "provider": settings.model_provider,
        "model": settings.model_name,
        "identity_source": identity.source,
        "user_message_id": identity.user_message_id,
        "utility": utility,
    }


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    settings: SettingsDep,
) -> Any:
    if body.model != settings.assistant_model_id:
        raise GatewayError("unsupported_model", f"Unknown model {body.model!r}")

    # Header-name receipt diagnostics (names only -- values are never logged).
    # Operational evidence for the Open WebUI header-lineage verification.
    logger.info(
        "received_header_names",
        extra={
            "event": "received_header_names",
            "names": sorted(k.lower() for k in request.headers.keys()),
        },
    )

    identity = extract_identity(request.headers, is_production=settings.is_production)
    run_id = uuid.uuid4().hex
    started = time.monotonic()
    ledger = UsageLedger()
    timeline = RunTimeline(run_id)
    request.app.state.last_run_timeline = timeline
    request.app.state.last_run_ledger = ledger

    # WP4: claim this turn durably BEFORE any external work. Same
    # user-message id + same content = duplicate delivery -> observe the
    # existing run; same id + different content = identity conflict.
    run_store = getattr(request.app.state, "run_store", None)
    claimed = False
    if identity.user_message_id and run_store is not None and not identity.is_utility:
        request_digest = hashlib.sha256(
            f"{body.model}:{_content_digest(body.messages)}".encode()
        ).hexdigest()
        claim = await run_store.claim(
            user_id=identity.user_id,
            chat_id=identity.chat_id,
            user_message_id=identity.user_message_id,
            request_digest=request_digest,
            run_id=run_id,
        )
        if not claim.owned:
            logger.info(
                "run_duplicate_rejected",
                extra={
                    "event": "run_duplicate_rejected",
                    "run_id": run_id,
                    "claim_status": claim.status,
                    "reason": claim.reason,
                },
            )
            if claim.reason == "identity_conflict":
                raise GatewayError(
                    "missing_chat_identity",
                    "Same user-message id delivered with different content.",
                )
            duplicate = _duplicate_response(settings, claim, claim.run_id, started)
            if body.stream:
                async def stream_duplicate() -> AsyncIterator[str]:
                    chunk = ChatCompletionChunk(
                        id=duplicate.id,
                        created=duplicate.created,
                        model=duplicate.model,
                        choices=[ChatCompletionChunkChoice(delta=DeltaMessage(
                            role="assistant", content=str(duplicate.choices[0].message.content)
                        ))],
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"
                    chunk.choices[0].delta = DeltaMessage()
                    chunk.choices[0].finish_reason = "stop"
                    yield f"data: {chunk.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"

                return StreamingResponse(stream_duplicate(), media_type="text/event-stream")
            return duplicate
        claimed = True

    timeline.mark("run_started")
    logger.info(
        "run_started",
        extra=_run_log_fields(run_id, identity, settings, utility=identity.is_utility),
    )

    try:
        if identity.is_utility:
            response: ChatCompletionResponse | StreamingResponse = await _run_utility(
                body, request, settings, run_id, ledger
            )
            fast_terminal = "completed"
        else:
            # The action ledger exists only for claimed runs: rows are
            # FK-bound to the registry entry created by the claim.
            action_ledger = (
                RunActionLedger(run_store, run_id=run_id)
                if claimed and run_store is not None
                else None
            )
            recipe_result = await _try_recipe_route(
                body, request, settings, identity, run_id, action_ledger
            )
            if recipe_result is None and settings.compact_planner_enabled:
                # WP6: natural phrasing of a supported task gets ONE compact
                # same-model decision before the full agent loop. Results
                # execute locally; no obligatory second model call. Flag
                # gives staged rollout and rollback (WP8).
                recipe_result = await _try_planner_route(
                    body, request, settings, identity, run_id, action_ledger, ledger
                )
            if recipe_result is not None:
                # Fast paths report their own terminal truth (P2-5b): a
                # failed or cancelled recipe is never recorded completed.
                recipe_response, fast_status = recipe_result
                # Fast paths honor the request's stream contract (P1-4):
                # JSON for stream=false, SSE with [DONE] for stream=true.
                if body.stream and not isinstance(recipe_response, StreamingResponse):
                    response = _stream_recipe_response(
                        recipe_response,
                        run_store=getattr(request.app.state, "run_store", None),
                        run_id=run_id,
                        timeline=timeline,
                        terminal_status=fast_status,
                    )
                else:
                    response = recipe_response
                    fast_terminal = fast_status
            else:
                response = await _run_agent_turn(
                    body, request, settings, identity, run_id, ledger, timeline,
                    action_ledger=action_ledger,
                )
                fast_terminal = "completed"
    except GatewayError as exc:
        if claimed and run_store is not None:
            await run_store.finish(run_id, "failed")
        timeline.mark_terminal(metadata={"status": "error", "error_code": exc.code})
        logger.warning(
            "run_finished",
            extra={
                "event": "run_finished",
                "run_id": run_id,
                "status": "error",
                "error_code": exc.code,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        raise
    except BaseException as exc:
        if claimed and run_store is not None:
            await run_store.finish(
                run_id, "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            )
        raise
    # NOTE: for streaming responses the terminal event is marked by the
    # generator's finally-clause (master plan F06): returning the
    # StreamingResponse object is not completion.

    if not isinstance(response, StreamingResponse):
        if claimed and run_store is not None:
            await run_store.finish(run_id, fast_terminal)
        timeline.mark_terminal(metadata={"status": "ok"})
        logger.info(
            "run_finished",
            extra={
                "event": "run_finished",
                "run_id": run_id,
                "status": "ok",
                "utility": identity.is_utility,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
    return response


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str, request: Request) -> dict[str, str]:
    """Local stop: mark the run cancelled; checked before every new action.

    No model round trip (master plan 7.5). Cancellation is not undo: any
    already-dispatched effect is preserved and reported by the run.
    """
    manager = getattr(request.app.state, "desktop_sessions", None)
    if manager is None or not manager.cancel(run_id):
        raise GatewayError("run_not_found", f"No active run {run_id!r}")
    logger.info(
        "run_stop_requested",
        extra={"event": "run_stop", "run_id": run_id},
    )
    return {"status": "cancelling", "run_id": run_id}


async def _persist_recipe_turn(
    request: Request,
    identity: RequestIdentity,
    user_text: str,
    assistant_text: str,
) -> None:
    """Record a fast-path exchange on the agent's thread (review P1-3).

    Without this, the recipe turn is invisible to later agent turns: the
    checkpoint holds zero messages and general-agent fallback cannot
    remember what was just done (violates the memory contract F04).
    Best-effort: a persistence failure is logged, never raised -- the
    user's answer must not depend on memory writes.
    """
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return
    config: dict[str, Any] = {"configurable": {"thread_id": identity.thread_id}}
    try:
        await agent.aupdate_state(
            config,
            {"messages": [HumanMessage(content=user_text), AIMessage(content=assistant_text)]},
        )
        logger.info(
            "recipe_turn_persisted",
            extra={"event": "recipe_turn_persisted", "run_id": identity.chat_id},
        )
    except Exception:  # noqa: BLE001 -- memory must never break the run
        logger.exception(
            "recipe_turn_persist_failed",
            extra={"event": "recipe_turn_persist_failed", "run_id": identity.chat_id},
        )


async def _try_recipe_route(
    body: ChatCompletionRequest,
    request: Request,
    settings: SettingsDep,
    identity: RequestIdentity,
    run_id: str,
    action_ledger: RunActionLedger | None,
) -> Any:
    """Exact local command -> recipe execution with ZERO model calls.

    Returns a ChatCompletionResponse when a recipe matched and executed,
    or None to fall through to the agent path. Failure inside a matched
    recipe is rendered honestly (never a guessed success); router
    non-matches silently fall back. Every native mutation counts against
    the same per-run budget and writes the same action-ledger rows as an
    agent-driven action.
    """
    from assistant.runtime.recipe_errors import RecipeFailure
    from assistant.runtime.recipe_executor import RecipeExecutor
    from assistant.runtime.recipe_result import render_result
    from assistant.runtime.recipes import execute_recipe
    from assistant.runtime.router import match_local_command

    # The router consumes the normalized user text (same normalization the
    # agent path uses); raw pydantic ChatMessages yield no user content.
    incoming = normalize_history(body.messages)
    last = last_user_content(incoming)
    if not last:
        return None
    request_obj = match_local_command(str(last), approved_context={})
    if request_obj is None:
        return None

    logger.info(
        "recipe_route_matched",
        extra={
            "event": "recipe_route_matched",
            "run_id": run_id,
            "recipe_id": request_obj.recipe_id,
        },
    )
    tools_by_name = dict(getattr(request.app.state, "cua_tools_by_name", {}) or {})
    budget = RunBudget()
    desktop_manager = getattr(request.app.state, "desktop_sessions", None)
    # The executor writes its own ledger rows (planned -> confirmed/failed/
    # unknown) against the claimed run; the policy ContextVar ledger stays
    # out of the recipe path to avoid double-counting raw-tool dispatch.
    executor = RecipeExecutor(
        cua_tools_by_name=tools_by_name,
        action_ledger=action_ledger,
        run_id=run_id,
        budget=budget,
        run=None,
    )
    try:
        async with _open_desktop_run(desktop_manager, run_id) as run:
            executor._run = run  # noqa: SLF001 -- executor is gateway-owned here
            result = await execute_recipe(request_obj, executor)
    except RecipeFailure as exc:
        result = {
            "recipe_id": request_obj.recipe_id,
            "ok": False,
            "reason": str(exc),
            "app_id": str(getattr(request_obj.arguments, "get", lambda k: "")("app_id") or ""),
            "query": str(getattr(request_obj.arguments, "get", lambda k: "")("query") or ""),
        }
    except DesktopRunCancelled:
        text = "[Run cancelled by user; the command was not completed.]"
        return ChatCompletionResponse(
            id=f"chatcmpl-{run_id}",
            created=int(time.time()),
            model=settings.assistant_model_id,
            choices=[ChatCompletionChoice(message=ChatMessage(role="assistant", content=text))],
            usage=_usage_from_ledger(UsageLedger()),
        ), "cancelled"
    text = render_result(result)
    # Terminal status is the recipe's own truth: a verified result is
    # completed; an honest failure is failed (P2-5b) -- never completed.
    # The exchange is persisted for later agent turns (P1-3, best-effort).
    await _persist_recipe_turn(request, identity, str(last), text)
    return ChatCompletionResponse(
        id=f"chatcmpl-{run_id}",
        created=int(time.time()),
        model=settings.assistant_model_id,
        choices=[ChatCompletionChoice(message=ChatMessage(role="assistant", content=text))],
        usage=UsageStats(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    ), ("completed" if result.get("ok") is True else "failed")


async def _try_planner_route(
    body: ChatCompletionRequest,
    request: Request,
    settings: SettingsDep,
    identity: RequestIdentity,
    run_id: str,
    action_ledger: RunActionLedger | None,
    ledger: UsageLedger,
) -> Any:
    """WP6: one compact same-model decision for natural phrasing.

    The planner chooses an enumerated recipe; validation is fail-closed
    (never a partially streamed argument). A recipe plan executes locally
    with zero FURTHER model calls; clarification/unsupported fall through
    to the general agent unchanged. Same budget and ledger as the
    exact-match route.
    """
    from assistant.runtime.planner import plan_supported_task
    from assistant.runtime.recipe_errors import RecipeFailure
    from assistant.runtime.recipe_executor import RecipeExecutor
    from assistant.runtime.recipe_result import render_result
    from assistant.runtime.recipes import execute_recipe

    incoming = normalize_history(body.messages)
    last = last_user_content(incoming)
    if not last:
        return None
    model = getattr(request.app.state, "utility_model", None)
    if model is None:
        return None
    try:
        accounted_model = model.with_config(
            callbacks=[LedgerCallbackHandler(ledger, prefix=f"{run_id}:planner")]
        )
        plan = await plan_supported_task(str(last), None, accounted_model)
    except Exception as exc:  # noqa: BLE001 -- planner failure falls back, never blocks
        logger.warning(
            "planner_route_failed",
            extra={"event": "planner_route_failed", "exc_type": type(exc).__name__},
        )
        return None
    if not isinstance(plan, RecipeRequest):
        # NeedsClarification / UnsupportedTask: the general agent owns them
        # (gate: constraints and unclear questions are not discarded).
        logger.info(
            "planner_route_fallback",
            extra={
                "event": "planner_route_fallback",
                "decision": type(plan).__name__,
                "reason": getattr(plan, "reason", "") or getattr(plan, "question", ""),
            },
        )
        return None

    tools_by_name = dict(getattr(request.app.state, "cua_tools_by_name", {}) or {})
    budget = RunBudget()
    desktop_manager = getattr(request.app.state, "desktop_sessions", None)
    executor = RecipeExecutor(
        cua_tools_by_name=tools_by_name,
        action_ledger=action_ledger,
        run_id=run_id,
        budget=budget,
        run=None,
    )
    try:
        async with _open_desktop_run(desktop_manager, run_id) as run:
            executor._run = run  # noqa: SLF001 -- executor is gateway-owned here
            result = await execute_recipe(plan, executor)
    except RecipeFailure as exc:
        result = {
            "recipe_id": plan.recipe_id,
            "ok": False,
            "reason": str(exc),
            "app_id": str(plan.arguments.get("app_id", "")),
            "query": str(plan.arguments.get("query", "")),
        }
    except DesktopRunCancelled:
        text = "[Run cancelled by user; the command was not completed.]"
        return ChatCompletionResponse(
            id=f"chatcmpl-{run_id}",
            created=int(time.time()),
            model=settings.assistant_model_id,
            choices=[ChatCompletionChoice(message=ChatMessage(role="assistant", content=text))],
            usage=_usage_from_ledger(UsageLedger()),
        ), "cancelled"
    text = render_result(result)
    # Persist the user's exchange, not the internal plan. Like exact
    # recipes, honest failures must remain visible to later agent turns.
    await _persist_recipe_turn(request, identity, str(last), text)
    # Same terminal truth as the exact-match route (P2-5b).
    return ChatCompletionResponse(
        id=f"chatcmpl-{run_id}",
        created=int(time.time()),
        model=settings.assistant_model_id,
        choices=[ChatCompletionChoice(message=ChatMessage(role="assistant", content=text))],
        usage=_usage_from_ledger(ledger),
    ), ("completed" if result.get("ok") is True else "failed")


async def _run_agent_turn(
    body: ChatCompletionRequest,
    request: Request,
    settings: SettingsDep,
    identity: RequestIdentity,
    run_id: str,
    ledger: UsageLedger,
    timeline: RunTimeline,
    action_ledger: RunActionLedger | None = None,
) -> Any:
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise GatewayError("agent_execution_failed", "Agent is not initialized")

    usage_handler = LedgerCallbackHandler(ledger, prefix=run_id)
    config: dict[str, Any] = {
        "configurable": {"thread_id": identity.thread_id},
        "callbacks": [usage_handler],
    }
    snapshot = await agent.aget_state(config)
    persisted: list = []
    if snapshot is not None and snapshot.values:
        persisted = list(snapshot.values.get("messages", []))
    incoming = normalize_history(body.messages)
    decision = decide_turn(persisted, incoming)

    context = AgentContext(
        user_id=identity.user_id,
        chat_id=identity.chat_id,
        assistant_id=settings.assistant_id,
    )
    budget = RunBudget()
    desktop_manager = getattr(request.app.state, "desktop_sessions", None)
    artifact_dir = str(Path(settings.cua_artifact_dir).resolve())

    run_config: dict[str, Any] = dict(config)
    run_config.setdefault("callbacks", [usage_handler])
    invoke_input: dict[str, Any] | None
    if decision.mode in ("initialize", "new_turn"):
        invoke_input = {"messages": decision.messages}
    else:
        user_content = (
            str(decision.messages[0].content)
            if decision.messages
            else str(last_user_content(persisted) or "")
        )
        replay = await _fork_config(agent, config, user_content) if user_content else None
        if replay is not None:
            # Time-travel: continue the model node; the turn is not re-added.
            run_config = replay
            invoke_input = None
        else:
            # No safe replay point: fall back to a plain replay from the end.
            run_config = config
            invoke_input = {"messages": decision.messages or [HumanMessage(content=user_content)]}

    completion_id = f"chatcmpl-{run_id}"

    if body.stream:
        return StreamingResponse(
            sse_agent_stream(
                agent,
                run_config,
                invoke_input,
                context,
                model_id=settings.assistant_model_id,
                completion_id=completion_id,
                run_id=run_id,
                ledger=ledger,
                timeline=timeline,
                desktop_manager=desktop_manager,
                budget=budget,
                artifact_dir=artifact_dir,
                status_events_enabled=settings.status_events_enabled,
                status_quiet_seconds=settings.status_quiet_seconds,
                run_store=getattr(request.app.state, "run_store", None),
                action_ledger=action_ledger,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming: the request handler owns the run scope and closes the
    # desktop session on every terminal path (result, error, timeout).
    async with _open_desktop_run(desktop_manager, run_id) as run:
        async with cua_run_scope(
            budget=budget, run=run, artifact_dir=artifact_dir, ledger=action_ledger
        ):
            try:
                async with asyncio.timeout(MAX_RUN_WALL_CLOCK_SECONDS):
                    result = await agent.ainvoke(invoke_input, run_config, context=context)
            except TimeoutError:
                timeline.mark_terminal(
                    metadata={"status": "wall_clock_exceeded", "cua_mutating_actions": budget.used}
                )
                logger.warning(
                    "run_finished",
                    extra={
                        "event": "run_finished",
                        "run_id": run_id,
                        "status": "wall_clock_exceeded",
                        "cua_mutating_actions": budget.used,
                    },
                )
                raise GatewayError(
                    "agent_execution_failed",
                    "Run exceeded the wall-clock ceiling; partial state is preserved.",
                ) from None
            except GatewayError as exc:
                timeline.mark_terminal(metadata={"status": "error", "error_code": exc.code})
                raise
            except Exception as exc:
                timeline.mark_terminal(metadata={"status": "error"})
                logger.exception(
                    "agent_turn_failed",
                    extra={
                        "event": "agent_turn_failed",
                        "run_id": run_id,
                        "exc_type": type(exc).__name__,
                    },
                )
                raise _provider_error(exc) from exc

            text = _final_assistant_text(result)
            return ChatCompletionResponse(
                id=completion_id,
                created=int(time.time()),
                model=settings.assistant_model_id,
                choices=[
                    ChatCompletionChoice(message=ChatMessage(role="assistant", content=text))
                ],
                usage=_usage_from_ledger(ledger),
            )


@asynccontextmanager
async def _open_desktop_run(manager: Any, run_id: str) -> AsyncIterator[Any]:
    """Open the run-scoped desktop handle when a manager is installed.

    Yields ``None`` when desktop sessions are absent (tests, disabled
    builds): the run scope still binds, just without a desktop handle.
    """
    if manager is None:
        yield None
        return
    async with manager.open(run_id) as run:
        yield run


def _usage_from_ledger(ledger: UsageLedger) -> UsageStats:
    """Run-aggregated usage from the provider boundary (master plan F05)."""
    totals = ledger.snapshot()
    input_tokens = int(totals.get("input_tokens") or 0)
    output_tokens = int(totals.get("output_tokens") or 0)
    return UsageStats(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


async def _run_utility(
    body: ChatCompletionRequest,
    request: Request,
    settings: SettingsDep,
    run_id: str,
    ledger: UsageLedger,
) -> ChatCompletionResponse:
    """Utility tasks get a plain model response: no agent, no CUA (spec 15.3)."""
    model = getattr(request.app.state, "utility_model", None)
    if model is None:
        model = build_chat_model(settings)
    messages = normalize_history(body.messages)
    if not messages:
        raise GatewayError("agent_execution_failed", "Empty messages for utility request")
    usage_handler = LedgerCallbackHandler(ledger, prefix=f"{run_id}-util")

    try:
        async with asyncio.timeout(settings.model_timeout_seconds + 5):
            result = await model.ainvoke(
                messages, config={"callbacks": [usage_handler]}  # type: ignore[arg-type]
            )
    except TimeoutError as exc:
        raise GatewayError("provider_timeout", "Model provider timed out.") from exc
    except Exception as exc:
        raise _provider_error(exc) from exc

    totals = ledger.snapshot()
    input_tokens = int(totals.get("input_tokens") or 0)
    output_tokens = int(totals.get("output_tokens") or 0)
    content = str(result.content or "")
    return ChatCompletionResponse(
        id=f"chatcmpl-{run_id}",
        created=int(time.time()),
        model=settings.assistant_model_id,
        choices=[
            ChatCompletionChoice(message=ChatMessage(role="assistant", content=content))
        ],
        usage=UsageStats(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )
