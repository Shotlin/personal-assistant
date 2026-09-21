"""Velo lifecycle: OBSERVE -> DECIDE -> ACT -> VERIFY -> REPEAT.

The run loop owns only run-local state (Velo spec section 7): objective,
exact user payloads, step count, last observation/decision, a bounded
recent history, and metrics. Every mutation is followed by fresh
verification; unknown outcomes are re-observed, never blindly retried;
identical actions on an unchanged scene hit the repeat bound; and any
uncertainty terminates in ASK_USER / STOPPED / FAILED. JEV is the only
decision engine -- there is no Deep Agent and no OpenAI/OpenRouter
fallback anywhere on this path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from assistant.runtime.session import DesktopRunCancelled
from assistant.velo.types import (
    VeloActionKind,
    VeloCuaAdapterProtocol,
    VeloDecision,
    VeloDecisionStatus,
    VeloError,
    VeloLimits,
    VeloMetrics,
    VeloObjective,
    VeloObservation,
    VeloResult,
    VeloStatus,
    VeloStep,
)

logger = logging.getLogger("assistant.velo.agent")

#: JEV-selected WAIT sleeps this long once -- bounded settling time, never
#: a polling interval (Velo spec section 12).
WAIT_SECONDS = 1.0

ProgressCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]


class VeloAgent:
    """One bounded quick-control run."""

    def __init__(
        self,
        cua: VeloCuaAdapterProtocol,
        jev: Any,
        limits: VeloLimits,
        *,
        allowed_apps: dict[str, str] | None = None,
        wait_seconds: float = WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cua = cua
        self._jev = jev
        self._limits = limits
        self._allowed_apps = allowed_apps or {}
        self._wait_seconds = wait_seconds
        self._clock = clock
        self._sleeper = sleeper

    async def run(
        self,
        objective: VeloObjective,
        *,
        cancel_check: CancelCheck | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> VeloResult:
        """Run the loop until DONE / ASK_USER / STOP / a bound is reached."""
        started = self._clock()
        observe_ms = 0.0
        jev_ms = 0.0
        act_ms = 0.0
        verify_ms = 0.0
        decision_count = 0
        action_count = 0
        steps: list[VeloStep] = []
        history: deque[dict[str, str]] = deque(maxlen=self._limits.recent_history_steps)
        consecutive_failures = 0
        repeats = 0
        last_signature = ""
        last_changed = True

        def progress(line: str) -> None:
            if on_progress is not None:
                on_progress(line)

        async def timed(awaitable: Awaitable[Any]) -> tuple[Any, float]:
            begin = self._clock()
            try:
                value = await awaitable
            finally:
                elapsed = (self._clock() - begin) * 1000.0
            return value, elapsed

        try:
            observation, elapsed = await timed(self._cua.observe())
            observe_ms += elapsed
            progress(f"[0] observe: {self._scene(observation)}")

            while True:
                if cancel_check is not None and cancel_check():
                    return self._result(
                        VeloStatus.STOPPED,
                        "cancelled by user before next action",
                        steps,
                        started,
                        observe_ms,
                        jev_ms,
                        act_ms,
                        verify_ms,
                        decision_count,
                        action_count,
                    )
                if len(steps) >= self._limits.max_steps:
                    return self._result(
                        VeloStatus.STOPPED,
                        f"max steps reached ({self._limits.max_steps})",
                        steps,
                        started,
                        observe_ms,
                        jev_ms,
                        act_ms,
                        verify_ms,
                        decision_count,
                        action_count,
                    )
                if self._clock() - started >= self._limits.max_runtime_seconds:
                    return self._result(
                        VeloStatus.STOPPED,
                        f"max runtime reached ({self._limits.max_runtime_seconds:.0f}s)",
                        steps,
                        started,
                        observe_ms,
                        jev_ms,
                        act_ms,
                        verify_ms,
                        decision_count,
                        action_count,
                    )

                decision, decide_ms = await timed(
                    self._jev.decide(
                        objective=objective,
                        observation=observation,
                        recent_steps=list(history),
                        step=len(steps) + 1,
                        max_steps=self._limits.max_steps,
                        allowed_apps=self._allowed_apps,
                    )
                )
                jev_ms += decide_ms
                decision_count += 1
                index = len(steps) + 1
                progress(
                    f"[{index}] decide: {self._describe(decision)} "
                    f"(confidence {decision.confidence:.2f}, {decide_ms:.0f} ms)"
                )

                if decision.status is not VeloDecisionStatus.ACT:
                    step = VeloStep(index=index, decision=decision, decide_ms=decide_ms)
                    steps.append(step)
                    status = {
                        VeloDecisionStatus.DONE: VeloStatus.DONE,
                        VeloDecisionStatus.ASK_USER: VeloStatus.ASK_USER,
                        VeloDecisionStatus.STOP: VeloStatus.STOPPED,
                    }[decision.status]
                    return self._result(
                        status,
                        decision.reason or status.value,
                        steps,
                        started,
                        observe_ms,
                        jev_ms,
                        act_ms,
                        verify_ms,
                        decision_count,
                        action_count,
                    )

                action_kind = decision.action
                assert action_kind is not None  # ACT decisions carry an action

                if action_kind is VeloActionKind.OBSERVE:
                    observation, observe_elapsed = await timed(self._cua.observe())
                    observe_ms += observe_elapsed
                    steps.append(VeloStep(index=index, decision=decision, decide_ms=decide_ms))
                    history.append(
                        {"step": str(index), "action": decision.signature(), "result": "observed"}
                    )
                    progress(f"[{index}] observe: {self._scene(observation)}")
                    continue

                if action_kind is VeloActionKind.WAIT:
                    await self._sleeper(self._wait_seconds)
                    observation, observe_elapsed = await timed(self._cua.observe())
                    observe_ms += observe_elapsed
                    steps.append(VeloStep(index=index, decision=decision, decide_ms=decide_ms))
                    history.append(
                        {"step": str(index), "action": decision.signature(), "result": "waited"}
                    )
                    continue

                action_result, act_step_ms = await timed(
                    self._cua.execute(decision, observation, objective)
                )
                act_ms += act_step_ms
                action_count += 1
                progress(
                    f"[{index}] action: {action_kind.value} -> "
                    f"{action_result.status} ({act_step_ms:.0f} ms)"
                )
                if action_result.status == "failed":
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                verification, verify_step_ms = await timed(self._cua.verify(decision, observation))
                verify_ms += verify_step_ms
                observation = verification.observation
                progress(
                    f"[{index}] verify: "
                    f"{'scene changed' if verification.changed else 'scene unchanged'}"
                )
                steps.append(
                    VeloStep(
                        index=index,
                        decision=decision,
                        action_result=action_result,
                        verification=verification,
                        decide_ms=decide_ms,
                        act_ms=act_step_ms,
                        verify_ms=verify_step_ms,
                    )
                )
                history.append(
                    {
                        "step": str(index),
                        "action": decision.signature(),
                        "result": action_result.status,
                        "scene_changed": "yes" if verification.changed else "no",
                    }
                )

                if consecutive_failures >= self._limits.max_consecutive_failed_actions:
                    return self._result(
                        VeloStatus.FAILED,
                        f"{consecutive_failures} consecutive CUA actions failed; "
                        "last error: "
                        f"{action_result.detail[:160] or action_result.effect}",
                        steps,
                        started,
                        observe_ms,
                        jev_ms,
                        act_ms,
                        verify_ms,
                        decision_count,
                        action_count,
                    )

                signature = decision.signature()
                if signature == last_signature and not last_changed:
                    repeats += 1
                else:
                    repeats = 0
                last_signature = signature
                last_changed = verification.changed
                if repeats >= self._limits.max_same_action_repeats:
                    return self._result(
                        VeloStatus.STOPPED,
                        "loop detected: the same action on an unchanged scene "
                        f"repeated {repeats} time(s)",
                        steps,
                        started,
                        observe_ms,
                        jev_ms,
                        act_ms,
                        verify_ms,
                        decision_count,
                        action_count,
                    )

        except DesktopRunCancelled:
            return self._result(
                VeloStatus.STOPPED,
                "cancelled by user",
                steps,
                started,
                observe_ms,
                jev_ms,
                act_ms,
                verify_ms,
                decision_count,
                action_count,
            )
        except VeloError as exc:
            logger.warning("velo_run_failed", extra={"event": "velo_run_failed"})
            return self._result(
                VeloStatus.FAILED,
                str(exc)[:200],
                steps,
                started,
                observe_ms,
                jev_ms,
                act_ms,
                verify_ms,
                decision_count,
                action_count,
            )
        except Exception as exc:  # noqa: BLE001 -- fail closed, never fall back
            return self._result(
                VeloStatus.FAILED,
                f"unexpected error: {exc}"[:200],
                steps,
                started,
                observe_ms,
                jev_ms,
                act_ms,
                verify_ms,
                decision_count,
                action_count,
            )

    # ------------------------------------------------------------------

    def _result(
        self,
        status: VeloStatus,
        reason: str,
        steps: list[VeloStep],
        started: float,
        observe_ms: float,
        jev_ms: float,
        act_ms: float,
        verify_ms: float,
        decision_count: int,
        action_count: int,
    ) -> VeloResult:
        total_ms = (self._clock() - started) * 1000.0
        metrics = VeloMetrics(
            observe_ms=observe_ms,
            jev_decision_ms=jev_ms,
            cua_action_ms=act_ms,
            verify_ms=verify_ms,
            total_run_ms=total_ms,
            decision_count=decision_count,
            action_count=action_count,
            screenshot_count=self._cua.screenshot_count,
            step_count=len(steps),
        )
        return VeloResult(status=status, reason=reason, steps=tuple(steps), metrics=metrics)

    @staticmethod
    def _describe(decision: VeloDecision) -> str:
        if decision.status is not VeloDecisionStatus.ACT:
            return decision.status.value
        action = decision.action
        if action is None:
            return "ACT"
        argument = (
            decision.target_id
            or decision.payload_id
            or decision.key
            or decision.combo
            or decision.direction
            or decision.app_name
        )
        return f"{action.value} {argument}".strip()

    @staticmethod
    def _scene(observation: VeloObservation) -> str:
        parts = [observation.foreground_app or "Desktop"]
        if observation.window_title:
            parts.append(observation.window_title)
        return " / ".join(parts) + f" [{len(observation.targets)} targets]"
