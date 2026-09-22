"""sani-core agent entry tests (Stage G): velo + deep against fakes.

The real CUA/JEV/model stacks are injected; nothing here touches the
driver, the network, or a model provider.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from assistant.core.agents import (
    DeepAgentEntry,
    VeloAgentEntry,
    build_default_registry,
    build_status_provider,
    velo_response,
)
from assistant.settings import Settings
from assistant.velo.agent import VeloAgent
from assistant.velo.types import VeloActionKind, VeloLimits, VeloStatus
from tests.velo.fakes import FakeCua, ScriptedJev, act, done, observation, target


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "development",
        "model_provider": "generic_openai_compatible",
        "model_base_url": "http://127.0.0.1:1",
        "model_api_key": "k",
        "model_name": "m",
        "velo_enabled": False,
        "typesafe_api_key": "",
        "openrouter_api_key": "",
    }
    base.update(overrides)
    return Settings(**base)


class _EventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, data: dict[str, Any]) -> None:
        self.events.append((kind, data))


def _fake_velo_context(fake_cua: FakeCua, jev: ScriptedJev, cancel_log: list[str]) -> Any:
    @contextlib.asynccontextmanager
    async def context(settings: Settings) -> AsyncIterator[tuple[VeloAgent, Any]]:
        agent = VeloAgent(fake_cua, jev, VeloLimits(max_steps=6))
        yield agent, lambda: cancel_log.append("cancelled")

    return context


async def test_velo_entry_runs_and_streams_progress() -> None:
    fake_cua = FakeCua(
        observations=[
            observation(),
            observation(target("t1", "ok"), foreground_app="Google Chrome"),
        ]
    )
    jev = ScriptedJev(act(VeloActionKind.LAUNCH_APP, app_name="com.google.Chrome"), done())
    entry = VeloAgentEntry(_settings(), context_factory=_fake_velo_context(fake_cua, jev, []))
    log = _EventLog()

    result = await entry.run("Open Chrome", thread_id="", on_event=log, cancel_check=lambda: False)

    assert result["status"] is VeloStatus.DONE.value
    assert fake_cua.call_kinds("execute") == [("execute", "LAUNCH_APP:com.google.Chrome")]
    assert [kind for kind, _ in log.events] and all(
        kind == "agent.progress" for kind, _ in log.events
    )
    # Velo reports status/reason, not prose; the transcript needs a line to show.
    assert result["response"]


async def test_velo_response_is_honest_about_a_failed_run() -> None:
    from assistant.velo.types import VeloMetrics, VeloResult

    assert velo_response(VeloResult(status=VeloStatus.DONE, reason="Chrome is open")) == (
        "Chrome is open"
    )
    assert velo_response(VeloResult(status=VeloStatus.DONE)) == "Done."
    assert velo_response(
        VeloResult(status=VeloStatus.FAILED, reason="target not found")
    ) == "I couldn't finish that. target not found"
    assert velo_response(
        VeloResult(status=VeloStatus.STOPPED, reason="cancelled by user", metrics=VeloMetrics())
    ) == "Stopped. cancelled by user"
    assert "decide" in velo_response(VeloResult(status=VeloStatus.ASK_USER, reason="which one?"))


class _YieldingCua(FakeCua):
    """FakeCua that yields between steps so the event pump stays current."""

    async def execute(self, decision, observation, objective) -> Any:
        await asyncio.sleep(0)
        return await super().execute(decision, observation, objective)


async def test_velo_entry_cancel_during_run_stops_before_next_action() -> None:
    fake_cua = _YieldingCua(observations=[observation(target("t1", "button"))])
    jev = ScriptedJev(act(VeloActionKind.CLICK, target_id="t1"))
    cancel_log: list[str] = []
    entry = VeloAgentEntry(
        _settings(), context_factory=_fake_velo_context(fake_cua, jev, cancel_log)
    )

    async def on_event(kind: str, data: dict[str, Any]) -> None:
        if kind == "agent.progress" and "verify" in data.get("message", ""):
            # The desktop host cancels mid-run; the loop must stop before
            # the next action starts.
            await entry.cancel()

    result = await entry.run("click", thread_id="", on_event=on_event, cancel_check=lambda: False)

    assert result["status"] is VeloStatus.STOPPED.value, result["reason"]
    assert "cancel" in result["reason"].lower()
    assert len(fake_cua.call_kinds("execute")) < 3  # bounded: no runaway repeats
    assert cancel_log == ["cancelled"]


async def test_velo_entry_default_context_fails_closed_without_infrastructure() -> None:
    from assistant.velo.types import JevServiceError

    # This machine has no credential configured and no CUA driver: the run
    # must fail closed with a clear terminal error -- never a fallback.
    entry = VeloAgentEntry(_settings())
    with pytest.raises((JevServiceError, RuntimeError)):
        await entry.run(
            "Open Chrome", thread_id="", on_event=_EventLog(), cancel_check=lambda: False
        )


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


async def test_status_provider_reports_subsystems(tmp_path: Any) -> None:
    provider = build_status_provider(
        _settings(cua_enabled=False, memory_backend="sqlite", sani_data_dir=str(tmp_path))
    )
    payload = await provider()
    assert payload["driver"]["found"] is False
    assert payload["memory_backend"] == "sqlite"
    assert payload["jev_credential"] == "none"
