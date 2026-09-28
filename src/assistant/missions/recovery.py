"""Mission recovery: restart reconciliation never repeats uncertain effects.

After a crash or restart, attempts left in RECONCILING own a committed
dispatch intent whose effect state is unknown. The reconciler decides, per
attempt, one of three honest answers (file 03 §7):

- ``CONFIRMED``   -- an injected probe observed the effect actually landed;
- ``NO_EFFECT``   -- a probe (or the effect class itself) proves nothing
  happened; only then is a retry permitted, within the mission budget;
- ``UNKNOWN``     -- no probe exists or the probe could not decide. The
  attempt stays blocked: an ambiguous dispatch is never replayed blindly.

Destructive effects stay denied; a lost release certainty is BLOCKED, not a
pass. This module never dispatches anything itself.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from assistant.missions.contracts import new_id
from assistant.missions.store import MissionStore

logger = logging.getLogger("assistant.missions.recovery")

ReconciliationOutcome = Literal["CONFIRMED", "NO_EFFECT", "UNKNOWN"]

#: A probe answers one question: did operation ``op_id`` land?
EffectProbe = Callable[[str], Awaitable[ReconciliationOutcome | None]]


@dataclass(frozen=True)
class Reconciliation:
    """One attempt's reconciled state, ready for the trace."""

    execution_id: str
    mission_id: str
    outcome: ReconciliationOutcome
    retriable: bool
    reason: str


class Reconciler:
    """Decides what happened to uncertain attempts after a restart."""

    def __init__(self, store: MissionStore, *, probe: EffectProbe | None = None) -> None:
        self._store = store
        self._probe = probe

    async def reconcile(self, execution_id: str) -> Reconciliation:
        attempt = await self._store.get_attempt(execution_id)
        if attempt is None:
            return Reconciliation(execution_id, "", "UNKNOWN", False,
                                  "no attempt record exists")
        if attempt["dispatch_state"] == "RESULT_APPLIED":
            # R07/F07: the SAVED outcome is the evidence — a durably applied
            # result with an UNKNOWN effect is still UNKNOWN, not confirmed.
            saved = str(attempt.get("effect_outcome") or "UNKNOWN")
            return Reconciliation(
                execution_id,
                str(attempt["mission_id"]),
                "CONFIRMED" if saved == "CONFIRMED" else "UNKNOWN",
                False,
                f"result was applied with outcome {saved}",
            )
        if attempt["dispatch_state"] not in {"INTENT_COMMITTED", "DISPATCHED", "RECONCILING"}:
            return Reconciliation(execution_id, str(attempt["mission_id"]), "UNKNOWN", False,
                                  f"attempt state {attempt['dispatch_state']} is final")
        effect_class = str(attempt["effect_class"])
        external_ids = _external_ids(attempt)
        # C03/N02: aggregate EVERY known external operation conservatively.
        # One confirmed or one unresolvable operation prevents replay of the
        # whole attempt; only when every operation is proven NO_EFFECT is
        # the attempt retriable at all.
        if external_ids and self._probe is not None:
            answers: list[ReconciliationOutcome] = []
            probed: list[str] = []
            for op_id in external_ids:
                probed.append(op_id)
                try:
                    answer = await self._probe(op_id)
                except Exception as exc:  # noqa: BLE001 -- a failed probe is not evidence
                    logger.warning("effect_probe_failed op=%s: %s", op_id, exc)
                    answer = None
                answers.append(answer if answer in {"CONFIRMED", "NO_EFFECT"} else "UNKNOWN")
            if "CONFIRMED" in answers:
                await self._record(execution_id, str(attempt["mission_id"]), "CONFIRMED")
                confirmed_ops = ",".join(
                    op for op, answer in zip(probed, answers, strict=False)
                    if answer == "CONFIRMED"
                )
                return Reconciliation(
                    execution_id, str(attempt["mission_id"]), "CONFIRMED", False,
                    f"probe confirmed {confirmed_ops}",
                )
            if "UNKNOWN" in answers:
                unresolved = ",".join(
                    op for op, a in zip(probed, answers, strict=False) if a == "UNKNOWN"
                )
                await self._record(execution_id, str(attempt["mission_id"]), "UNKNOWN")
                return Reconciliation(
                    execution_id, str(attempt["mission_id"]), "UNKNOWN", False,
                    f"operations could not be decided: {unresolved}",
                )
            await self._record(execution_id, str(attempt["mission_id"]), "NO_EFFECT")
            return Reconciliation(
                execution_id, str(attempt["mission_id"]), "NO_EFFECT", True,
                f"probe disproved all {len(probed)} operations",
            )
        # Without probes, the effect class is the only evidence.
        if effect_class == "READ_ONLY":
            # A read has no lasting effect: nothing to reconcile, safe to retry.
            return Reconciliation(execution_id, str(attempt["mission_id"]),
                                  "NO_EFFECT", True, "read-only effects leave nothing behind")
        if effect_class == "REPEATABLE_LOCAL":
            # Repeatable only with fresh preconditions and idempotent
            # semantics; without a probe we conservatively stay unknown.
            return Reconciliation(execution_id, str(attempt["mission_id"]), "UNKNOWN",
                                  False, "local effect without a probe stays uncertain")
        return Reconciliation(execution_id, str(attempt["mission_id"]), "UNKNOWN",
                              False, "no probe exists for this effect class")

    async def _record(self, execution_id: str, mission_id: str, outcome: str) -> None:
        """Persist the reconciliation answer on the attempt row."""
        await self._store.record_reconciliation(execution_id, outcome)


def _external_ids(attempt: dict[str, Any]) -> list[str]:
    raw = attempt.get("external_ids") or ""
    return [part for part in str(raw).split(",") if part]


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


__all__ = [
    "EffectProbe",
    "Reconciliation",
    "Reconciler",
    "ReconciliationOutcome",
    "new_id",
]
