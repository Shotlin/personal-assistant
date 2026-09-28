"""Shared Velo contracts: the types every component exchanges.

Velo is the persistent controller that owns the user's intent, task state and
execution. JEV makes compact structured decisions when interpretation is
needed. CUA observes and operates the computer. Nothing below the controller
invents its own shapes: a Task, a Target, an Action, an Outcome and a
Verification all travel through this module (master plan sections 2, 3, 6-8).

Two rules shape everything here:

- **Target ownership.** An explicit user target resolves to an application
  identity and is then *maintained* for the whole task. Foreground state is
  evidence, never permission to substitute another application: "Open Safari"
  must resolve to Safari regardless of Chrome's presence.
- **Honest outcomes.** ``accepted -> dispatched -> confirmed / no-effect /
  unknown / failed / cancelled`` is the only outcome vocabulary, and only
  ``CONFIRMED`` supports a completion claim. A tool accepting a call is not
  the task succeeding.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class VeloError(RuntimeError):
    """Base class for Velo failures; terminal, never retried blindly."""


class AdapterContractError(VeloError):
    """The adapter was asked to send a call outside its declared contract.

    An argument the tool's schema does not declare is a programming error and
    must stop the call loudly: silently filtering it (the historical adapter's
    behaviour) made an incompatible argument disappear instead of failing.
    """


class JevServiceError(VeloError):
    """JEV request failed; Velo reports it, it never silently swaps models."""


class JevContractError(VeloError):
    """JEV answered outside the decision contract; fail closed."""


class Route(StrEnum):
    """Which of Velo's three routes executed the instruction (section 4)."""

    LOCAL = "local"  # A: fully resolved ordinary command
    JEV = "jev"  # B: interpretation or target selection needed
    PLAN = "plan"  # C: unfamiliar multi-step objective (general reasoning)


class OutcomeState(StrEnum):
    """Explicit action-outcome states (section 8)."""

    ACCEPTED = "accepted"
    DISPATCHED = "dispatched"
    CONFIRMED = "confirmed"
    NO_EFFECT = "no-effect"
    UNKNOWN = "unknown"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def supports_completion(self) -> bool:
        return self is OutcomeState.CONFIRMED


class TargetOwnership(StrEnum):
    """How the task's application target was established (section 2)."""

    REQUESTED = "requested"  # the instruction named this app explicitly
    RESOLVED = "resolved"  # mechanically resolved from the instruction
    OBSERVED = "observed"  # read from the scene this turn
    FOREGROUND = "foreground"  # evidence only: never a substitution


@dataclass(frozen=True)
class AppIdentity:
    """One application the driver can see."""

    name: str
    bundle_id: str = ""
    pid: int | None = None
    running: bool = False
    active: bool = False

    def identity_matches(self, wanted: str) -> bool:
        """``safari`` against "Safari" / ``com.apple.Safari`` — name or bundle."""
        def shape(value: str) -> str:
            text = value.strip().lower()
            if text.endswith(".app"):
                text = text[: -len(".app")]
            return text

        wanted_shape = shape(wanted)
        if not wanted_shape:
            return False
        name_shape = shape(self.name)
        bundle_shape = shape(self.bundle_id)
        return (
            wanted_shape == name_shape
            or wanted_shape == bundle_shape
            or name_shape.endswith(" " + wanted_shape)
            or wanted_shape in name_shape.split()
            or bundle_shape.endswith("." + wanted_shape)
            or bundle_shape.endswith(wanted_shape)
        )


@dataclass(frozen=True)
class Target:
    """The application surface a task is bound to."""

    app: AppIdentity
    window_id: int | None = None
    ownership: TargetOwnership = TargetOwnership.RESOLVED
    #: Task version the target was validated against; a stale target must be
    #: re-validated before any action uses it.
    version: int = 0


@dataclass
class TaskState:
    """One shared task context across all routes (section 7)."""

    instruction: str
    conversation: str = ""
    #: The application name the user said, verbatim — never substituted.
    requested_app: str = ""
    resolved: Target | None = None
    #: Exact dictated payload text, keyed ``user_text_1``, ``user_text_2``…
    #: JEV may select a payload id but never rewrite the value.
    payloads: dict[str, str] = field(default_factory=dict)
    #: Bumped whenever the desktop or the instruction invalidates held state.
    version: int = 1
    cancelled: bool = False
    route: Route = Route.LOCAL
    #: Digest of the most recent observation, for no-progress tracking.
    last_observation_digest: str = ""
    #: Deadline for the whole task (``time.monotonic`` seconds).
    deadline: float | None = None
    max_actions: int = 12
    used_actions: int = 0
    last_outcome: "ActionOutcome | None" = None
    #: Every adapter step registers here; the tracker ends changeless churn.
    tracker: "NoProgressTracker" = field(default_factory=lambda: NoProgressTracker())

    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    def invalidate(self) -> None:
        """Desktop state moved; held observations and targets are stale."""
        self.version += 1

    def check_usable(self) -> None:
        """Raise when the task must not continue (cancelled / expired / over budget)."""
        if self.cancelled:
            raise TaskCancelled("task was cancelled")
        if self.expired():
            raise TaskCancelled("task ran out of time")
        if self.used_actions >= self.max_actions:
            raise TaskCancelled(f"task reached its {self.max_actions}-action budget")


class TaskCancelled(VeloError):
    """A local stop, budget or deadline ended the task before completion."""


@dataclass(frozen=True)
class ActionOutcome:
    """The honest result of one desktop action (section 8)."""

    tool: str
    state: OutcomeState
    evidence: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class ToolReply:
    """What one adapter dispatch produced: model-facing text + typed payload."""

    tool: str
    text: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    ok: bool = True


class PostconditionKind(StrEnum):
    """Postconditions matched to the objective (section 8 table)."""

    APP_RUNNING = "app_running"  # requested app is running with a usable surface
    APP_FOREGROUND = "app_foreground"
    NAVIGATED = "navigated"  # intended window reports the requested destination
    SEARCHED = "searched"  # correct query/search destination observed
    TEXT_IN_FIELD = "text_in_field"  # intended field holds the expected text
    PLAYBACK_STARTED = "playback_started"  # playback state or advancing evidence
    VIEWPORT_CHANGED = "viewport_changed"  # scroll position/content changed


@dataclass(frozen=True)
class Postcondition:
    """The evidence the objective owes before a completion claim."""

    kind: PostconditionKind
    url: str = ""
    query: str = ""
    text: str = ""
    direction: str = ""
    before_digest: str = ""


@dataclass(frozen=True)
class Verification:
    """Result of checking a postcondition against fresh evidence."""

    satisfied: bool
    kind: PostconditionKind
    evidence: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


class ProgressKind(StrEnum):
    """What a step was; the no-progress controller counts across all of them."""

    ACTION = "action"
    OBSERVATION = "observation"
    WAIT = "wait"
    RECOVERY = "recovery"


#: No-progress ceilings (section 8): a small bounded set of distinct attempts,
#: across actions, observations, waits *and* alternating recovery tries.
MAX_STEPS_WITHOUT_CHANGE = 4
MAX_RECOVERY_ATTEMPTS = 2


@dataclass
class NoProgressTracker:
    """Spending steps without observable change must stop early.

    The historical loop's ``OBSERVE``/``WAIT`` branches ran ahead of its
    repeated-mutation check, so identical steps could consume the budget one
    decision at a time. Every step of every route registers here with the
    scene digest it observed. A digest that repeats within the look-back
    window -- immediately, or after alternating screens (A, B, A, B is the
    classic stall the last-digest-only counter evaded; A09/RF-12) --
    increments the no-progress count whatever the step's kind was. Only a
    genuinely new digest outside the window resets the count.
    """

    max_steps_without_change: int = MAX_STEPS_WITHOUT_CHANGE
    max_recovery_attempts: int = MAX_RECOVERY_ATTEMPTS
    steps_without_change: int = 0
    recovery_attempts: int = 0
    _last_digest: str = ""
    _recent: "deque[str]" = field(default_factory=deque, repr=False)

    def register(self, kind: ProgressKind, scene_digest: str) -> bool:
        """Record one step; ``False`` when the run must stop and report.

        ``scene_digest`` is a short stable hash of what observation last saw.
        A digest seen within the no-progress window counts as no progress
        whatever kind of step produced it, so observe/wait/recover cannot
        launder a stall into progress and two alternating screens cannot
        reset each other forever.
        """
        if kind is ProgressKind.RECOVERY:
            self.recovery_attempts += 1
            if self.recovery_attempts > self.max_recovery_attempts:
                return False
        digest = scene_digest or f"<{kind.value}:undigested>"
        window = self.max_steps_without_change
        if not scene_digest:
            # Nothing was observed: a blind step is never progress.
            self.steps_without_change += 1
        elif digest in self._recent:
            self.steps_without_change += 1
        else:
            self.steps_without_change = 0
        self._recent.append(digest)
        while len(self._recent) > window:
            self._recent.popleft()
        self._last_digest = digest
        return self.steps_without_change < window

    @property
    def exhausted(self) -> bool:
        return self.steps_without_change >= self.max_steps_without_change


# -- JEV decision contract (section 5) ---------------------------------------


class CandidateKind(StrEnum):
    """What a candidate asks JEV to choose."""

    RECIPE = "recipe"  # one bounded, validated action sequence
    TARGET = "target"  # which application/surface a recipe should act on


@dataclass(frozen=True)
class Candidate:
    """One mechanically-built option. JEV selects; it never invents these."""

    id: str
    kind: CandidateKind
    description: str
    recipe: str = ""
    #: Recipe arguments, referencing task payload ids — never generated text.
    args: dict[str, str] = field(default_factory=dict)
    target_pid: int | None = None


@dataclass(frozen=True)
class JevDecisionRequest:
    """Everything one JEV request carries, compact and bounded (section 5)."""

    objective: str
    requested_app: str
    target_summary: str
    scene_facts: tuple[str, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    payload_ids: tuple[str, ...] = ()
    last_outcome: str = ""
    reason: str = ""
    task_version: int = 0


class DecisionStatus(StrEnum):
    """The only statuses JEV may return."""

    ACT = "ACT"
    DONE = "DONE"
    ASK_USER = "ASK_USER"
    STOP = "STOP"


@dataclass(frozen=True)
class JevDecision:
    """One validated JEV answer, bound to the task version it applies to."""

    status: DecisionStatus
    selected_id: str = ""
    confidence: float = 0.0
    applies_to_version: int = 0
    need_more_evidence: bool = False
    need_user_choice: bool = False
    latency_ms: int = 0


class JevDecisionService:
    """The interface provider-specific JEV code hides behind (section 5).

    Implementations classify a bounded candidate set; they never generate
    action content, rewrite dictated text, or invent element ids.
    """

    async def decide(self, request: JevDecisionRequest) -> JevDecision:
        raise NotImplementedError


__all__ = [
    "AdapterContractError",
    "ActionOutcome",
    "AppIdentity",
    "Candidate",
    "CandidateKind",
    "DecisionStatus",
    "JevContractError",
    "JevDecision",
    "JevDecisionRequest",
    "JevDecisionService",
    "JevServiceError",
    "MAX_RECOVERY_ATTEMPTS",
    "MAX_STEPS_WITHOUT_CHANGE",
    "NoProgressTracker",
    "OutcomeState",
    "Postcondition",
    "PostconditionKind",
    "ProgressKind",
    "Route",
    "Target",
    "TargetOwnership",
    "TaskCancelled",
    "TaskState",
    "ToolReply",
    "Verification",
    "VeloError",
]
