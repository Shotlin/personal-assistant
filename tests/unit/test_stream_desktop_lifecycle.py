"""Desktop lifecycle through the streaming path (master plan WP3).

The generator owns the run scope: it opens the run-scoped desktop handle,
checks local cancellation between chunks, emits bounded status text, and
guarantees driver cleanup on every exit path. Fakes only; no driver, no
model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from assistant.agent.context import AgentContext
from assistant.api.streaming import sse_agent_stream
from assistant.runtime.session import DesktopSessionManager
from tests.helpers.fake_driver import FakeDriver


class _ScriptedAgent:
    def __init__(self, items: list[tuple[Any, dict[str, str]]]) -> None:
        self._items = items

    async def astream(self, _input: Any, _config: Any, **kwargs: Any) -> AsyncIterator[Any]:
        for item in self._items:
            yield item


def _context() -> AgentContext:
    return AgentContext(user_id="u", chat_id="c")


async def _collect(gen: AsyncIterator[str]) -> str:
    body = ""
    async for piece in gen:
        body += piece
    return body


async def test_pure_chat_stream_never_touches_the_driver() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    agent = _ScriptedAgent([(AIMessage(content="hello"), {"langgraph_node": "model"})])

    body = await _collect(
        sse_agent_stream(
            agent,
            {"configurable": {"thread_id": "t"}},
            {"messages": [HumanMessage("hi")]},
            _context(),
            model_id="m",
            completion_id="chatcmpl-1",
            desktop_manager=manager,
            run_id="run-chat",
        )
    )
    assert "hello" in body or "hi" in body
    assert driver.calls == []


async def test_stream_opens_and_cleans_the_desktop_run_once() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    agent = _ScriptedAgent([(AIMessage(content="done"), {"langgraph_node": "model"})])

    body = await _collect(
        sse_agent_stream(
            agent,
            {"configurable": {"thread_id": "t"}},
            {"messages": [HumanMessage("hi")]},
            _context(),
            model_id="m",
            completion_id="chatcmpl-2",
            desktop_manager=manager,
            run_id="run-desktop",
        )
    )
    assert body.endswith("data: [DONE]\n\n")
    assert driver.calls == []  # never started -> never ended


async def test_stream_emits_status_and_finishes_once_per_chunk() -> None:
    agent = _ScriptedAgent([(AIMessage(content="answer"), {"langgraph_node": "model"})])
    body = await _collect(
        sse_agent_stream(
            agent,
            {"configurable": {"thread_id": "t"}},
            {"messages": [HumanMessage("hi")]},
            _context(),
            model_id="m",
            completion_id="chatcmpl-3",
            status_events_enabled=True,
        )
    )
    assert body.count("data: [DONE]") == 1
    assert body.count('"finish_reason":"stop"') == 1


async def test_disabled_status_events_emit_no_status_text() -> None:
    agent = _ScriptedAgent([(AIMessage(content="answer"), {"langgraph_node": "model"})])
    body = await _collect(
        sse_agent_stream(
            agent,
            {"configurable": {"thread_id": "t"}},
            {"messages": [HumanMessage("hi")]},
            _context(),
            model_id="m",
            completion_id="chatcmpl-4",
            status_events_enabled=False,
        )
    )
    assert "[working]" not in body
    assert "answer" in body


async def test_waiting_for_model_status_appears_after_quiet_period() -> None:
    class _QuietThenAnswer(_ScriptedAgent):
        async def astream(self, _input: Any, _config: Any, **kwargs: Any) -> AsyncIterator[Any]:
            import asyncio

            await asyncio.sleep(0.05)
            yield (AIMessage(content="late answer"), {"langgraph_node": "model"})

    body = await _collect(
        sse_agent_stream(
            _QuietThenAnswer([]),
            {"configurable": {"thread_id": "t"}},
            {"messages": [HumanMessage("hi")]},
            _context(),
            model_id="m",
            completion_id="chatcmpl-5",
            status_events_enabled=True,
            status_quiet_seconds=0.01,
        )
    )
    assert "waiting for the model" in body
    assert body.count("waiting for the model") == 1  # not a polling loop
    assert "late answer" in body


async def test_cancelled_run_yields_stop_notice_without_model_stream() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)

    class _StreamThenStopped:
        """Streams one chunk, then the user's local stop lands, then more."""

        def __init__(self) -> None:
            self.chunks_after_stop = 0

        async def astream(self, _input: Any, _config: Any, **kwargs: Any) -> AsyncIterator[Any]:
            yield (AIMessageChunk(content="partial "), {"langgraph_node": "model"})
            manager.cancel("run-stop")  # user pressed Stop mid-run
            self.chunks_after_stop += 1
            yield (AIMessageChunk(content="continued output"), {"langgraph_node": "model"})

    agent = _StreamThenStopped()
    body = await _collect(
        sse_agent_stream(
            agent,
            {"configurable": {"thread_id": "t"}},
            {"messages": [HumanMessage("hi")]},
            _context(),
            model_id="m",
            completion_id="chatcmpl-6",
            desktop_manager=manager,
            run_id="run-stop",
            status_events_enabled=False,
        )
    )
    assert "partial" in body  # the already-streamed part stays
    assert "continued output" not in body  # nothing streams after the stop
    assert "no further desktop actions" in body
    assert body.endswith("data: [DONE]\n\n")
