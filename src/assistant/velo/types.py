"""Stable Velo contracts (Velo spec section 6).

Small, frozen dataclasses and enums only. Everything the JEV engine, the
CUA adapter, and the run loop exchange goes through these types; no module
below ``assistant.velo`` invents its own shapes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class VeloStatus(StrEnum):
    """Terminal (and in-run) statuses of a Velo run."""

    RUNNING = "RUNNING"
    DONE = "DONE"
    ASK_USER = "ASK_USER"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class VeloDecisionStatus(StrEnum):
    """The only statuses JEV may return (Velo spec section 9)."""

    ACT = "ACT"
    DONE = "DONE"
    ASK_USER = "ASK_USER"
    STOP = "STOP"


class VeloActionKind(StrEnum):
    """The only action types the executor accepts (Velo spec section 6).

    Everything outside this set -- shell commands, arbitrary tools, raw
    code -- is rejected at validation time. DONE/ASK_USER/STOP appear here
    for contract completeness; they terminate the loop and are never
    dispatched to CUA.
    """

    LAUNCH_APP = "LAUNCH_APP"
    CLICK = "CLICK"
    TYPE_USER_TEXT = "TYPE_USER_TEXT"
    PRESS_KEY = "PRESS_KEY"
    HOTKEY = "HOTKEY"
    SCROLL = "SCROLL"
    OBSERVE = "OBSERVE"
    WAIT = "WAIT"
    DONE = "DONE"
    ASK_USER = "ASK_USER"
    STOP = "STOP"


#: Kinds the CUA adapter must dispatch as desktop mutations.
MUTATING_ACTION_KINDS = frozenset(
    {
        VeloActionKind.LAUNCH_APP,
        VeloActionKind.CLICK,
        VeloActionKind.TYPE_USER_TEXT,
        VeloActionKind.PRESS_KEY,
        VeloActionKind.HOTKEY,
        VeloActionKind.SCROLL,
    }
)


class VeloError(RuntimeError):
    """Base class for Velo failures; always terminal, never retried blindly."""


class VeloCuaError(VeloError):
    """CUA infrastructure failure (missing tools, driver refused, unknown)."""


class JevServiceError(VeloError):
    """JEV/TypeSafe API failure; Velo fails, it never calls another model."""


class JevContractError(VeloError):
    """JEV returned something outside the decision contract; fail closed."""


@dataclass(frozen=True)
class VeloObjective:
    """What the user asked for, plus exact text payloads (spec section 23).

    ``user_text_payloads`` hold byte-exact user-provided text keyed by
    ``user_text_N``; JEV may select a payload id but never rewrite the
    value. ``text_candidates`` are mechanical slices of the objective
    itself (quoted spans, phrases after trigger words) used for typed
    search terms; they are data, not generated text.
    """

    text: str
    user_text_payloads: Mapping[str, str] = field(default_factory=dict)
    text_candidates: tuple[str, ...] = ()

    def payload_text(self, payload_id: str) -> str | None:
        """Exact bytes for a payload or candidate id, else None."""
        if payload_id.startswith("user_text_"):
            return self.user_text_payloads.get(payload_id)
        if payload_id.startswith("text_"):
            index = payload_id.removeprefix("text_")
            if index.isdigit():
                position = int(index) - 1
                if 0 <= position < len(self.text_candidates):
                    return self.text_candidates[position]
        return None


@dataclass(frozen=True)
class VeloTarget:
    """One addressable UI element from CUA semantic observation."""

    id: str
    role: str
    label: str
    value: str = ""
    element_token: str = ""

    def compact(self) -> dict[str, str]:
        payload = {"id": self.id, "role": self.role, "label": self.label}
        if self.value:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True)
class VeloObservation:
    """Compact normalized desktop state (Velo spec section 8).

    Mechanical translation of CUA evidence only; never a second reasoning
    engine. Screenshots are never embedded -- semantic/accessibility state
    first.
    """

    foreground_app: str = ""
    window_title: str = ""
    url: str = ""
    focused_element: str = ""
    modal: bool = False
    targets: tuple[VeloTarget, ...] = ()

    def target(self, target_id: str) -> VeloTarget | None:
        for target in self.targets:
            if target.id == target_id:
                return target
        return None

    def signature(self) -> tuple[str, str, str, str, bool, tuple[tuple[str, str, str], ...]]:
        """Scene fingerprint for fresh-verification and loop detection."""
        return (
            self.foreground_app,
            self.window_title,
            self.url,
            self.focused_element,
            self.modal,
            tuple((target.role, target.label, target.value) for target in self.targets),
        )

    def compact(self) -> dict[str, object]:
        return {
            "foreground_app": self.foreground_app,
            "window_title": self.window_title,
            "url": self.url,
            "focused_element": self.focused_element,
            "modal": self.modal,
            "targets": [target.compact() for target in self.targets],
        }


@dataclass(frozen=True)
class VeloDecision:
    """One validated JEV decision (Velo spec section 9).

    Only fields consistent with ``status``/``action`` may be set; ids are
    validated against the observation and the objective before any CUA
    call is built.
    """

    status: VeloDecisionStatus
    action: VeloActionKind | None = None
    target_id: str = ""
    payload_id: str = ""
    key: str = ""
    combo: str = ""
    direction: str = ""
    app_name: str = ""
    confidence: float = 0.0
    reason: str = ""

    def signature(self) -> str:
        """Loop-detection fingerprint of the concrete action."""
        if self.status is not VeloDecisionStatus.ACT or self.action is None:
            return self.status.value
        argument = (
            self.target_id
            or self.payload_id
            or self.key
            or self.combo
            or self.direction
            or self.app_name
        )
        return f"{self.action.value}:{argument}"


@dataclass(frozen=True)
class VeloActionResult:
    """Normalized outcome of one CUA mutation (fail-closed semantics)."""

    status: str  # ok | failed | unknown
    effect: str  # confirmed | suspected_noop | unverifiable | not_applicable
    detail: str = ""


@dataclass(frozen=True)
class VeloVerification:
    """Fresh post-action observation plus a scene-changed verdict."""

    observation: VeloObservation
    changed: bool
    note: str = ""


@dataclass(frozen=True)
class VeloStep:
    """One loop iteration, with its real per-step timings (ms)."""

    index: int
    decision: VeloDecision
    action_result: VeloActionResult | None = None
    verification: VeloVerification | None = None
    decide_ms: float = 0.0
    act_ms: float = 0.0
    verify_ms: float = 0.0


@dataclass(frozen=True)
class VeloMetrics:
    """Real measured latency (Velo spec section 12); never claimed, measured."""

    observe_ms: float = 0.0
    jev_decision_ms: float = 0.0
    cua_action_ms: float = 0.0
    verify_ms: float = 0.0
    total_run_ms: float = 0.0
    decision_count: int = 0
    action_count: int = 0
    screenshot_count: int = 0
    step_count: int = 0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "observe_ms": round(self.observe_ms, 1),
            "jev_decision_ms": round(self.jev_decision_ms, 1),
            "cua_action_ms": round(self.cua_action_ms, 1),
            "verify_ms": round(self.verify_ms, 1),
            "total_run_ms": round(self.total_run_ms, 1),
            "decision_count": self.decision_count,
            "action_count": self.action_count,
            "screenshot_count": self.screenshot_count,
            "step_count": self.step_count,
        }


@dataclass(frozen=True)
class VeloLimits:
    """Bounded-run ceilings (Velo spec section 14); centralized in settings."""

    max_steps: int = 20
    max_runtime_seconds: float = 120.0
    max_same_action_repeats: int = 2
    max_consecutive_failed_actions: int = 2
    recent_history_steps: int = 5


@dataclass(frozen=True)
class VeloResult:
    """Terminal outcome of one Velo run."""

    status: VeloStatus
    reason: str = ""
    steps: tuple[VeloStep, ...] = ()
    metrics: VeloMetrics = field(default_factory=VeloMetrics)


class VeloCuaAdapterProtocol(Protocol):
    """What the run loop needs from the CUA adapter (spec section 6)."""

    screenshot_count: int

    async def observe(self) -> VeloObservation: ...

    async def execute(
        self, decision: VeloDecision, observation: VeloObservation, objective: VeloObjective
    ) -> VeloActionResult: ...

    async def verify(self, decision: VeloDecision, before: VeloObservation) -> VeloVerification: ...
