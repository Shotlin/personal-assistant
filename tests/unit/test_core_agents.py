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

    result = await entry.run("Open Chrome", on_event=log, cancel_check=lambda: False)

    assert result["status"] is VeloStatus.DONE.value
    assert fake_cua.call_kinds("execute") == [("execute", "LAUNCH_APP:com.google.Chrome")]
    assert any(kind == "progress" for kind, _ in log.events)


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
        if kind == "progress" and "verify" in data.get("line", ""):
            # The desktop host cancels mid-run; the loop must stop before
            # the next action starts.
            await entry.cancel()

    result = await entry.run("click", on_event=on_event, cancel_check=lambda: False)

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
        await entry.run("Open Chrome", on_event=_EventLog(), cancel_check=lambda: False)


async def test_deep_entry_streams_and_returns_response() -> None:
    class FakeDeepAgent:
        async def ainvoke(self, messages, config, context=None):  # noqa: ANN001

            class _Msg:
                type = "ai"
                tool_calls = None
                content = "Here is the answer."

            return {"messages": [_Msg()]}

    builds: list[int] = []

    async def builder() -> FakeDeepAgent:
        builds.append(1)
        return FakeDeepAgent()

    entry = DeepAgentEntry(_settings(), agent_builder=builder)
    log = _EventLog()
    result = await entry.run("What is 2 + 2?", on_event=log, cancel_check=lambda: False)

    assert result["status"] == "done"
    assert result["response"] == "Here is the answer."
    assert result["thread_id"].startswith("sani:")
    assert any(kind == "started" for kind, _ in log.events)
    assert builds == [1]


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
