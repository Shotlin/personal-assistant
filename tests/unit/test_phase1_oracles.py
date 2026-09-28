"""Phase 1 regression oracles (T01, file 04).

Four oracles pin behaviors the mission foundation depends on. At T01 three of
them FAIL for the named defect -- that failure is the recorded baseline
evidence, and the owning task turns each one green:

- unknown/cancelled outcome must not read as ``status: done`` (A02; fixed T04);
- two concurrent agent runs must cancel independently (A07 shared-flag
  cross-talk; fixed T06);
- alternating observation digests must trip the no-progress breaker (A09;
  fixed T04);
- the legacy parser fast path must perform zero model calls (regression
  guard; passes now and must keep passing).

These tests assert through the exact shipping interfaces (VeloEntry,
DeepAgentEntry, NoProgressTracker), never a reimplementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk

from assistant.settings import Settings
from assistant.velo.contracts import (
    NoProgressTracker,
    OutcomeState,
    ProgressKind,
    TaskState,
)
from assistant.velo.controller import VeloEntry
from assistant.velo.parse import parse
from assistant.velo.recipes import RecipeResult
from tests.unit.velo_fakes import (
    FakeRuntime,
    app_entry,
    reset_world,
    standard_tools,
    window_state_payload,
)

# -- oracle 1: an unknown or cancelled effect cannot read as success ----------


def test_unknown_effect_cannot_become_success() -> None:
    """``_local_result`` must not report ``status: done`` for UNKNOWN/CANCELLED.

    A02: the entry always answered ``done`` even when the recipe ended
    UNKNOWN, and the host maps ``done`` to a completed turn. The honest
    result surfaces the outcome as the status.
    """
    task = TaskState(instruction="fixture", conversation="c1")
    for outcome_state in (OutcomeState.UNKNOWN, OutcomeState.CANCELLED):
        result = VeloEntry._local_result(
            task, RecipeResult("open_app", outcome_state, "fixture"), actions_used=1
        )
        assert result["status"] != "done", (
            f"outcome {outcome_state.value} must not be reported as done"
        )


# -- oracle 2: two agent runs cancel independently ----------------------------


class _SlowStreamAgent:
    """Streams numbered chunks slowly so a cancel can land mid-run."""

    def __init__(self) -> None:
        self.streams_open = 0

    async def astream(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        self.streams_open += 1
        try:
            for index in range(40):
                yield AIMessageChunk(content=f"chunk-{index}", id="m1")
                await asyncio.sleep(0.005)
        finally:
            self.streams_open -= 1


def _mission_settings(tmp_path: Any) -> Settings:
    return Settings(
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path / "sani-data"),
        cua_enabled=False,
        openrouter_api_key="fixture-not-a-secret",
    )


async def test_two_agent_runs_cancel_independently(tmp_path: Any) -> None:
    """Cancelling run A must not kill concurrent run B (A07 cross-talk)."""
    from assistant.core.agents import DeepAgentEntry, RuntimeProvider

    agent = _SlowStreamAgent()
    settings = _mission_settings(tmp_path)
    provider = RuntimeProvider(settings, agent_builder=lambda: _as_coro(agent))
    deep = DeepAgentEntry(settings, provider=provider)

    started = asyncio.Event()

    async def on_event(kind: str, data: dict[str, Any]) -> None:
        if kind == "agent.token":
            started.set()

    async def run_named(name: str, text: str) -> dict[str, Any]:
        return await deep.run(
            text, thread_id=f"conv-{name}", on_event=on_event, cancel_check=lambda: False
        )

    task_a = asyncio.create_task(run_named("a", "fixture question a"))
    task_b = asyncio.create_task(run_named("b", "fixture question b"))
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.sleep(0.05)  # both streams are open and producing

    # The app's cancel path: entry.cancel() for the run, then task.cancel().
    await deep.cancel()
    task_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task_a, timeout=5)

    result_b = await asyncio.wait_for(task_b, timeout=10)
    assert result_b["status"] == "done", (
        "run B must complete normally after run A was cancelled"
    )
    assert agent.streams_open == 0


async def _as_coro(agent: Any) -> Any:
    return agent


# -- oracle 3: alternating observations cannot loop forever -------------------


def test_alternating_observations_cannot_loop_forever() -> None:
    """A,B,A,B... must trip the no-progress breaker (A09; file 06 RF-12).

    The tracker compared only the immediately previous digest, so two
    alternating screens reset the counter forever. With the windowed
    history the alternation itself is the stall signature. Exact finite
    stop (RF-12): with a ceiling of 4, the first two registers introduce
    the screens, then four repeats trip the breaker on the 6th register.
    """
    tracker = NoProgressTracker(max_steps_without_change=4)
    digests = ["screen-a", "screen-b"]
    results = [tracker.register(ProgressKind.OBSERVATION, digests[i % 2]) for i in range(12)]
    assert results[:5] == [True] * 5, "the first five registers may pass"
    assert not results[5], "the 6th register must trip the breaker"
    assert tracker.exhausted
    assert sum(results) == 5, "exactly five steps survive; the loop cannot run 12"


# -- oracle 4: the legacy parser performs zero model calls --------------------


async def test_legacy_parser_performs_zero_model_calls() -> None:
    """``open safari`` must stay on the local route: no Deep, no JEV."""
    from tests.unit.velo_fakes import SHARED_APPS

    reset_world()
    SHARED_APPS["list"] = [app_entry("Safari", pid=9, running=True)]
    SHARED_APPS["windows"][9] = [
        {"window_id": 2, "is_on_screen": True, "bounds": {"height": 600, "width": 800}}
    ]
    SHARED_APPS["state"][(9, 2)] = window_state_payload(
        [{"role": "AXTextField", "label": "address", "value": "", "element_token": "addr-1"}],
        pid=9,
        window_id=2,
    )

    class _RecordingDeep:
        def __init__(self) -> None:
            self.runs: list[str] = []

        async def cancel(self) -> None:
            return None

        async def run(self, text: str, **_kwargs: Any) -> dict[str, Any]:
            self.runs.append(text)
            return {"status": "done", "response": ""}

    class _StubSettings:
        velo_jev_enabled = False
        velo_command_deadline_seconds = 90

    deep = _RecordingDeep()
    runtime = FakeRuntime(standard_tools())
    entry = VeloEntry(
        _StubSettings(),
        get_runtime=runtime.runtime,
        deep_entry=deep,
        jev_factory=None,
    )

    command = parse("open safari")
    assert command is not None, "the fixture phrase must parse locally"
    result = await entry.run(
        "open safari",
        thread_id="conv-oracle",
        on_event=lambda kind, data: _noop(),
        cancel_check=lambda: False,
    )
    assert deep.runs == [], "a parsed command must never reach the reasoning model"
    assert result["route"] == "local"


async def _noop() -> None:
    return None
