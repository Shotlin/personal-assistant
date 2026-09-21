"""Deterministic doubles for Velo tests: no driver, no network, no model.

``FakeCua`` replaces the CUA adapter; ``ScriptedJev`` replaces the JEV
engine. The real JEV engine is exercised in ``test_jev_contract.py`` with
``_classify`` stubbed at the package boundary; the real adapter is
exercised in ``test_cua_adapter.py`` with fake MCP tools.
"""

from __future__ import annotations

from assistant.runtime.session import DesktopRunCancelled
from assistant.velo.types import (
    VeloActionKind,
    VeloActionResult,
    VeloDecision,
    VeloDecisionStatus,
    VeloObservation,
    VeloTarget,
    VeloVerification,
)

OK_RESULT = VeloActionResult(status="ok", effect="confirmed")
UNKNOWN_RESULT = VeloActionResult(
    status="unknown", effect="unverifiable", detail="outcome unconfirmed"
)
FAILED_RESULT = VeloActionResult(status="failed", effect="unverifiable", detail="driver refused")


def target(
    target_id: str, label: str, role: str = "button", value: str = "", token: str | None = None
) -> VeloTarget:
    return VeloTarget(
        id=target_id, role=role, label=label, value=value, element_token=token or target_id
    )


def observation(
    *targets: VeloTarget,
    foreground_app: str = "Finder",
    window_title: str = "",
    url: str = "",
    focused_element: str = "",
    modal: bool = False,
) -> VeloObservation:
    return VeloObservation(
        foreground_app=foreground_app,
        window_title=window_title,
        url=url,
        focused_element=focused_element,
        modal=modal,
        targets=tuple(targets),
    )


def act(action: VeloActionKind, **fields: str) -> VeloDecision:
    return VeloDecision(status=VeloDecisionStatus.ACT, action=action, confidence=0.9, **fields)


def done(reason: str = "objective complete") -> VeloDecision:
    return VeloDecision(status=VeloDecisionStatus.DONE, confidence=0.95, reason=reason)


def ask_user(reason: str = "two plausible targets") -> VeloDecision:
    return VeloDecision(status=VeloDecisionStatus.ASK_USER, confidence=0.8, reason=reason)


def stop(reason: str = "target unavailable") -> VeloDecision:
    return VeloDecision(status=VeloDecisionStatus.STOP, confidence=0.85, reason=reason)


class FakeCua:
    """Scripted VeloCuaAdapterProtocol double."""

    def __init__(
        self,
        observations: list[VeloObservation] | None = None,
        results: list[VeloActionResult] | None = None,
    ) -> None:
        self.observations = list(observations) if observations else [observation()]
        self.results = list(results) if results else []
        self.calls: list[tuple[str, str]] = []
        self.screenshot_count = 0
        self.cancelled = False
        self._observe_index = 0
        self._result_index = 0

    async def observe(self) -> VeloObservation:
        self.calls.append(("observe", ""))
        current = self.observations[min(self._observe_index, len(self.observations) - 1)]
        self._observe_index += 1
        return current

    async def execute(
        self, decision: VeloDecision, observation: VeloObservation, objective
    ) -> VeloActionResult:
        self.calls.append(("execute", decision.signature()))
        if self.cancelled:
            raise DesktopRunCancelled("desktop run was cancelled")
        if not self.results:
            return OK_RESULT
        result = self.results[min(self._result_index, len(self.results) - 1)]
        self._result_index += 1
        return result

    async def verify(self, decision: VeloDecision, before: VeloObservation) -> VeloVerification:
        self.calls.append(("verify", decision.signature()))
        after = await self.observe()
        return VeloVerification(observation=after, changed=after.signature() != before.signature())

    def call_kinds(self, kind: str) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] == kind]


class ScriptedJev:
    """Returns queued decisions (repeating the last one once exhausted)."""

    def __init__(self, *decisions: VeloDecision) -> None:
        self._queue = list(decisions)
        self._last: VeloDecision | None = None
        self.calls: list[dict] = []

    async def decide(self, **kwargs) -> VeloDecision:
        self.calls.append(kwargs)
        if self._queue:
            self._last = self._queue.pop(0)
        if self._last is None:
            raise AssertionError("ScriptedJev ran out of scripted decisions")
        return self._last
