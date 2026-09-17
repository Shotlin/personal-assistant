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
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from assistant.agent.context import (
    MAX_RUN_WALL_CLOCK_SECONDS,
    AgentContext,
    RunBudget,
)
from assistant.api.auth import SettingsDep, require_gateway_key
from assistant.api.identity import RequestIdentity, extract_identity
from assistant.api.schemas import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    GatewayError,
    UsageStats,
)
from assistant.api.streaming import sse_agent_stream
from assistant.api.turns import decide_turn, last_user_content, message_text, normalize_history
from assistant.models import build_chat_model
from assistant.settings import Settings
from assistant.tools.policy import cua_run_budget

logger = logging.getLogger("assistant.api.chat")

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])


def _user_hash(user_id: str) -> str:
    return f"sha256:{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:12]}"


def _final_assistant_text(result: dict[str, Any]) -> str:
    from assistant.api.turns import message_text

    for message in reversed(result.get("messages", [])):
        if getattr(message, "type", "") != "ai":
            continue
        content = message_text(message)
        if content.strip():
            return content
    return ""


def _usage_from(result: dict[str, Any]) -> UsageStats:
    for message in reversed(result.get("messages", [])):
        usage = getattr(message, "usage_metadata", None)
        if usage:
            return UsageStats(
                prompt_tokens=int(usage.get("input_tokens") or 0),
                completion_tokens=int(usage.get("output_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            )
    return UsageStats()


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

    identity = extract_identity(request.headers, is_production=settings.is_production)
    run_id = uuid.uuid4().hex
    started = time.monotonic()
    logger.info(
        "run_started",
        extra=_run_log_fields(run_id, identity, settings, utility=identity.is_utility),
    )

    try:
        if identity.is_utility:
            response = await _run_utility(body, request, settings, run_id)
        else:
            response = await _run_agent_turn(body, request, settings, identity, run_id)
    except GatewayError as exc:
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


async def _run_agent_turn(
    body: ChatCompletionRequest,
    request: Request,
    settings: SettingsDep,
    identity: RequestIdentity,
    run_id: str,
) -> Any:
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise GatewayError("agent_execution_failed", "Agent is not initialized")

    config = {"configurable": {"thread_id": identity.thread_id}}
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
    cua_run_budget.set(budget)

    run_config: dict[str, Any] = dict(config)
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
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        async with asyncio.timeout(MAX_RUN_WALL_CLOCK_SECONDS):
            result = await agent.ainvoke(invoke_input, run_config, context=context)
    except TimeoutError:
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
    except GatewayError:
        raise
    except Exception as exc:
        raise _provider_error(exc) from exc

    text = _final_assistant_text(result)
    return ChatCompletionResponse(
        id=completion_id,
        created=int(time.time()),
        model=settings.assistant_model_id,
        choices=[
            ChatCompletionChoice(message=ChatMessage(role="assistant", content=text))
        ],
        usage=_usage_from(result),
    )


async def _run_utility(
    body: ChatCompletionRequest,
    request: Request,
    settings: SettingsDep,
    run_id: str,
) -> ChatCompletionResponse:
    """Utility tasks get a plain model response: no agent, no CUA (spec 15.3)."""
    model = getattr(request.app.state, "utility_model", None)
    if model is None:
        model = build_chat_model(settings)
    messages = normalize_history(body.messages)
    if not messages:
        raise GatewayError("agent_execution_failed", "Empty messages for utility request")

    try:
        async with asyncio.timeout(settings.model_timeout_seconds + 5):
            result = await model.ainvoke(messages)
    except TimeoutError as exc:
        raise GatewayError("provider_timeout", "Model provider timed out.") from exc
    except Exception as exc:
        raise _provider_error(exc) from exc

    usage_meta = getattr(result, "usage_metadata", None) or {}
    return ChatCompletionResponse(
        id=f"chatcmpl-{run_id}",
        created=int(time.time()),
        model=settings.assistant_model_id,
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(role="assistant", content=str(result.content or ""))
            )
        ],
        usage=UsageStats(
            prompt_tokens=int(usage_meta.get("input_tokens") or 0),
            completion_tokens=int(usage_meta.get("output_tokens") or 0),
            total_tokens=int(usage_meta.get("total_tokens") or 0),
        ),
    )
