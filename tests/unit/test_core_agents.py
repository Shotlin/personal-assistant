"""The sani-core executor entry, against injected fakes.

What is covered here is the contract the sidecar runs on: token and progress
events from a streamed model, cooperative cancellation, the registry that names
the one executor twice, and the status payload the desktop UI reads. Nothing
touches the driver, the network, or a model provider.
"""

import asyncio
from typing import Any

import pytest

from assistant.core.agents import (
    DeepAgentEntry,
    LabeledAgentEntry,
    build_default_registry,
    build_status_provider,
)
from assistant.core.registry import AgentDescriptor
from assistant.settings import Settings


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "development",
        "model_provider": "generic_openai_compatible",
        "model_base_url": "http://127.0.0.1:1",
        "model_api_key": "k",
        "model_name": "m",
        "openrouter_api_key": "",
    }
    base.update(overrides)
    return Settings(**base)


class _EventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, data: dict[str, Any]) -> None:
        self.events.append((kind, data))


def _chunk(message_id: str, content: str = "", tool_names: list[str] | None = None) -> Any:
    """A real AIMessageChunk: `.type` is 'AIMessageChunk', not 'ai'."""
    from langchain_core.messages import AIMessageChunk

    return AIMessageChunk(
        content=content,
        id=message_id,
        tool_call_chunks=[
            {"name": name, "args": None, "id": f"call-{name}", "index": 0}
            for name in (tool_names or [])
        ],
    )


class _StreamingDeepAgent:
    """Yields scripted (message chunk, metadata) pairs like stream_mode=messages."""

    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self.seen_config: dict[str, Any] = {}

    async def astream(self, messages, config, context=None, stream_mode=None):  # noqa: ANN001
        self.seen_config = dict(config)
        assert stream_mode == "messages"
        for chunk in self._script:
            yield chunk, {"langgraph_node": "model"}


async def test_deep_entry_streams_tokens_and_returns_the_answer() -> None:
    agent = _StreamingDeepAgent(
        [
            _chunk("m1", "Explaining"),
            _chunk("m1", " the design."),
            _chunk("m2", tool_names=["launch_app"]),
            _chunk("m3", "Chrome is open."),
        ]
    )

    async def builder() -> _StreamingDeepAgent:
        return agent

    entry = DeepAgentEntry(_settings(), agent_builder=builder)
    log = _EventLog()
    result = await entry.run(
        "What is 2 + 2?", thread_id="conv-9", on_event=log, cancel_check=lambda: False
    )

    assert result["status"] == "done"
    assert result["response"] == "Chrome is open."
    # The host's conversation id becomes the agent thread, so turn two resumes
    # turn one instead of starting a fresh conversation every time.
    assert result["thread_id"] == "sani:conv-9"
    assert agent.seen_config["configurable"]["thread_id"] == "sani:conv-9"
    text = "".join(data["text"] for kind, data in log.events if kind == "agent.token")
    assert text == "Explaining the design.Chrome is open."
    # The tool call is surfaced as an activity line, never as answer text.
    assert ("agent.progress", {"message": "Using launch_app"}) in log.events
    assert not any(kind == "started" for kind, _ in log.events), "the app loop owns that frame"


async def test_deep_entry_is_cooperative_about_cancellation() -> None:
    class _NeverEnding:
        async def astream(self, messages, config, context=None, stream_mode=None):  # noqa: ANN001
            for _ in range(50):
                yield _chunk("m1", "x"), {}
                await asyncio.sleep(0)

    async def builder() -> _NeverEnding:
        return _NeverEnding()

    entry = DeepAgentEntry(_settings(), agent_builder=builder)
    with pytest.raises(asyncio.CancelledError):
        await entry.run(
            "chatter",
            thread_id="",
            on_event=_EventLog(),
            cancel_check=lambda: True,
        )


def test_default_registry_lists_velo_and_deep() -> None:
    registry = build_default_registry(_settings())
    names = [descriptor.id for descriptor in registry.list()]
    assert names == ["deep", "velo"]


def test_velo_is_the_controller_and_deep_is_the_executor() -> None:
    """Velo owns intent and routing; the Deep Agent is the reasoning executor.

    Both entries draw their runtime from one provider, so there is still no
    second transport, checkpointer or agent to drift out of step -- but Velo is
    no longer a mere label: it resolves ordinary commands locally and asks JEV
    when interpretation is genuinely needed.
    """
    from assistant.velo.controller import VeloEntry

    registry = build_default_registry(_settings())
    velo = registry.get("velo")
    assert velo is not None
    assert isinstance(velo, VeloEntry)
    assert velo.descriptor.name == "Velo"
    deep = registry.get("deep")
    assert isinstance(deep, DeepAgentEntry)
    # One shared runtime: the controller hands Route C to the same executor.
    assert velo._deep is deep


async def test_the_label_delegates_the_whole_run_to_the_executor() -> None:
    seen: dict[str, object] = {}

    class Inner:
        async def run(self, text, *, thread_id, on_event, cancel_check):
            seen["text"] = text
            seen["thread_id"] = thread_id
            await on_event("agent.token", {"text": "hi"})
            return {"status": "done", "response": "hi"}

        async def cancel(self):
            seen["cancelled"] = True

    class Log:
        async def __call__(self, kind, payload):
            seen["event"] = (kind, payload)

    entry = LabeledAgentEntry(Inner(), AgentDescriptor(id="velo", name="Velo", capabilities=()))
    result = await entry.run(
        "open chrome", thread_id="c1", on_event=Log(), cancel_check=lambda: False
    )
    await entry.cancel()

    assert result == {"status": "done", "response": "hi"}
    assert seen["text"] == "open chrome" and seen["thread_id"] == "c1"
    assert seen["event"] == ("agent.token", {"text": "hi"})
    assert seen["cancelled"] is True


async def test_status_provider_reports_subsystems(tmp_path: Any) -> None:
    provider = build_status_provider(
        _settings(cua_enabled=False, memory_backend="sqlite", sani_data_dir=str(tmp_path))
    )
    payload = await provider()
    assert payload["driver"]["found"] is False
    assert payload["memory_backend"] == "sqlite"


async def test_deep_entry_stops_a_repeating_loop_fail_closed() -> None:
    script = [_chunk(f"m{i}", "I will find the second video and click it now.") for i in range(6)]
    agent = _StreamingDeepAgent(script)

    async def builder() -> _StreamingDeepAgent:
        return agent

    entry = DeepAgentEntry(_settings(), agent_builder=builder)
    result = await entry.run(
        "play the second video", thread_id="c", on_event=_EventLog(), cancel_check=lambda: False
    )

    assert result["status"] == "blocked"
    assert "repeating" in result["response"]


class _HungDeepAgent:
    async def astream(self, messages, config, context=None, stream_mode=None):  # noqa: ANN001
        await asyncio.sleep(30)
        yield _chunk("m1", "never"), {}


async def test_deep_entry_ends_a_hung_model_request_at_the_hard_limit() -> None:
    agent = _HungDeepAgent()

    async def builder() -> _HungDeepAgent:
        return agent

    entry = DeepAgentEntry(_settings(deep_run_deadline_seconds=-29), agent_builder=builder)
    result = await entry.run(
        "do something", thread_id="c", on_event=_EventLog(), cancel_check=lambda: False
    )
    assert result["status"] == "blocked"
    assert "time limit" in result["response"]


async def test_an_empty_model_answer_becomes_an_explanation_not_a_blank_reply() -> None:
    agent = _StreamingDeepAgent([_chunk("m1", "")])

    async def builder() -> _StreamingDeepAgent:
        return agent

    entry = DeepAgentEntry(_settings(), agent_builder=builder)
    result = await entry.run(
        "a very long request", thread_id="c", on_event=_EventLog(), cancel_check=lambda: False
    )
    assert result["status"] == "blocked"
    assert "didn't get an answer back from the model" in result["response"]
    assert "smaller parts" in result["response"]


async def test_tools_run_but_no_summary_is_reported_as_done_with_a_note() -> None:
    agent = _StreamingDeepAgent([_chunk("m1", tool_names=["launch_app"])])

    async def builder() -> _StreamingDeepAgent:
        return agent

    entry = DeepAgentEntry(_settings(), agent_builder=builder)
    result = await entry.run(
        "open it", thread_id="c", on_event=_EventLog(), cancel_check=lambda: False
    )
    assert result["status"] == "done"
    assert "did not write a summary" in result["response"]
