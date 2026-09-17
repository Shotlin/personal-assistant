"""Unit tests for SSE streaming translation (spec section 16.6)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from assistant.agent.context import AgentContext
from assistant.api.streaming import sse_agent_stream, visible_text


def test_visible_text_from_ai_message_chunk() -> None:
    chunk = AIMessageChunk(content="hello world")
    assert visible_text(chunk) == "hello world"


def test_visible_text_rejects_tool_call_chunks() -> None:
    chunk = AIMessageChunk(
        content="",
        tool_call_chunks=[{"name": "click", "args": "{}", "id": "1", "index": 0}],
    )
    assert visible_text(chunk) == ""


def test_visible_text_rejects_tool_messages() -> None:
    tool = ToolMessage(content="screenshot-bytes", tool_call_id="x")
    assert visible_text(tool) == ""


def test_visible_text_from_full_ai_message() -> None:
    message = AIMessage(content="final answer")
    assert visible_text(message) == "final answer"


class _SlowAgent:
    """Agent stub whose astream hangs past the wall clock."""

    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def astream(self, _input: Any, _config: Any, **kwargs: Any) -> AsyncIterator[Any]:
        import asyncio

        await asyncio.sleep(self._delay)
        yield (AIMessage(content="too late"), {"langgraph_node": "model"})


async def test_wall_clock_emits_partial_notice_and_done() -> None:
    chunks: list[str] = []
    async for piece in sse_agent_stream(
        _SlowAgent(delay=0.05),
        {"configurable": {"thread_id": "t"}},
        {"messages": [HumanMessage("hi")]},
        AgentContext(user_id="u", chat_id="c"),
        model_id="personal-assistant-v1",
        completion_id="chatcmpl-test",
        wall_clock_seconds=0.01,
    ):
        chunks.append(piece)

    assert chunks[-1] == "data: [DONE]\n\n"
    body = "".join(chunks)
    assert "partial progress" in body


class _ScriptedAgent:
    def __init__(self, items: list[tuple[Any, dict[str, str]]]) -> None:
        self._items = items

    async def astream(self, _input: Any, _config: Any, **kwargs: Any) -> AsyncIterator[Any]:
        for item in self._items:
            yield item


async def test_stream_emits_only_model_node_text() -> None:
    agent = _ScriptedAgent(
        [
            (
                AIMessage(content="", tool_calls=[{"name": "click", "args": {}, "id": "1"}]),
                {"langgraph_node": "model"},
            ),
            (ToolMessage(content="result", tool_call_id="1"), {"langgraph_node": "tools"}),
            (AIMessage(content="all done"), {"langgraph_node": "model"}),
        ]
    )
    chunks: list[str] = []
    async for piece in sse_agent_stream(
        agent,
        {"configurable": {"thread_id": "t"}},
        {"messages": [HumanMessage("hi")]},
        AgentContext(user_id="u", chat_id="c"),
        model_id="personal-assistant-v1",
        completion_id="chatcmpl-test",
    ):
        chunks.append(piece)

    body = "".join(chunks)
    assert "all done" in body
    assert "click" not in body  # tool arguments never streamed
    assert "result" not in body  # tool results never streamed
    assert body.endswith("data: [DONE]\n\n")


@pytest.mark.parametrize("node", ["tools", "SkillsMiddleware.before_agent"])
async def test_stream_skips_non_model_nodes(node: str) -> None:
    agent = _ScriptedAgent([(AIMessage(content="hidden"), {"langgraph_node": node})])
    chunks: list[str] = []
    async for piece in sse_agent_stream(
        agent,
        {"configurable": {"thread_id": "t"}},
        None,
        AgentContext(user_id="u", chat_id="c"),
        model_id="m",
        completion_id="chatcmpl-test",
    ):
        chunks.append(piece)
    assert "hidden" not in "".join(chunks)
