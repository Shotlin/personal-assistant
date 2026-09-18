"""Append-only control-plane audit stream (C4).

Records security-sensitive Designer actions with IDs and safe metadata
only. Never store credential values, bearer tokens, passwords, full
prompts, or sensitive tool outputs here. The audit stream is control-plane
history and is intentionally separate from run Live events
(``designer_run_events``, added in P8).

Every authorization denial is audited (C1) with the denied permission and
subject IDs -- enough to answer "who tried to do what to whom", never
"with what secret content".
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("assistant.designer.audit")

# Control-plane audit events (C4). New events are added here, never improvised.
AUDIT_EVENTS = frozenset(
    {
        "agent.created",
        "draft.saved",
        "revision.validated",
        "revision.validated_failed",
        "revision.activated",
        "revision.rollback",
        "capability.revoked",
        "credential.created",
        "credential.replaced",
        "credential.deleted",
        "connector.created",
        "connector.changed",
        "connector.quarantined",
        "connector.revalidated",
        # P1 events:
        "session.created",
        "session.revoked",
        "credential.rotated",
        "permission.denied",
        "rate_limited",
    }
)


async def record(
    store: Any,
    *,
    actor_user_id: str,
    event: str,
    subject: dict[str, Any] | None = None,
) -> None:
    """Insert one audit row. Audit failures are logged, never raised:
    an audit write must not break a user-visible operation, and a
    permission denial must still be enforced even if its audit row fails.
    """
    if subject is None:
        subject = {}
    try:
        await store.record_audit(actor_user_id=actor_user_id, event=event, subject=subject)
    except Exception as exc:  # noqa: BLE001 -- see docstring: log, never raise
        logger.error(
            "designer_audit_write_failed",
            extra={"event": "designer_audit_write_failed", "audit_event": event,
                    "error_type": type(exc).__name__},
        )
