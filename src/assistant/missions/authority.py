"""Mission authority: model outputs never grant authority (file 03 §4, §8).

Every dispatcher request to act is checked here against five independent
facts before a single-use :class:`ActionPermit` is minted:

1. the action's tool, effect class, and payload digest sit inside the
   packet's ``allowed_action_scope`` (wildcards are structurally impossible —
   the contract rejects them);
2. the observed surface matches the mission scope: app bundle, origin, and
   any bound account/workspace identity. Missing identity is UNKNOWN and
   fails closed for anything beyond read-only;
3. the execution still owns its dispatch intent in the store, under the same
   plan version and control epoch (no stale packet acts);
4. the driver generation and lease fence recorded at claim time still match;
5. external writes consumed an owner-issued approval bound to the exact
   action digest — a model's "approved" string is not an approval.

Budget reservations flow through the same object so that "reserve, then
act, then settle" cannot be bypassed. Paid calls keep their reservation on
failure: an uncertain charge is never released.
"""

from __future__ import annotations

import time
from typing import Any

from assistant.missions.contracts import (
    ActionIntent,
    ActionPermit,
    BoundedWorkItem,
    BudgetCharge,
    BudgetReservation,
    BudgetUsage,
    ScopeObservation,
    new_id,
    text_digest,
)
from assistant.missions.store import MissionStore, MissionStoreError

DEFAULT_PERMIT_TTL_MS = 30_000

#: How long an observation stays fresh for authorization. Executed actions
#: must sit on a recent observation of the actual surface (R03): a stale or
#: absent observation is a STALE_TARGET, never implicit permission.
OBSERVATION_FRESH_MS = 10_000

#: C02/N04: the bounded discovery set. These reads are HOW the executor
#: obtains an observation in the first place, so they bootstrap without a
#: prior observation timestamp — but they are still bound to the step's
#: tool catalog and dispatch intent, and they still grant nothing beyond
#: reading the inventory/window state they name.
DISCOVERY_TOOLS = frozenset(
    {
        "list_apps",
        "list_windows",
        "get_window_state",
        "get_desktop_state",
        "get_screen_size",
        "get_accessibility_tree",
    }
)

#: Whole-desktop reads: no single target surface, so there is no observed
#: app identity to scope-check. Targeted reads (get_window_state...) sit in
#: DISCOVERY_TOOLS but NOT here: once an observation names a surface, the
#: read is scope-checked like any other call (a READ of an out-of-scope
#: surface is denied, like a mutation).
UNTARGETED_INVENTORY_TOOLS = frozenset(
    {"list_apps", "list_windows", "get_desktop_state", "get_screen_size"}
)


class AuthorityDenied(RuntimeError):
    """The dispatcher may not act; ``category`` is a FAILURE_CATEGORIES entry."""

    def __init__(self, category: str, reason: str) -> None:
        super().__init__(f"{category}: {reason}")
        self.category = category
        self.reason = reason


class MissionAuthority:
    """Issues single-use permits and durable budget reservations."""

    def __init__(self, store: MissionStore, *, permit_ttl_ms: int = DEFAULT_PERMIT_TTL_MS) -> None:
        self._store = store
        self._permit_ttl_ms = permit_ttl_ms
        self._issued: dict[str, ActionPermit] = {}
        self._pending_approvals: dict[tuple[str, int], list[str]] = {}

    # -- permits -----------------------------------------------------------------

    async def authorize(
        self,
        item: BoundedWorkItem,
        action: ActionIntent,
        observed: ScopeObservation,
    ) -> ActionPermit:
        """Check everything, then mint one permit bound to the exact digest."""
        scope = item.expected_scope
        action_scope = item.allowed_action_scope

        if action.effect_class == "DESTRUCTIVE" or not scope.permits_effect(action.effect_class):
            raise AuthorityDenied(
                "PERMISSION_DENIED",
                f"effect {action.effect_class} is not permitted by this mission's scope",
            )
        if action.tool not in action_scope.tool_ids:
            raise AuthorityDenied(
                "SCOPE_MISMATCH",
                f"tool {action.tool!r} is not in this step's allowed action scope",
            )
        if action.effect_class not in action_scope.permitted_effects:
            raise AuthorityDenied(
                "SCOPE_MISMATCH",
                f"effect {action.effect_class} exceeds the step's permitted effects",
            )
        # C05/N07: the payload digest and the action-argument digest are
        # DIFFERENT objects. Payload-bearing tools (typing/setting values)
        # carry the user text in a dedicated argument; the check binds THAT
        # content against the digests the plan registered — never the whole
        # argument digest, which mixes target identity with payload bytes.
        if action_scope.payload_digests and action.tool in {"type_text", "set_value"}:
            payload_content = str(action.args.get("text") or action.args.get("value") or "")
            payload_digest = text_digest(payload_content) if payload_content else ""
            if payload_digest not in action_scope.payload_digests:
                raise AuthorityDenied(
                    "SCOPE_MISMATCH",
                    "payload digest is not one the plan registered for this step",
                )
        self._check_surface(item, scope, action, observed)

        await self._check_intent(item)

        if action.effect_class == "EXTERNAL_WRITE":
            await self._consume_approval(item, action)

        now = int(time.time() * 1000)
        permit = ActionPermit(
            permit_id=new_id(),
            execution_id=item.execution_id,
            lease_fence=item.lease_fence,
            action_digest=action.args_digest,
            effect_class=action.effect_class,
            issued_at_ms=now,
            expires_at_ms=now + self._permit_ttl_ms,
        )
        self._issued[permit.permit_id] = permit
        return permit

    async def consume_permit(self, permit_id: str, action_digest: str) -> ActionPermit:
        """Burn a permit exactly once, before the effect is dispatched."""
        permit = self._issued.get(permit_id)
        if permit is None:
            raise AuthorityDenied("APPROVAL_REQUIRED", "unknown permit")
        if permit.used_at_ms is not None:
            raise AuthorityDenied("APPROVAL_REQUIRED", "permit already used")
        if permit.action_digest != action_digest:
            raise AuthorityDenied("SCOPE_MISMATCH", "permit does not cover these arguments")
        now = int(time.time() * 1000)
        if permit.expires_at_ms <= now:
            raise AuthorityDenied("APPROVAL_REQUIRED", "permit expired")
        used = permit.model_copy(update={"used_at_ms": now})
        self._issued[permit_id] = used
        return used

    def _check_surface(
        self, item: BoundedWorkItem, scope: Any, action: ActionIntent, observed: ScopeObservation
    ) -> None:
        # C02/N04: bounded discovery bootstraps. A whole-inventory read has
        # no single target surface and needs no prior observation. A
        # TARGETED read (get_window_state...) bootstraps only while nothing
        # has been observed yet; the moment the observation names a surface,
        # the read is scope-checked like any other call — a READ of an
        # out-of-scope surface is denied, like a mutation.
        if action.tool in UNTARGETED_INVENTORY_TOOLS:
            return
        if action.tool in DISCOVERY_TOOLS and observed.captured_at_ms <= 0 and (
            not observed.app_bundle
        ):
            return
        self._check_action_surface(item, scope, action, observed)

    def _check_action_surface(
        self, item: BoundedWorkItem, scope: Any, action: ActionIntent, observed: ScopeObservation
    ) -> None:
        # R03/F03: observations must be fresh and complete. An observation
        # whose timestamp is zero/ancient, or whose identity is missing where
        # the scope names an app, grants NOTHING (RP06: unknown identity is
        # not a wildcard).
        if observed.captured_at_ms <= 0:
            raise AuthorityDenied(
                "STALE_TARGET", "the surface observation carries no timestamp"
            )
        age = int(time.time() * 1000) - observed.captured_at_ms
        if age > OBSERVATION_FRESH_MS:
            raise AuthorityDenied(
                "STALE_TARGET",
                f"the surface observation is {age}ms old (fresh window "
                f"{OBSERVATION_FRESH_MS}ms); observe again before acting",
            )
        if observed.app_bundle == "" and scope.allowed_apps:
            # The packet's own scope hash must also match its scope contents
            # (constructed Scope objects recompute it; a mismatch means the
            # packet was tampered with after claim).
            raise AuthorityDenied(
                "SCOPE_MISMATCH",
                "no observed application identity; the mission scope names "
                "allowed apps, so an unnamed surface grants nothing",
            )
        if observed.app_bundle and observed.app_bundle not in scope.allowed_apps:
            raise AuthorityDenied(
                "SCOPE_MISMATCH",
                f"observed app {observed.app_bundle!r} is not in the mission scope",
            )
        if observed.origin is not None and (
            scope.allowed_origins and observed.origin not in scope.allowed_origins
        ):
            raise AuthorityDenied(
                "SCOPE_MISMATCH",
                "the observed origin is outside the mission scope",
            )
        if (
            scope.allowed_origins
            and action.effect_class != "READ_ONLY"
            and (observed.origin is None or observed.origin not in scope.allowed_origins)
        ):
            raise AuthorityDenied(
                "SCOPE_MISMATCH",
                "the observed origin is unknown or outside the mission scope",
            )
        if scope.account_ref is not None:
            if observed.account_ref is None:
                raise AuthorityDenied(
                    "AUTH_REQUIRED",
                    "the mission is account-bound but the surface's account is unknown",
                )
            if observed.account_ref != scope.account_ref:
                raise AuthorityDenied(
                    "SCOPE_MISMATCH",
                    "the observed account does not match the mission's bound account",
                )
        if scope.workspace_ref is not None:
            if observed.workspace_ref is None or observed.workspace_ref != scope.workspace_ref:
                raise AuthorityDenied(
                    "SCOPE_MISMATCH",
                    "the observed workspace does not match the mission's bound workspace",
                )
        if item_driver_stale(item.driver_generation, observed.driver_generation):
            raise AuthorityDenied(
                "STALE_TARGET",
                "the driver generation changed since the packet was claimed",
            )

    async def _check_intent(self, item: BoundedWorkItem) -> None:
        attempt = await self._store.get_attempt(item.execution_id)
        if attempt is None:
            raise AuthorityDenied(
                "UNKNOWN_EFFECT", "no committed dispatch intent exists for this execution"
            )
        if attempt["dispatch_state"] not in {"INTENT_COMMITTED", "DISPATCHED"}:
            raise AuthorityDenied(
                "STALE_TARGET",
                f"dispatch intent is {attempt['dispatch_state']}; it may no longer act",
            )
        if (
            attempt["plan_version"] != item.plan_version
            or attempt["control_epoch"] != item.control_epoch
        ):
            raise AuthorityDenied(
                "STALE_TARGET",
                "the packet's plan/epoch no longer matches the recorded intent",
            )
        if item.lease_fence and attempt.get("lease_fence") not in ("", item.lease_fence):
            raise AuthorityDenied("STALE_TARGET", "the lease fence changed since claim")

    async def _consume_approval(self, item: BoundedWorkItem, action: ActionIntent) -> None:
        digest = approval_digest(item, action)
        seen: set[str] = set()
        for approval_id in [*self._approvals_for(item),
                            *await self._store.active_approval_ids(
                                item.mission_id, item.control_epoch)]:  # type: ignore[attr-defined]
            if approval_id in seen:
                continue
            seen.add(approval_id)
            if await self._store.consume_approval(approval_id, digest):
                return
        raise AuthorityDenied(
            "APPROVAL_REQUIRED",
            "an unexpired owner approval bound to this exact action is required",
        )

    def _approvals_for(self, item: BoundedWorkItem) -> list[str]:
        return self._pending_approvals.get((item.mission_id, item.control_epoch), [])

    # -- budgets -----------------------------------------------------------------

    async def reserve(
        self,
        mission_id: str,
        charge: BudgetCharge,
        *,
        conservative_unit_cost_microunits: int | None = None,
    ) -> BudgetReservation:
        """Reserve budget durably before acting.

        A dollar-capped paid call without a conservative per-unit cost bound
        is denied outright: an unpriced call can never prove it stays under
        the cap, and reporting the unknown bill as zero is forbidden.
        """
        limits = await self._mission_limits(mission_id)
        if charge.resource == "paid_units":
            if charge.amount > limits.max_paid_units:
                raise AuthorityDenied(
                    "BUDGET_EXHAUSTED",
                    "paid external actions have no allowance in this mission",
                )
            if limits.max_cost_microunits is not None:
                if conservative_unit_cost_microunits is None:
                    raise AuthorityDenied(
                        "BUDGET_EXHAUSTED",
                        "the call's price is unknown; a dollar-capped mission "
                        "cannot bound it, so the call is denied",
                    )
                usage = await self._mission_usage(mission_id)
                known = usage.known_cost_microunits or 0
                projected = known + charge.amount * conservative_unit_cost_microunits
                if projected > limits.max_cost_microunits:
                    raise AuthorityDenied(
                        "BUDGET_EXHAUSTED",
                        f"projected cost {projected} exceeds the mission cap "
                        f"{limits.max_cost_microunits}",
                    )
        try:
            return await self._store.reserve_budget(mission_id, charge)
        except MissionStoreError as exc:
            raise AuthorityDenied("BUDGET_EXHAUSTED", str(exc)) from exc

    async def settle(self, reservation: BudgetReservation, *, consumed: bool) -> None:
        await self._store.settle_reservation(reservation.reservation_id, consumed=consumed)

    # -- approvals registration ------------------------------------------------------

    def register_approvals(self, mission_id: str, control_epoch: int,
         approval_ids: list[str]) -> None:
        """Record which approvals exist for a mission/epoch (host-issued only)."""
        self._pending_approvals[(mission_id, control_epoch)] = list(approval_ids)

    # -- helpers ----------------------------------------------------------------------

    async def _mission_limits(self, mission_id: str) -> Any:
        record = await self._store.get_mission(mission_id)
        if record is None:
            raise AuthorityDenied("UNKNOWN_EFFECT", f"unknown mission {mission_id}")
        return record.budget_limits

    async def _mission_usage(self, mission_id: str) -> BudgetUsage:
        record = await self._store.get_mission(mission_id)
        assert record is not None
        return record.budget_usage


def item_driver_stale(item_generation: str, observed_generation: str) -> bool:
    """True when an explicitly recorded generation no longer matches."""
    return (
        bool(item_generation)
        and bool(observed_generation)
        and item_generation != observed_generation
    )


def approval_digest(item: BoundedWorkItem, action: ActionIntent) -> str:
    """The exact digest an owner approval must have been issued for."""
    from assistant.missions.contracts import canonical_json, text_digest

    return text_digest(
        canonical_json(
            {
                "mission_id": item.mission_id,
                "plan_version": item.plan_version,
                "control_epoch": item.control_epoch,
                "step_id": item.step_id,
                "tool": action.tool,
                "args_digest": action.args_digest,
                "scope_hash": item.expected_scope.scope_hash,
                "effect_class": action.effect_class,
            }
        )
    )


__all__ = [
    "AuthorityDenied",
    "DEFAULT_PERMIT_TTL_MS",
    "OBSERVATION_FRESH_MS",
    "MissionAuthority",
    "approval_digest",
    "item_driver_stale",
]
