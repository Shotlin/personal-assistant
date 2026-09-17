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
from typing import Any

from langchain_core.messages import AIMessageChunk  # noqa: F401  (chunk type documented)

from assistant.agent.context import MAX_RUN_WALL_CLOCK_SECONDS
from assistant.api.schemas import ChatCompletionChunk, ChatCompletionChunkChoice, DeltaMessage

logger = logging.getLogger("assistant.api.streaming")

_PARTIAL_STATE_NOTICE = (
    "\n\n[Stopped: run time ceiling reached. Reporting partial progress rather than "
    "continuing.]"
)


def _sse(payload: ChatCompletionChunk) -> str:
    return f"data: {payload.model_dump_json()}\n\n"


def _base_chunk(completion_id: str, model: str) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id=completion_id,
        created=int(time.time()),
        model=model,
        choices=[ChatCompletionChunkChoice(delta=DeltaMessage())],
    )


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
) -> AsyncIterator[str]:
    """Yield OpenAI-compatible SSE chunks for one agent run, ending with [DONE]."""
    first = _base_chunk(completion_id, model_id)
    first.choices[0].delta = DeltaMessage(role="assistant")
    yield _sse(first)

    emitted_any = False
    try:
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
                payload = _base_chunk(completion_id, model_id)
                payload.choices[0].delta = DeltaMessage(content=text)
                yield _sse(payload)
                emitted_any = True
    except TimeoutError:
        logger.warning(
            "agent_run_wall_clock_exceeded",
            extra={"event": "agent_run_wall_clock_exceeded", "completion_id": completion_id},
        )
        payload = _base_chunk(completion_id, model_id)
        payload.choices[0].delta = DeltaMessage(content=_PARTIAL_STATE_NOTICE)
        yield _sse(payload)
    except Exception:
        logger.exception(
            "agent_run_stream_failed",
            extra={"event": "agent_run_stream_failed", "completion_id": completion_id},
        )
        payload = _base_chunk(completion_id, model_id)
        payload.choices[0].delta = DeltaMessage(
            content="\n\n[The assistant run failed; no further output. Check gateway logs.]"
        )
        yield _sse(payload)

    final = _base_chunk(completion_id, model_id)
    final.choices[0].finish_reason = "stop"
    yield _sse(final)
    yield "data: [DONE]\n\n"
    _ = emitted_any
