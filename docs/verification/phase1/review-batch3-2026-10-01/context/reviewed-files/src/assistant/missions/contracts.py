"""Jarvis Phase 1 mission contracts (``jarvis.v1``, file 03 §4).

The typed surface every mission component exchanges. Rules baked in here:

- IDs this process mints (mission/request/execution/approval/event) are
  UUIDs; host-provided conversation ids stay opaque strings.
- Timestamps are UTC epoch milliseconds; durations are nonnegative integer
  milliseconds; money is integer micro-units plus currency, or null.
- Authority records (Scope, BoundedWorkItem, ApprovalRecord, MissionControl)
  use ``extra="forbid"``: an unknown field in a security-sensitive record is
  a rejection, never ignored metadata.
- Text is bounded everywhere; payloads the executor may act on travel by
  reference and digest, never inline in the packet.
- An UNKNOWN effect outcome is a first-class value: uncertainty is never
  folded into success or zero.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1

# -- enums (file 03 §4) -------------------------------------------------------

MissionStatus = Literal[
    "PLANNED", "RUNNING", "WAITING_EXTERNAL", "BLOCKED", "NEEDS_APPROVAL",
    "PAUSED", "VERIFYING", "COMPLETED", "FAILED", "CANCELLED",
]
StepStatus = Literal[
    "PENDING", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED", "SKIPPED",
]
ExecutionStatus = Literal[
    "PENDING", "RUNNING", "COMPLETED", "FAILED", "BLOCKED",
    "NEEDS_CONTROLLER", "NEEDS_HUMAN", "CANCELLED",
]
EffectClass = Literal["READ_ONLY", "REPEATABLE_LOCAL", "EXTERNAL_WRITE", "DESTRUCTIVE"]
EffectOutcome = Literal["NOT_ATTEMPTED", "CONFIRMED", "NO_EFFECT", "UNKNOWN"]
Origin = Literal["typed_final", "voice_final"]
ControlKind = Literal["PAUSE", "RESUME", "CANCEL", "REVISE", "SET_PRIORITY"]

TERMINAL_MISSION_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
NONTERMINAL_MISSION_STATUSES = frozenset(set(MissionStatus.__args__) - TERMINAL_MISSION_STATUSES)  # type: ignore[attr-defined]

#: Failure/escalation catalog (file 03 §12). Unknown categories reject on
#: commands; forward-compatible event viewing may preserve bounded UNKNOWN.
FAILURE_CATEGORIES = frozenset(
    {
        "SCOPE_MISMATCH", "AUTH_REQUIRED", "PERMISSION_DENIED", "APPROVAL_REQUIRED",
        "UNSUPPORTED_ACTION", "STALE_TARGET", "NO_PROGRESS", "BUDGET_EXHAUSTED",
        "DEADLINE", "TRANSPORT_LOST", "UNKNOWN_EFFECT", "VERIFICATION_FAILED",
        "STORAGE_UNAVAILABLE", "USER_TAKEOVER", "CANCELLED",
    }
)

_RECOVERY_DECISIONS = frozenset({"RETRY_SAFE", "REVISE", "ASK_OWNER", "BLOCK", "FAIL"})

_uuid_re = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def new_id() -> str:
    """A fresh UUID4 string for process-minted identities."""
    return str(uuid.uuid4())


def is_uuid(value: str) -> bool:
    return bool(_uuid_re.match(value))


MissionId = Annotated[str, Field(pattern=r"^[0-9a-fA-F-]{36}$")]


def canonical_json(value: Any) -> str:
    """Stable serialization for digests (sorted keys, compact separators)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_fallback
    )


def _fallback(item: Any) -> Any:
    if isinstance(item, (set, frozenset)):
        return sorted(item)
    raise TypeError(f"not JSON serializable: {type(item).__name__}")


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StrictModel(BaseModel):
    """Base for authority records: unknown fields reject."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# -- envelope and scope --------------------------------------------------------


class RequestEnvelope(StrictModel):
    """One finalized request (voice or text), host-derived identity."""

    schema_version: int = SCHEMA_VERSION
    request_id: str
    conversation_id: str
    owner_id: str  # host-derived, never model-supplied
    input_origin: Origin
    input_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=16000)
    submitted_at_ms: int
    mission_id: str | None = None

    @field_validator("request_id", "owner_id")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must be non-empty")
        return value


class Scope(StrictModel):
    """What this mission may touch. Missing identity is UNKNOWN, never wildcard.

    R03/F03: the scope hash is ALWAYS recomputed from the canonical trusted
    contents — a caller-supplied hash is ignored, so copying a hash cannot
    launder a broadened scope past validation.
    """

    owner_id: str
    project_id: str | None = None
    account_ref: str | None = None
    workspace_ref: str | None = None
    allowed_apps: list[str] = Field(default_factory=list)
    allowed_roots: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    permitted_effects: set[EffectClass] = Field(default_factory=set)
    policy_version: str = "jarvis.v1"
    scope_hash: str = ""

    @model_validator(mode="after")
    def _finalize(self) -> Scope:
        object.__setattr__(
            self,
            "scope_hash",
            digest_of(
                {
                    "owner_id": self.owner_id,
                    "project_id": self.project_id,
                    "account_ref": self.account_ref,
                    "workspace_ref": self.workspace_ref,
                    "allowed_apps": sorted(self.allowed_apps),
                    "allowed_roots": sorted(self.allowed_roots),
                    "allowed_origins": sorted(self.allowed_origins),
                    "permitted_effects": sorted(self.permitted_effects),
                    "policy_version": self.policy_version,
                }
            ),
        )
        return self

    def contains(self, other: Scope) -> bool:
        """True when ``other`` grants no authority beyond this scope."""
        if other.owner_id != self.owner_id:
            return False
        if other.account_ref != self.account_ref or other.workspace_ref != self.workspace_ref:
            return False
        if not set(other.allowed_apps) <= set(self.allowed_apps):
            return False
        if not set(other.allowed_roots) <= set(self.allowed_roots):
            return False
        if not set(other.allowed_origins) <= set(self.allowed_origins):
            return False
        return set(other.permitted_effects) <= set(self.permitted_effects)

    def permits_effect(self, effect: EffectClass) -> bool:
        """Destructive effects are denied in P1 regardless of configuration."""
        if effect == "DESTRUCTIVE":
            return False
        return effect in self.permitted_effects


# -- budgets -------------------------------------------------------------------


class BudgetLimits(StrictModel):
    """Every limit is finite and nonnegative; absent paid allowance is zero."""

    max_wall_ms: int = Field(default=900_000, ge=0)
    max_actions: int = Field(default=100, ge=0)
    max_observations: int = Field(default=300, ge=0)
    max_screenshots: int = Field(default=10, ge=0)
    max_deep_calls: int = Field(default=8, ge=0)
    max_jev_calls: int = Field(default=20, ge=0)
    max_retries: int = Field(default=3, ge=0)
    max_replans: int = Field(default=2, ge=0)
    max_no_progress: int = Field(default=4, ge=0)
    # D05: hard ceiling on actual provider transport requests (including
    # graph-internal sub-calls and retries) for ONE Controller invocation.
    max_provider_requests: int = Field(default=16, ge=0)
    max_paid_units: int = Field(default=0, ge=0)
    max_cost_microunits: int | None = None
    currency: str | None = None

    @model_validator(mode="after")
    def _paid_consistency(self) -> BudgetLimits:
        if self.max_cost_microunits is not None and not self.currency:
            raise ValueError("a cost ceiling requires a currency")
        return self


BUDGET_RESOURCES = (
    "actions",
    "observations",
    "screenshots",
    "deep_calls",
    "jev_calls",
    "retries",
    "replans",
    "paid_units",
)


class BudgetUsage(StrictModel):
    """Counters that survive restart; reservations count failed attempts too."""

    reserved: dict[str, int] = Field(default_factory=dict)
    consumed: dict[str, int] = Field(default_factory=dict)
    known_input_tokens: int = 0
    known_output_tokens: int = 0
    known_cache_tokens: int = 0
    known_reasoning_tokens: int = 0
    unknown_usage_calls: int = 0
    known_cost_microunits: int | None = None
    external_wait_ms: int = Field(default=0, ge=0)
    active_ms: int = Field(default=0, ge=0)

    def total(self, resource: str) -> int:
        return self.reserved.get(resource, 0) + self.consumed.get(resource, 0)

    def within(self, limits: BudgetLimits, resource: str) -> bool:
        ceiling = getattr(limits, f"max_{resource}", None)
        if ceiling is None:
            return True
        return self.total(resource) < ceiling


# -- evidence and checks -------------------------------------------------------


class EvidenceRef(StrictModel):
    """A pointer to sanitized evidence. Raw screenshots/secrets never travel."""

    evidence_id: str
    mission_id: str
    execution_id: str | None = None
    kind: str = Field(min_length=1, max_length=64)
    relative_path: str | None = None
    inline_facts: dict[str, Any] | None = None
    sha256: str = Field(min_length=64, max_length=64)
    captured_at_ms: int
    producer: str = ""
    producer_version: str = ""
    sensitivity: Literal["PUBLIC", "INTERNAL", "SENSITIVE", "WITHHELD"] = "INTERNAL"
    redaction_version: str = "v1"
    redaction_status: Literal["SAFE", "WITHHELD"] = "SAFE"
    expires_at_ms: int | None = None

    @model_validator(mode="after")
    def _one_locator(self) -> EvidenceRef:
        if self.redaction_status == "SAFE" and not self.relative_path and self.inline_facts is None:
            raise ValueError("SAFE evidence needs a relative_path or inline facts")
        if self.redaction_status == "WITHHELD" and (self.relative_path or self.inline_facts):
            raise ValueError("WITHHELD evidence carries no content locator")
        return self


class ScopeObservation(StrictModel):
    """What the executor actually observed about the target surface."""

    app_bundle: str = ""
    pid: int | None = Field(default=None, ge=1)
    window_id: int | None = Field(default=None, ge=1)
    account_ref: str | None = None
    workspace_ref: str | None = None
    origin: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    captured_at_ms: int
    driver_generation: str = ""
    target_version: int = Field(default=0, ge=0)


class CheckSpec(StrictModel):
    """One acceptance check from the trusted catalog; no generated verifier code."""

    check_id: str = Field(min_length=1, max_length=128)
    verifier_id: str = Field(min_length=1, max_length=128)
    verifier_version: str = Field(min_length=1, max_length=32)
    expected: dict[str, Any] = Field(default_factory=dict)
    target_scope_hash: str = ""
    required: bool = True

    @field_validator("expected")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(canonical_json(value).encode("utf-8")) > 4096:
            raise ValueError("CheckSpec.expected is bounded to 4 KiB")
        return value


class CheckResult(StrictModel):
    """A verifier's answer. The executor cannot manufacture these into the store."""

    check_id: str
    passed: bool
    evidence_ids: list[str] = Field(default_factory=list)
    checked_at_ms: int
    verifier_id: str
    verifier_version: str
    reason: str = Field(default="", max_length=1000)


# -- plan, steps, mission -------------------------------------------------------


class StepSpec(StrictModel):
    """One semantic step. DAG: dependencies resolve, no cycles, <=20 steps."""

    step_id: str = Field(min_length=1, max_length=128)
    ordinal: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=2000)
    dependencies: list[str] = Field(default_factory=list)
    executor: Literal["velo"] = "velo"
    recipe_id: str = Field(min_length=1, max_length=128)
    #: Bounded mechanical recipe arguments (e.g. app_name), plan data only.
    #: User-dictated text never travels here -- it goes through payload_refs.
    recipe_args: dict[str, str] = Field(default_factory=dict)
    payload_refs: list[str] = Field(default_factory=list)
    #: Digests of the referenced payloads, registered at plan time so the
    #: executor's action scope can bind them exactly (R06).
    payload_digests: list[str] = Field(default_factory=list)
    checks: list[CheckSpec] = Field(default_factory=list)
    scope: Scope
    budget: BudgetLimits
    effect_class: EffectClass = "READ_ONLY"
    escalation_conditions: list[str] = Field(default_factory=list)
    optional: bool = False

    @field_validator("escalation_conditions")
    @classmethod
    def _known_escalations(cls, value: list[str]) -> list[str]:
        unknown = [entry for entry in value if entry not in FAILURE_CATEGORIES]
        if unknown:
            raise ValueError(f"unknown escalation conditions: {unknown}")
        return value


class MissionRecord(StrictModel):
    """The durable mission: append-only goal revisions, CAS plan/epoch."""

    schema_version: int = SCHEMA_VERSION
    mission_id: str
    request_id: str
    owner_id: str
    conversation_id: str
    project_id: str | None = None
    original_goal: str = Field(min_length=1, max_length=16000)
    scope: Scope
    success_criteria: list[CheckSpec] = Field(default_factory=list)
    plan_version: int = Field(default=0, ge=0)
    control_epoch: int = Field(default=1, ge=1)
    status: MissionStatus = "PLANNED"
    steps: list[StepSpec] = Field(default_factory=list)
    resume_cursor: str | None = None
    budget_limits: BudgetLimits = Field(default_factory=BudgetLimits)
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    approval_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    # D02/D10: the exact per-step approvals the host UI owes right now,
    # projected from durable step state (never a model's claim).
    pending_approvals: list[PendingApprovalDigest] = Field(default_factory=list)
    priority: int = Field(default=4, ge=0, le=9)
    created_at_ms: int
    updated_at_ms: int

    @model_validator(mode="after")
    def _dag_valid(self) -> MissionRecord:
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step_id in plan")
        if len(self.steps) > 20:
            raise ValueError("a mission plan is bounded to 20 steps in Phase 1")
        known = set(ids)
        for step in self.steps:
            for dep in step.dependencies:
                if dep not in known:
                    raise ValueError(f"step {step.step_id} depends on unknown step {dep}")
            if step.step_id in step.dependencies:
                raise ValueError(f"step {step.step_id} depends on itself")
        # Cycle check by DFS over the dependency edges.
        seen: dict[str, int] = {}

        def visit(node: str) -> None:
            state = seen.get(node, 0)
            if state == 1:
                raise ValueError("dependency cycle in plan")
            if state == 2:
                return
            seen[node] = 1
            step = next(s for s in self.steps if s.step_id == node)
            for dep in step.dependencies:
                visit(dep)
            seen[node] = 2

        for step in self.steps:
            visit(step.step_id)
        return self


# -- bounded work packet and result ---------------------------------------------


class ActionScopeRecord(StrictModel):
    """``allowed_action_scope`` (file 03 §12): no wildcard tool ids, ever."""

    tool_ids: list[str] = Field(min_length=1)
    target_scope_hash: str
    payload_digests: list[str] = Field(default_factory=list)
    permitted_effects: set[EffectClass] = Field(default_factory=set)

    @field_validator("tool_ids")
    @classmethod
    def _no_wildcards(cls, value: list[str]) -> list[str]:
        for tool_id in value:
            if not tool_id or tool_id in {"*", "**", ""} or "*" in tool_id or "?" in tool_id:
                raise ValueError(f"wildcard tool id rejected: {tool_id!r}")
        if len(value) != len(set(value)):
            raise ValueError("duplicate tool ids in allowed_action_scope")
        return value


class EvidenceRequirements(StrictModel):
    """``evidence_requirements`` (file 03 §12)."""

    required_check_ids: list[str] = Field(default_factory=list)
    required_artifact_kinds: list[str] = Field(default_factory=list)
    max_age_ms: int = Field(default=60_000, ge=0)
    allow_sanitized_image: bool = False


class BoundedWorkItem(StrictModel):
    """The only packet the executor sees. Total <=16 KiB serialized."""

    schema_version: int = SCHEMA_VERSION
    mission_id: str
    plan_version: int = Field(ge=1)
    control_epoch: int = Field(ge=1)
    step_id: str
    execution_id: str
    attempt: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=2000)
    minimal_context: str = Field(default="", max_length=4096)
    expected_scope: Scope
    preconditions: list[CheckSpec] = Field(default_factory=list)
    allowed_action_scope: ActionScopeRecord
    recipe_id: str = Field(min_length=1, max_length=128)
    recipe_args: dict[str, str] = Field(default_factory=dict)
    payload_refs: list[str] = Field(default_factory=list)
    expected_postconditions: list[CheckSpec] = Field(default_factory=list)
    evidence_requirements: EvidenceRequirements = Field(default_factory=EvidenceRequirements)
    deadline_at_ms: int
    budget: BudgetLimits
    effect_class: EffectClass = "READ_ONLY"
    deduplication_key: str = ""
    escalation_conditions: list[str] = Field(default_factory=list)
    driver_generation: str = ""
    lease_fence: str = ""

    def serialized_size(self) -> int:
        return len(canonical_json(self.model_dump()).encode("utf-8"))

    @field_validator("recipe_args")
    @classmethod
    def _bounded_recipe_args(cls, value: dict[str, str]) -> dict[str, str]:
        if len(canonical_json(value).encode("utf-8")) > 1024:
            raise ValueError("recipe_args are bounded to 1 KiB of plan data")
        return value

    @model_validator(mode="after")
    def _packet_size(self) -> BoundedWorkItem:
        size = self.serialized_size()
        if size > 16 * 1024:
            raise ValueError(f"BoundedWorkItem packet is {size} bytes; cap is 16 KiB")
        return self


class StepResult(StrictModel):
    """What the executor reports. COMPLETED means this unit's checks passed."""

    schema_version: int = SCHEMA_VERSION
    mission_id: str
    plan_version: int = Field(ge=1)
    control_epoch: int = Field(ge=1)
    step_id: str
    execution_id: str
    attempt: int = Field(ge=1)
    status: ExecutionStatus
    observed_scope: ScopeObservation | None = None
    postconditions: list[CheckResult] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    external_operation_ids: list[str] = Field(default_factory=list)
    failure_category: str | None = None
    uncertainty: str = Field(default="", max_length=512)
    effect_outcome: EffectOutcome = "NOT_ATTEMPTED"
    elapsed_ms: int = Field(default=0, ge=0)
    usage: BudgetUsage = Field(default_factory=BudgetUsage)
    suggested_next_action: str | None = Field(default=None, max_length=512)
    # D02: the exact approval a refused dispatch owes, persisted with the
    # BLOCKED step so release/consumption can match it exactly.
    pending_approval: PendingApprovalDigest | None = None
    # D02: an external retry hint (e.g. a rate limiter's Retry-After). When
    # present on an escalation the service records a durable external wait
    # with this deadline instead of pausing for an owner decision.
    retry_after_ms: int | None = Field(default=None, ge=0, le=3_600_000)

    @model_validator(mode="after")
    def _consistency(self) -> StepResult:
        if self.failure_category is not None and self.failure_category not in FAILURE_CATEGORIES:
            raise ValueError(f"unknown failure_category: {self.failure_category}")
        if self.status in {"FAILED", "BLOCKED"} and not self.failure_category:
            raise ValueError("FAILED/BLOCKED results must name a failure_category")
        if self.status == "COMPLETED" and self.effect_outcome == "UNKNOWN":
            raise ValueError("COMPLETED with UNKNOWN effect is not a reportable unit success")
        return self


class ExceptionPacket(StrictModel):
    """A compact exception for the Controller. Full traces never reach Deep."""

    mission_id: str
    plan_version: int = Field(ge=1)
    control_epoch: int = Field(ge=1)
    step_id: str
    execution_id: str
    attempt: int = Field(ge=1)
    category: str
    expected_summary: str = Field(default="", max_length=512)
    observed_summary: str = Field(default="", max_length=512)
    last_safe_events: list[str] = Field(default_factory=list, max_length=3)
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)
    attempted_recoveries: list[str] = Field(default_factory=list)
    remaining_budget: BudgetUsage = Field(default_factory=BudgetUsage)
    unresolved_effects: list[str] = Field(default_factory=list)
    allowed_decisions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bounded(self) -> ExceptionPacket:
        if self.category not in FAILURE_CATEGORIES:
            raise ValueError(f"unknown exception category: {self.category}")
        packet = canonical_json(self.model_dump())
        if len(packet.encode("utf-8")) > 8 * 1024:
            raise ValueError("ExceptionPacket is bounded to 8 KiB")
        return self


# -- approvals and controls ------------------------------------------------------


class PendingApprovalDigest(StrictModel):
    """The exact action a blocked step owes an owner approval for.

    Persisted when a dispatch is refused with APPROVAL_REQUIRED so the host
    UI can mint an approval bound to THIS digest, plan version and control
    epoch — and so a release that does not match the digest is impossible.
    """

    step_id: str
    tool: str
    action_digest: str = Field(min_length=16, max_length=128)
    plan_version: int = Field(ge=1)
    control_epoch: int = Field(ge=1)
    target_ref: str | None = None


class ApprovalRecord(StrictModel):
    """Minted only by the trusted host; a model's 'approved' string is invalid."""

    approval_id: str
    mission_id: str
    plan_version: int = Field(ge=1)
    control_epoch: int = Field(ge=1)
    action_digest: str = Field(min_length=16, max_length=128)
    scope_hash: str
    target_ref: str | None = None
    account_ref: str | None = None
    workspace_ref: str | None = None
    effect_class: EffectClass
    maximum_units: int = Field(default=1, ge=1)
    issued_by: Literal["local_owner"] = "local_owner"
    issued_at_ms: int
    expires_at_ms: int
    single_use: bool = True
    consumed_at_ms: int | None = None

    @model_validator(mode="after")
    def _expiry_order(self) -> ApprovalRecord:
        if self.expires_at_ms <= self.issued_at_ms:
            raise ValueError("approval must expire after it is issued")
        return self


class MissionControl(StrictModel):
    """Compare-and-swap control command; stale commands reject."""

    control_id: str
    mission_id: str
    expected_plan_version: int = Field(ge=0)
    expected_control_epoch: int = Field(ge=1)
    kind: ControlKind
    reason: str = Field(default="", max_length=1000)
    revision_request: str | None = Field(default=None, max_length=16000)
    priority: int | None = Field(default=None, ge=0, le=9)


# -- controller-side value types --------------------------------------------------


class PlanProposal(BaseModel):
    """What Deep proposes. It cannot add authority or budgets."""

    steps: list[StepSpec]
    success_criteria: list[CheckSpec] = Field(default_factory=list)
    explanation: str = Field(default="", max_length=4000)


class RecoveryDecision(BaseModel):
    """A bounded recovery answer from the Controller."""

    decision: Literal["RETRY_SAFE", "REVISE", "ASK_OWNER", "BLOCK", "FAIL"]
    proposed_change: str = Field(default="", max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)
    reason: str = Field(default="", max_length=1000)


class FinalReview(BaseModel):
    """The Controller's final summary. It cannot set a terminal status."""

    supported_summary: str = Field(default="", max_length=4000)
    acceptance_check_ids: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list, max_length=10)


class MissionSnapshot(BaseModel):
    """The safe bounded projection of a MissionRecord for Controller views."""

    mission_id: str
    status: MissionStatus
    plan_version: int
    control_epoch: int
    original_goal: str
    steps: list[StepSpec] = Field(default_factory=list)
    remaining_budget: BudgetUsage = Field(default_factory=BudgetUsage)
    unresolved_effects: list[str] = Field(default_factory=list)
    recent_evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class ActionIntent(StrictModel):
    """One concrete action the dispatcher wants to take."""

    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    args_digest: str = ""
    target_ref: str | None = None
    effect_class: EffectClass = "READ_ONLY"
    operation_key: str = ""

    @model_validator(mode="after")
    def _digest(self) -> ActionIntent:
        if not self.args_digest:
            object.__setattr__(self, "args_digest", text_digest(canonical_json(self.args)))
        return self


class ActionPermit(StrictModel):
    """Opaque single-use authorization bound to execution, fence and digest."""

    permit_id: str
    execution_id: str
    lease_fence: str
    action_digest: str
    effect_class: EffectClass
    issued_at_ms: int
    expires_at_ms: int
    used_at_ms: int | None = None


class BudgetCharge(StrictModel):
    """One resource charge request against a mission's budget."""

    resource: str
    amount: int = Field(default=1, ge=1)
    call_key: str = ""

    @model_validator(mode="after")
    def _known_resource(self) -> BudgetCharge:
        if self.resource not in BUDGET_RESOURCES:
            raise ValueError(
                f"unknown budget resource {self.resource!r}; known: {BUDGET_RESOURCES}"
            )
        return self


class BudgetReservation(StrictModel):
    """A durable reservation; consumed or released, never silently dropped."""

    reservation_id: str
    mission_id: str
    resource: str
    amount: int
    call_key: str = ""
    created_at_ms: int
    consumed: bool = False
    released: bool = False


class EvidenceCandidate(StrictModel):
    """Raw ephemeral evidence before sanitization; never persisted as-is."""

    kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    sensitivity_origin: str = Field(default="", max_length=256)
    captured_at_ms: int


class CancellationToken:
    """Per-execution cancellation; local, cheap, and epoch-aware."""

    __slots__ = ("is_cancelled", "epoch")

    def __init__(self, epoch: int = 1) -> None:
        self.is_cancelled: bool = False
        self.epoch: int = epoch

    def cancel(self) -> None:
        self.is_cancelled = True


# -- trace events -----------------------------------------------------------------


class TraceEvent(StrictModel):
    """Append-only trace record with hash chaining for tamper detection.

    Same-user/root rewriting is out of threat-model protection; the chain
    detects accidental or partial modification and is not advertised as
    cryptographic immutability.
    """

    schema_version: int = SCHEMA_VERSION
    event_id: str
    sequence: int = Field(ge=1)
    trace_id: str
    mission_id: str
    plan_version: int = Field(ge=0)
    control_epoch: int = Field(ge=1)
    step_id: str | None = None
    execution_id: str | None = None
    kind: str = Field(min_length=1, max_length=64)
    safe_payload: dict[str, Any] = Field(default_factory=dict)
    expected_state: dict[str, Any] | None = None
    observed_state: dict[str, Any] | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    component_versions: dict[str, str] = Field(default_factory=dict)
    skill_versions: dict[str, str] = Field(default_factory=dict)
    occurred_at_ms: int
    previous_hash: str = ""
    event_hash: str = ""

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        allowed = {
            "request", "plan", "action", "outcome", "correction", "recovery",
            "approval", "control", "budget", "verification", "observer",
            # C07: deletion tombstones are trace events too (file 03 §10 —
            # deletion writes a tombstone; the chain detects any other edit).
            "retention",
        }
        if value not in allowed:
            raise ValueError(f"unknown trace kind {value!r}; known: {sorted(allowed)}")
        return value

    def compute_hash(self) -> str:
        body = self.model_dump(exclude={"event_hash"})
        return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def _chain(self) -> TraceEvent:
        if not self.event_hash:
            object.__setattr__(self, "event_hash", self.compute_hash())
        return self


class ObserverRecommendation(StrictModel):
    """Observation-only output. No executable code, activation, or authority."""

    recommendation_id: str
    pattern_id: str = Field(min_length=1, max_length=128)
    mission_ids: list[str] = Field(default_factory=list)
    supporting_event_ids: list[str] = Field(default_factory=list)
    hypothesis: str = Field(default="", max_length=4000)
    confidence: float = Field(ge=0.0, le=1.0)
    scope: str = Field(default="", max_length=512)
    created_at_ms: int
    status: Literal["OBSERVATION_ONLY"] = "OBSERVATION_ONLY"

    @model_validator(mode="after")
    def _no_authority(self) -> ObserverRecommendation:
        # Guard against embedding an activation instruction in the free-text
        # fields: any known activation verb pattern is rejected outright.
        lowered = (self.hypothesis + " " + self.scope).lower()
        forbidden = ("activate ", "enable rsi", "run experiment", "grant ", "execute ")
        for marker in forbidden:
            if marker in lowered:
                raise ValueError(f"observer recommendations may not request authority: {marker!r}")
        return self


class WorkerStatus(StrictModel):
    """Compatibility semantics only; no coding supervisor exists in Phase 1."""

    worker_id: str
    mission_id: str
    status: Literal[
        "WORKING", "WAITING_INPUT", "WAITING_EXTERNAL", "BLOCKED",
        "RATE_LIMITED", "CRASHED", "COMPLETED", "UNKNOWN",
    ]
    evidence_ids: list[str] = Field(default_factory=list)
    updated_at_ms: int


__all__ = [
    "ActionIntent",
    "ActionPermit",
    "ActionScopeRecord",
    "ApprovalRecord",
    "BudgetCharge",
    "BudgetLimits",
    "BudgetReservation",
    "BudgetUsage",
    "BoundedWorkItem",
    "BUDGET_RESOURCES",
    "CancellationToken",
    "CheckResult",
    "CheckSpec",
    "EffectClass",
    "EffectOutcome",
    "EvidenceCandidate",
    "EvidenceRef",
    "EvidenceRequirements",
    "ExecutionStatus",
    "FAILURE_CATEGORIES",
    "FinalReview",
    "MissionControl",
    "MissionId",
    "MissionRecord",
    "MissionSnapshot",
    "MissionStatus",
    "ObserverRecommendation",
    "Origin",
    "PlanProposal",
    "RecoveryDecision",
    "RequestEnvelope",
    "Scope",
    "ScopeObservation",
    "SCHEMA_VERSION",
    "StepResult",
    "StepSpec",
    "StepStatus",
    "TERMINAL_MISSION_STATUSES",
    "TraceEvent",
    "WorkerStatus",
    "canonical_json",
    "digest_of",
    "new_id",
    "text_digest",
]
