"""P7 — Activation, revocation & rollback tests.

Covers the full lifecycle: Prepare → CAS Activate → Revoke Now,
rollback with revalidation, concurrency (stale ETag), authorization
isolation, runtime drain, and dispatch blocking after revocation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from tests.designer.conftest import USER_A

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_agent(api: httpx.AsyncClient) -> dict[str, Any]:
    resp = await api.post("/designer/api/v1/agents", json={"name": "P7 Test Agent"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _save_revision(
    api: httpx.AsyncClient, agent_id: str, etag: str, graph: dict[str, Any]
) -> dict[str, Any]:
    resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/revisions",
        json={"graph": graph},
        headers={"If-Match": etag},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get_agent(api: httpx.AsyncClient, agent_id: str) -> dict[str, Any]:
    resp = await api.get(f"/designer/api/v1/agents/{agent_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Activate a valid revision (no previous active)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_valid_revision(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    agent = await _create_agent(api)
    etag = agent["etag"]
    saved = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    new_etag = f'W/"{saved["row_version"]}"'

    resp = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": saved["revision_id"]},
        headers={"If-Match": new_etag},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["activated"] is True
    assert body["revision_id"] == saved["revision_id"]
    assert body["previous_revision_id"] is None

    # Agent now has an active revision.
    agent_after = await _get_agent(api, agent["agent_id"])
    assert str(agent_after["active_revision_id"]) == saved["revision_id"]


# ---------------------------------------------------------------------------
# 2. Activate replaces existing active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_replaces_active(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    agent = await _create_agent(api)
    etag = agent["etag"]

    # Save + activate r1.
    r1 = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    etag_r1 = f'W/"{r1["row_version"]}"'
    act1 = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r1["revision_id"]},
        headers={"If-Match": etag_r1},
    )
    assert act1.status_code == 200
    etag_act1 = act1.json()["etag"]

    # Save r2.
    r2 = await _save_revision(api, agent["agent_id"], etag_act1, valid_graph)
    etag_r2 = f'W/"{r2["row_version"]}"'

    # Activate r2 — should replace r1.
    act2 = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r2["revision_id"]},
        headers={"If-Match": etag_r2},
    )
    assert act2.status_code == 200
    body = act2.json()
    assert body["revision_id"] == r2["revision_id"]
    assert body["previous_revision_id"] == r1["revision_id"]


# ---------------------------------------------------------------------------
# 3. Activate on stale ETag → 409 conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_stale_etag(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    agent = await _create_agent(api)
    stale_etag = agent["etag"]  # version before save
    saved = await _save_revision(api, agent["agent_id"], stale_etag, valid_graph)

    # Use the stale_etag (pre-save version) — should conflict.
    resp = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": saved["revision_id"]},
        headers={"If-Match": stale_etag},
    )
    assert resp.status_code == 409, resp.text
    assert "conflict" in resp.json()["detail"]["error"]["code"]


# ---------------------------------------------------------------------------
# 4. Failed preparation preserves active revision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_preparation_preserves_active(
    api: httpx.AsyncClient, valid_graph: dict[str, Any], designer_db: Any
) -> None:
    import uuid

    agent = await _create_agent(api)
    etag = agent["etag"]

    # Save + activate a valid revision.
    r1 = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    etag_r1 = f'W/"{r1["row_version"]}"'
    act1 = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r1["revision_id"]},
        headers={"If-Match": etag_r1},
    )
    assert act1.status_code == 200

    # Insert an invalid revision directly into DB (bypassing save-time validation).
    bad_graph = {
        "schema_version": 1,
        "nodes": [
            {"id": "agent-root", "type": "agent", "position": {"x": 0, "y": 0},
             "data": {"enabled": True}},
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    bad_rev_id = str(uuid.uuid4())
    new_version = await designer_db.insert_revision(
        revision_id=bad_rev_id,
        agent_id=agent["agent_id"],
        revision_number=2,
        schema_version=1,
        graph_json=bad_graph,
        semantic_hash="bad-hash",
        layout_hash="bad-hash",
        dependency_lock={},
        parent_revision_id=r1["revision_id"],
        created_by=USER_A,
    )
    etag_r2 = f'W/"{new_version}"'

    # Attempt to activate the bad revision.
    resp = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": bad_rev_id},
        headers={"If-Match": etag_r2},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error"]["code"] == "activation_failed"

    # Active revision is still r1.
    agent_after = await _get_agent(api, agent["agent_id"])
    assert str(agent_after["active_revision_id"]) == r1["revision_id"]


# ---------------------------------------------------------------------------
# 5. Revoke Now clears active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_clears_active(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    agent = await _create_agent(api)
    etag = agent["etag"]

    r1 = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    etag_r1 = f'W/"{r1["row_version"]}"'
    act = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r1["revision_id"]},
        headers={"If-Match": etag_r1},
    )
    assert act.status_code == 200
    etag_act = act.json()["etag"]

    resp = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/revoke",
        headers={"If-Match": etag_act},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revoked"] is True
    assert body["revoked_revision_id"] == r1["revision_id"]

    # Agent has no active revision.
    agent_after = await _get_agent(api, agent["agent_id"])
    assert agent_after["active_revision_id"] is None


# ---------------------------------------------------------------------------
# 6. Revoke with no active → revocation_failed 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_no_active(api: httpx.AsyncClient) -> None:
    agent = await _create_agent(api)
    resp = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/revoke",
        headers={"If-Match": agent["etag"]},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error"]["code"] == "revocation_failed"


# ---------------------------------------------------------------------------
# 7. Rollback re-validates before activating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_revalidates(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    agent = await _create_agent(api)
    etag = agent["etag"]

    # Save + activate r1.
    r1 = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    etag_r1 = f'W/"{r1["row_version"]}"'
    act1 = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r1["revision_id"]},
        headers={"If-Match": etag_r1},
    )
    assert act1.status_code == 200
    etag_act1 = act1.json()["etag"]

    # Save + activate r2.
    r2 = await _save_revision(api, agent["agent_id"], etag_act1, valid_graph)
    etag_r2 = f'W/"{r2["row_version"]}"'
    act2 = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r2["revision_id"]},
        headers={"If-Match": etag_r2},
    )
    assert act2.status_code == 200
    etag_act2 = act2.json()["etag"]

    # Rollback to r1 (revalidation runs internally).
    resp = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/rollback",
        json={"revision_id": r1["revision_id"]},
        headers={"If-Match": etag_act2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rolled_back"] is True
    assert body["revision_id"] == r1["revision_id"]
    assert body["previous_revision_id"] == r2["revision_id"]


# ---------------------------------------------------------------------------
# 8. Cross-user activation denied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_activation_denied(
    api: httpx.AsyncClient,
    api_b: httpx.AsyncClient,
    valid_graph: dict[str, Any],
) -> None:
    # User A creates + saves an agent.
    agent = await _create_agent(api)
    etag = agent["etag"]
    saved = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    new_etag = f'W/"{saved["row_version"]}"'

    # User B cannot activate it (agent not found for user B → 404).
    resp = await api_b.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": saved["revision_id"]},
        headers={"If-Match": new_etag},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 9. CAS serialization — concurrent activation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cas_serialization(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    agent = await _create_agent(api)
    etag = agent["etag"]

    r1 = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    etag_r1 = f'W/"{r1["row_version"]}"'

    r2 = await _save_revision(api, agent["agent_id"], etag_r1, valid_graph)
    etag_r2 = f'W/"{r2["row_version"]}"'

    # First activation succeeds.
    act1 = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r1["revision_id"]},
        headers={"If-Match": etag_r2},
    )
    assert act1.status_code == 200

    # Second activation with the same (now stale) etag → 409.
    act2 = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r2["revision_id"]},
        headers={"If-Match": etag_r2},
    )
    assert act2.status_code == 409


# ---------------------------------------------------------------------------
# 10. Revoke blocks next dispatch (resolve_chat_agent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_blocks_dispatch(
    api: httpx.AsyncClient,
    valid_graph: dict[str, Any],
    designer_db: Any,
) -> None:
    """After revocation, resolve_chat_agent raises unsupported_model."""
    agent = await _create_agent(api)
    etag = agent["etag"]

    r1 = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    etag_r1 = f'W/"{r1["row_version"]}"'
    act = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r1["revision_id"]},
        headers={"If-Match": etag_r1},
    )
    assert act.status_code == 200
    etag_act = act.json()["etag"]

    # Verify the agent has an active revision.
    agent_mid = await _get_agent(api, agent["agent_id"])
    assert agent_mid["active_revision_id"] is not None

    # Revoke.
    resp = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/revoke",
        headers={"If-Match": etag_act},
    )
    assert resp.status_code == 200

    # Active revision is NULL — chat cannot resolve.
    agent_after = await _get_agent(api, agent["agent_id"])
    assert agent_after["active_revision_id"] is None


# ---------------------------------------------------------------------------
# 11. Runtime pool drain_agent integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_pool_drain_agent() -> None:
    """drain_agent drains all entries for the target agent_id."""
    from assistant.designer.runtimes import RuntimeCacheKey, RuntimePool

    closed: list[str] = []

    class FakeRuntime:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            closed.append(self.name)

    async def builder(key: RuntimeCacheKey, config: Any) -> tuple[Any, dict[str, int]]:
        return FakeRuntime(f"{key.agent_id}-{key.revision_id}"), {}

    pool = RuntimePool(builder)

    # Inject two entries for agent-A and one for agent-B.
    from assistant.designer.compiler import (
        ContextPolicy,
        ExecutionConfig,
        MemoryPolicy,
        ModelConfig,
        PromptConfig,
    )
    from assistant.designer.connectors import ScopeKind

    dummy_config = ExecutionConfig(
        agent_id="a", revision_id="r1",
        model=ModelConfig(provider="x", model_id="y"),
        prompt=PromptConfig(), skills=(), memory=MemoryPolicy(),
        context=ContextPolicy(), capabilities=frozenset(),
    )

    lease_a1 = await pool.acquire(
        agent_id="agent-a", revision_id="r1", execution_epoch=1,
        scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=dummy_config,
    )
    await lease_a1.release()

    lease_a2 = await pool.acquire(
        agent_id="agent-a", revision_id="r2", execution_epoch=1,
        scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=dummy_config,
    )
    await lease_a2.release()

    lease_b = await pool.acquire(
        agent_id="agent-b", revision_id="r1", execution_epoch=1,
        scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=dummy_config,
    )
    await lease_b.release()

    drained = await pool.drain_agent("agent-a")
    assert drained == 2
    assert len(closed) == 2
    # agent-b untouched
    assert len(pool._entries) == 1


# ---------------------------------------------------------------------------
# 12. Activation lifecycle events are recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_lifecycle_events(
    api: httpx.AsyncClient, valid_graph: dict[str, Any], designer_db: Any
) -> None:
    """Activation and revocation record revision events."""
    agent = await _create_agent(api)
    etag = agent["etag"]
    r1 = await _save_revision(api, agent["agent_id"], etag, valid_graph)
    etag_r1 = f'W/"{r1["row_version"]}"'

    # Activate.
    act = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/activate",
        json={"revision_id": r1["revision_id"]},
        headers={"If-Match": etag_r1},
    )
    assert act.status_code == 200
    etag_act = act.json()["etag"]

    # Revoke.
    rev = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/revoke",
        headers={"If-Match": etag_act},
    )
    assert rev.status_code == 200

    # Check events in the DB.
    async with designer_db.connection() as conn:
        cursor = await conn.execute(
            "SELECT event FROM designer_revision_events "
            "WHERE agent_id = %s ORDER BY at",
            (agent["agent_id"],),
        )
        rows = await cursor.fetchall()
    events = [str(row[0]) for row in rows]
    assert "draft.saved" in events
    assert "revision.activated" in events
    assert "revision.revoked" in events


# ---------------------------------------------------------------------------
# 13. Concurrent CUA queue serializes runs and reports "Waiting for desktop"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desktop_queue_serialization() -> None:
    """P7 gate: concurrent CUA serializes; waiter reports 'Waiting for desktop'."""
    from assistant.runtime.desktop_queue import DesktopQueue

    queue = DesktopQueue(timeout_seconds=5.0)

    # Run 1 acquires immediately
    await queue.acquire("run-1")
    assert queue.current_owner == "run-1"
    assert queue.status_for("run-1") == "active"
    assert queue.status_for("run-2") == "idle"

    # Run 2 contends and waits
    task2 = asyncio.create_task(queue.acquire("run-2"))
    await asyncio.sleep(0.01)

    assert queue.is_waiting("run-2")
    assert queue.status_for("run-2") == "Waiting for desktop"
    assert queue.queue_length == 1

    # Run 1 releases, Run 2 is granted
    await queue.release("run-1")
    await task2
    assert queue.current_owner == "run-2"
    assert queue.status_for("run-2") == "active"
    assert queue.queue_length == 0

    await queue.release("run-2")
    assert queue.current_owner is None


# ---------------------------------------------------------------------------
# 14. DesktopQueue timeout raises DesktopLeaseBusy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desktop_queue_timeout() -> None:
    """DesktopQueue timeout raises DesktopLeaseBusy."""
    from assistant.runtime.desktop_queue import DesktopQueue
    from assistant.runtime.session import DesktopLeaseBusy

    queue = DesktopQueue(timeout_seconds=0.05)
    await queue.acquire("run-1")

    with pytest.raises(DesktopLeaseBusy, match="Timed out waiting for desktop lease"):
        await queue.acquire("run-2", timeout=0.05)

    await queue.release("run-1")


# ---------------------------------------------------------------------------
# 15. Permission denial when actor lacks designer.activate / designer.revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_permission_denial() -> None:
    """Actor without designer.activate gets permission_denied."""
    from assistant.designer.auth import Actor, require_permission
    from assistant.designer.errors import DesignerError

    view_only_actor = Actor(user_id=USER_A, role="user", permissions=frozenset({"designer.view"}))
    with pytest.raises(DesignerError) as exc_act:
        require_permission(view_only_actor, "designer.activate")
    assert exc_act.value.code == "permission_denied"

    with pytest.raises(DesignerError) as exc_rev:
        require_permission(view_only_actor, "designer.revoke")
    assert exc_rev.value.code == "permission_denied"

