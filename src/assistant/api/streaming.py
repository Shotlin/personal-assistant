"""SSE streaming translation (spec section 16.6).

Only user-visible assistant text is emitted: model-node text chunks with
no tool-call payload. Tool arguments, tool results (screenshots), hidden
system prompts, and reasoning from other nodes are never streamed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import nullcontext
from typing import Any

from langchain_core.messages import AIMessageChunk  # noqa: F401  (chunk type documented)

from assistant.agent.context import MAX_RUN_WALL_CLOCK_SECONDS
from assistant.api.schemas import ChatCompletionChunk, ChatCompletionChunkChoice, DeltaMessage

logger = logging.getLogger("assistant.api.streaming")

_PARTIAL_STATE_NOTICE = (
    "\n\n[Stopped: run time ceiling reached. Reporting partial progress rather than "
    "continuing.]"
)

_STOPPED_NOTICE = (
    "\n\n[Stopped by user; no further desktop actions were taken after the stop.]"
)
_STATUS_WORKING = "[working] "
_STATUS_WAITING = "[waiting for the model…]"


def _sse(payload: ChatCompletionChunk) -> str:
    return f"data: {payload.model_dump_json()}\n\n"


def _base_chunk(completion_id: str, model: str) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id=completion_id,
        created=int(time.time()),
        model=model,
        choices=[ChatCompletionChunkChoice(delta=DeltaMessage())],
    )


def _content_chunk(completion_id: str, model: str, text: str) -> ChatCompletionChunk:
    payload = _base_chunk(completion_id, model)
    payload.choices[0].delta = DeltaMessage(content=text)
    return payload


def visible_text(chunk: Any) -> str:
    """Extract user-visible text from a model-node message chunk."""
    msg_type = getattr(chunk, "type", None)
    if msg_type is not None and msg_type not in {"ai", "AIMessageChunk"}:
        return ""
    if getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None):
        return ""
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict) and "text" in part
        )
    return ""


async def sse_agent_stream(
    agent: Any,
    run_config: dict[str, Any],
    invoke_input: dict[str, Any] | None,
    context: Any,
    *,
    model_id: str,
    completion_id: str,
    wall_clock_seconds: float = MAX_RUN_WALL_CLOCK_SECONDS,
    run_id: str | None = None,
    ledger: Any | None = None,
    timeline: Any | None = None,
    desktop_manager: Any | None = None,
    budget: Any | None = None,
    artifact_dir: str = "",
    status_events_enabled: bool = True,
    status_quiet_seconds: float = 6.0,
    run_store: Any | None = None,
    action_ledger: Any | None = None,
) -> AsyncIterator[str]:
    """Yield OpenAI-compatible SSE chunks for one agent run, ending with [DONE].

    The generator owns the run scope (master plan 7.2): it opens the
    run-scoped desktop handle (lazy -- pure chat never touches the
    driver), sets the run-scoped ContextVars in its own execution scope,
    and closes the session in ``finally`` on every terminal path
    (completion, timeout, error, cancellation, client disconnect).
    The terminal timeline event is marked after real completion/cleanup
    (master plan F06), not when the response object is created.
    """
    from assistant.tools.policy import cua_run_scope

    first = _base_chunk(completion_id, model_id)
    first.choices[0].delta = DeltaMessage(role="assistant")
    yield _sse(first)
    if status_events_enabled:
        yield _sse(_content_chunk(completion_id, model_id, _STATUS_WORKING))

    manager_cm = (
        desktop_manager.open(run_id)
        if desktop_manager is not None and run_id
        else nullcontext(None)
    )
    emitted_any = False
    status = "ok"
    async with manager_cm as run:  # type: ignore[attr-defined]
        try:
            async with cua_run_scope(
                budget=budget, run=run, artifact_dir=artifact_dir, ledger=action_ledger
            ):
                if run is not None and run.cancelled:
                    status = "cancelled"
                    yield _sse(_content_chunk(completion_id, model_id, _STOPPED_NOTICE))
                else:
                    waiting_emitted = False
                    quiet_since_ns = time.monotonic_ns()
                    async with asyncio.timeout(wall_clock_seconds):
                        async for chunk, metadata in agent.astream(
                            invoke_input,
                            run_config,
                            context=context,
                            stream_mode="messages",
                        ):
                            node = (metadata or {}).get("langgraph_node")
                            if node != "model":
                                continue
                            text = visible_text(chunk)
                            if not text:
                                continue
                            # Check the local stop flag BEFORE yielding: output
                            # produced after a stop must never reach the client.
                            # Break (not return) so finalization still runs.
                            if run is not None and run.cancelled:
                                status = "cancelled"
                                yield _sse(
                                    _content_chunk(completion_id, model_id, _STOPPED_NOTICE)
                                )
                                break
                            if status_events_enabled and waiting_emitted is False:
                                elapsed = (time.monotonic_ns() - quiet_since_ns) / 1e9
                                if elapsed >= status_quiet_seconds:
                                    yield _sse(
                                        _content_chunk(
                                            completion_id, model_id, _STATUS_WAITING
                                        )
                                    )
                                    waiting_emitted = True
                            payload = _base_chunk(completion_id, model_id)
                            payload.choices[0].delta = DeltaMessage(content=text)
                            yield _sse(payload)
                            emitted_any = True
                            quiet_since_ns = time.monotonic_ns()
        except (asyncio.CancelledError, GeneratorExit):
            status = "cancelled"
            raise
        except TimeoutError:
            status = "wall_clock_exceeded"
            logger.warning(
                "agent_run_wall_clock_exceeded",
                extra={"event": "agent_run_wall_clock_exceeded", "completion_id": completion_id},
            )
            payload = _base_chunk(completion_id, model_id)
            payload.choices[0].delta = DeltaMessage(content=_PARTIAL_STATE_NOTICE)
            yield _sse(payload)
        except Exception:
            status = "error"
            logger.exception(
                "agent_run_stream_failed",
                extra={"event": "agent_run_stream_failed", "completion_id": completion_id},
            )
            payload = _base_chunk(completion_id, model_id)
            payload.choices[0].delta = DeltaMessage(
                content="\n\n[The assistant run failed; no further output. Check gateway logs.]"
            )
            yield _sse(payload)
        finally:
            # Truthful terminal event: after the generator really terminates
            # (completion, timeout, error, or client disconnect). The desktop
            # session is ended by manager.open()'s own cleanup below.
            if run_store is not None and run_id:
                await run_store.finish(
                    run_id,
                    "completed" if status == "ok" else (
                        "cancelled" if status == "cancelled" else "failed"
                    ),
                )
            if timeline is not None:
                timeline.mark_terminal(
                    metadata={"status": status, "emitted_any": emitted_any}
                )
            if ledger is not None:
                totals = ledger.snapshot()
                logger.info(
                    "run_usage",
                    extra={
                        "event": "run_usage",
                        "run_id": run_id or "",
                        "model_calls": totals.get("calls"),
                        "input_tokens": totals.get("input_tokens"),
                        "output_tokens": totals.get("output_tokens"),
                        "unknown_output_calls": totals.get("unknown_output_calls"),
                    },
                )

    final = _base_chunk(completion_id, model_id)
    final.choices[0].finish_reason = "stop"
    yield _sse(final)
    yield "data: [DONE]\n\n"
    _ = emitted_any
