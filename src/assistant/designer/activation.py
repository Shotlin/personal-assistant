"""Activation & revocation lifecycle (P7, R05/R12-14 + Clar 1/3 + Safety 2).

Implements the mandatory sequence:

    Draft → Validate → **Prepare → CAS Activate** → Revoke Now

Key invariants:
- Activation is an atomic CAS pointer swap (``cas_set_active_revision``):
  ``active_revision_id`` changes only when ``row_version`` matches.
- Failed preparation preserves the active revision and closes the
  candidate runtime — the agent never loses its running version because
  a new one failed.
- Revoke Now is immediate: NULL the pointer, drain runtimes, block the
  next dispatch. No idle-timeout wait.
- Rollback re-validates the target revision before activating it.
- Quarantine (connector generation/digest mismatch) blocks activation;
  the user must Review Changes → Validate Again.
- Credential rotation marks runtimes stale/rebuilds (Safety 2): the
  drain path is wired through ``RuntimePool.drain_agent``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from assistant.designer import audit
from assistant.designer.compiler import compile_execution_config
from assistant.designer.errors import DesignerError
from assistant.designer.schemas import UnsupportedSchemaError, parse_graph_document
from assistant.designer.validation import validate_graph

logger = logging.getLogger("assistant.designer.activation")


@dataclass
class ActivationResult:
    """Returned by activate / rollback on success."""

    agent_id: str
    revision_id: str
    previous_revision_id: str | None
    new_row_version: int
    runtimes_drained: int


@dataclass
class RevocationResult:
    """Returned by revoke_now on success."""

    agent_id: str
    revoked_revision_id: str | None
    new_row_version: int
    runtimes_drained: int


async def _validate_revision_for_activation(
    store: Any,
    agent_id: str,
    revision_id: str,
) -> dict[str, Any]:
    """Load + validate a revision for activation (shared by activate and rollback).

    Returns the parsed graph as a dict on success.  Raises
    ``DesignerError("activation_failed", ...)`` on validation failure.
    """
    revision = await store.get_revision(revision_id)
    if revision is None:
        raise DesignerError("missing", "revision not found")
    if str(revision.get("agent_id", "")) != agent_id:
        raise DesignerError("missing", "revision does not belong to this agent")

    try:
        graph = parse_graph_document(dict(revision["graph_json"]))
    except UnsupportedSchemaError as exc:
        raise DesignerError("activation_failed", f"schema error: {exc}") from exc

    report = validate_graph(graph)
    if not report.ok:
        raise DesignerError(
            "activation_failed",
            f"revision {revision_id} has validation issues: "
            + "; ".join(issue.message for issue in report.issues[:5]),
        )

    # Compile to verify the graph can produce an ExecutionConfig (the
    # compiler is pure — no I/O, processes, or side effects).
    try:
        compile_execution_config(
            agent_id=agent_id, revision_id=revision_id, graph=graph
        )
    except Exception as exc:
        raise DesignerError(
            "activation_failed", f"compilation failed: {exc}"
        ) from exc

    return dict(revision["graph_json"])


async def activate(
    store: Any,
    *,
    agent_id: str,
    revision_id: str,
    expected_version: int,
    actor_user_id: str,
    runtime_pool: Any | None = None,
) -> ActivationResult:
    """Prepare + CAS-activate a revision (R05, R12, Clar 3).

    1. Validate the candidate revision graph.
    2. Compile to ExecutionConfig (pure — no processes).
    3. CAS-swap the active_revision_id pointer.
    4. Record revision.activated event + audit.
    5. Drain old runtimes for this agent.

    On CAS conflict → ``DesignerError("conflict", ...)``.
    On validation failure → ``DesignerError("activation_failed", ...)``.
    """
    agent = await store.get_agent(agent_id)
    if agent is None:
        raise DesignerError("missing", "agent not found")

    previous_revision_id = (
        str(agent["active_revision_id"]) if agent.get("active_revision_id") else None
    )

    # Prepare: validate + compile (candidate never reachable by chat).
    await _validate_revision_for_activation(store, agent_id, revision_id)

    # CAS pointer swap: row_version must match expected_version.
    new_version = await store.cas_set_active_revision(
        agent_id, revision_id, expected_version
    )
    if new_version is None:
        raise DesignerError(
            "conflict",
            "activation conflict: agent was modified concurrently",
        )

    # Lifecycle event + audit.
    await store.record_revision_event(
        revision_id=revision_id,
        agent_id=agent_id,
        event="revision.activated",
        actor_user_id=actor_user_id,
        data={
            "previous_revision_id": previous_revision_id,
            "new_row_version": new_version,
        },
    )
    await audit.record(
        store,
        actor_user_id=actor_user_id,
        event="revision.activated",
        subject={
            "agent_id": agent_id,
            "revision_id": revision_id,
            "previous_revision_id": previous_revision_id,
        },
    )

    # Drain old runtimes so the next acquisition rebuilds from the new
    # active revision (P7 / Safety 2).
    drained = 0
    if runtime_pool is not None:
        drained = await runtime_pool.drain_agent(agent_id)

    logger.info(
        "revision_activated",
        extra={
            "event": "revision_activated",
            "agent_id": agent_id,
            "revision_id": revision_id,
            "previous": previous_revision_id,
            "runtimes_drained": drained,
        },
    )

    return ActivationResult(
        agent_id=agent_id,
        revision_id=revision_id,
        previous_revision_id=previous_revision_id,
        new_row_version=new_version,
        runtimes_drained=drained,
    )


async def revoke_now(
    store: Any,
    *,
    agent_id: str,
    expected_version: int | None,
    actor_user_id: str,
    runtime_pool: Any | None = None,
) -> RevocationResult:
    """Immediate revocation (R14, designer.revoke).

    NULLs the active_revision_id pointer, drains all runtimes for this
    agent, and records the event.  The next chat dispatch finds no active
    version → ``unsupported_model`` error.
    """
    agent = await store.get_agent(agent_id)
    if agent is None:
        raise DesignerError("missing", "agent not found")

    revoked_revision_id = (
        str(agent["active_revision_id"]) if agent.get("active_revision_id") else None
    )
    if revoked_revision_id is None:
        raise DesignerError("revocation_failed", "agent has no active revision to revoke")

    new_version = await store.clear_active_revision(agent_id, expected_version)
    if new_version is None:
        raise DesignerError(
            "conflict",
            "revocation conflict: agent was modified concurrently",
        )

    await store.record_revision_event(
        revision_id=revoked_revision_id,
        agent_id=agent_id,
        event="revision.revoked",
        actor_user_id=actor_user_id,
        data={"new_row_version": new_version},
    )
    await audit.record(
        store,
        actor_user_id=actor_user_id,
        event="revision.revoked",
        subject={
            "agent_id": agent_id,
            "revoked_revision_id": revoked_revision_id,
        },
    )

    drained = 0
    if runtime_pool is not None:
        drained = await runtime_pool.drain_agent(agent_id)

    logger.info(
        "revision_revoked",
        extra={
            "event": "revision_revoked",
            "agent_id": agent_id,
            "revoked_revision_id": revoked_revision_id,
            "runtimes_drained": drained,
        },
    )

    return RevocationResult(
        agent_id=agent_id,
        revoked_revision_id=revoked_revision_id,
        new_row_version=new_version,
        runtimes_drained=drained,
    )


async def rollback(
    store: Any,
    *,
    agent_id: str,
    target_revision_id: str,
    expected_version: int,
    actor_user_id: str,
    runtime_pool: Any | None = None,
) -> ActivationResult:
    """Rollback to an earlier revision (P7: rollback revalidates).

    Re-validates the target revision before activating it. Uses the same
    activation path so the full lifecycle applies: validate → compile →
    CAS swap → drain.
    """
    return await activate(
        store,
        agent_id=agent_id,
        revision_id=target_revision_id,
        expected_version=expected_version,
        actor_user_id=actor_user_id,
        runtime_pool=runtime_pool,
    )

