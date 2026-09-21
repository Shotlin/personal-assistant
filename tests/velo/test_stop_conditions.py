"""Stop-condition tests: bounds, loops, failures, cancellation, isolation."""

import os
import subprocess
import sys
from pathlib import Path

from assistant.velo.agent import VeloAgent
from assistant.velo.types import VeloActionKind, VeloLimits, VeloObjective, VeloStatus
from tests.velo.fakes import (
    FAILED_RESULT,
    UNKNOWN_RESULT,
    FakeCua,
    ScriptedJev,
    act,
    observation,
    target,
)

CLICK = act(VeloActionKind.CLICK, target_id="t1")
OBSERVE = act(VeloActionKind.OBSERVE)


def build_agent(
    fake_cua: FakeCua,
    jev: ScriptedJev,
    *,
    clock=None,
    sleeper=None,
    **limit_overrides,
) -> VeloAgent:
    limits = VeloLimits(**{"max_steps": 8, **limit_overrides})
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    return VeloAgent(fake_cua, jev, limits, allowed_apps={}, **kwargs)


class FakeClock:
    """Advances by ``step`` seconds on every read."""

    def __init__(self, step: float = 1000.0) -> None:
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        now = self._now
        self._now += self._step
        return now


async def test_max_steps_stop() -> None:
    fake_cua = FakeCua(observations=[observation(target("t1", "button"))])
    jev = ScriptedJev(OBSERVE)  # repeats forever
    agent = build_agent(fake_cua, jev, max_steps=5)

    result = await agent.run(VeloObjective(text="watch"))

    assert result.status is VeloStatus.STOPPED
    assert "max steps" in result.reason
    assert result.metrics.step_count == 5
    assert result.metrics.decision_count == 5


async def test_max_runtime_stop() -> None:
    fake_cua = FakeCua()
    jev = ScriptedJev(OBSERVE)
    agent = build_agent(fake_cua, jev, clock=FakeClock(step=1000.0))

    result = await agent.run(VeloObjective(text="watch"))

    assert result.status is VeloStatus.STOPPED
    assert "max runtime" in result.reason


async def test_repeated_action_on_unchanged_scene_stops() -> None:
    fake_cua = FakeCua(observations=[observation(target("t1", "button"))])
    jev = ScriptedJev(CLICK)  # repeats forever
    agent = build_agent(fake_cua, jev, max_same_action_repeats=2)

    result = await agent.run(VeloObjective(text="click it"))

    assert result.status is VeloStatus.STOPPED
    assert "loop detected" in result.reason
    # Baseline attempt plus exactly `max_same_action_repeats` repeats.
    assert len(fake_cua.call_kinds("execute")) == 3


async def test_scene_change_resets_the_repeat_counter() -> None:
    # Seven distinct scenes: initial observe + verify-per-iteration for six
    # iterations, so every fresh verification sees a changed scene.
    fake_cua = FakeCua(
        observations=[
            observation(target("t1", "button", value=f"state {index}")) for index in range(7)
        ]
    )
    jev = ScriptedJev(CLICK)
    agent = build_agent(fake_cua, jev, max_same_action_repeats=2, max_steps=6)

    result = await agent.run(VeloObjective(text="click it"))

    # Each click changes the scene, so no loop is declared.
    assert result.status is VeloStatus.STOPPED
    assert "max steps" in result.reason
    assert len(fake_cua.call_kinds("execute")) == 6


async def test_consecutive_failed_actions_fail_the_run() -> None:
    fake_cua = FakeCua(observations=[observation(target("t1", "button"))], results=[FAILED_RESULT])
    jev = ScriptedJev(CLICK)
    agent = build_agent(fake_cua, jev, max_consecutive_failed_actions=2)

    result = await agent.run(VeloObjective(text="click it"))

    assert result.status is VeloStatus.FAILED
    assert "consecutive" in result.reason
    assert len(fake_cua.call_kinds("execute")) == 2


async def test_unknown_outcome_is_reobserved_never_blindly_repeated() -> None:
    fake_cua = FakeCua(observations=[observation(target("t1", "button"))], results=[UNKNOWN_RESULT])
    jev = ScriptedJev(CLICK)
    agent = build_agent(fake_cua, jev, max_same_action_repeats=2)

    result = await agent.run(VeloObjective(text="click it"))

    assert result.status is VeloStatus.STOPPED  # loop bound on unchanged scene
    kinds = [kind for kind, _ in fake_cua.calls]
    # Every execute is followed by verify(fresh observe) before the next one.
    for index, kind in enumerate(kinds):
        if kind == "execute":
            assert "verify" in kinds[index + 1 : index + 2], (
                "unknown outcome must be followed by fresh verification"
            )


async def test_cancelled_run_never_starts_a_new_action() -> None:
    fake_cua = FakeCua(observations=[observation(target("t1", "button"))])
    jev = ScriptedJev(CLICK)
    agent = build_agent(fake_cua, jev)

    def cancel_after_first() -> bool:
        return len(fake_cua.call_kinds("execute")) >= 1

    result = await agent.run(VeloObjective(text="click"), cancel_check=cancel_after_first)

    assert result.status is VeloStatus.STOPPED
    assert len(fake_cua.call_kinds("execute")) == 1


async def test_wait_is_bounded_and_then_observes() -> None:
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    fake_cua = FakeCua()
    jev = ScriptedJev(
        act(VeloActionKind.WAIT), act(VeloActionKind.OBSERVE), act(VeloActionKind.OBSERVE)
    )
    agent = build_agent(fake_cua, jev, sleeper=sleeper, max_steps=3)

    result = await agent.run(VeloObjective(text="settle"))

    assert result.status is VeloStatus.STOPPED
    assert sleeps and sleeps[0] == 1.0
    assert len(fake_cua.call_kinds("execute")) == 0


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_velo_never_imports_deep_agent_or_fallback_models() -> None:
    """No Deep Agent, no OpenAI/OpenRouter fallback on the Velo path."""
    probe = (
        "import sys; import assistant.velo, assistant.velo.agent, assistant.velo.jev, "
        "assistant.velo.cua_adapter; "
        "banned = {'deepagents', 'langchain_openai', 'langchain_openrouter', 'openai'}; "
        "hits = banned & set(sys.modules); "
        "assert not hits, f'velo imported fallback modules: {sorted(hits)}'"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
