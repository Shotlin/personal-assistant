"""P10 — Live & Activation UI backend endpoint tests.

Covers:
- Listing runs for an agent (newest first, filtered by agent and actor)
- Empty runs list handling
- Cross-user authorization (404 without leaking existence)
- Fetching revision graph for LiveCanvas
- Immediate revocation endpoint behavior and ETag CAS
- Run execution snapshot retrieval for live bootstrapping
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tests.designer.conftest import USER_A, USER_B

from assistant.designer.events import (
    CONTEXT_BUDGET_UPDATE,
    NODE_STARTED,
    RUN_COMPLETED,
    RUN_STARTED,
    emit_run_event,
)
from assistant.designer.store import DesignerStore


async def _create_agent(api: httpx.AsyncClient) -> dict[str, Any]:
    resp = await api.post("/designer/api/v1/agents", json={"name": "P10 Live Test Agent"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _insert_run(
    designer_db: DesignerStore,
    run_id: str,
    *,
    user_id: str = USER_A,
    agent_id: str,
    revision_id: str = "rev-1",
    status: str = "running",
) -> None:
    async with designer_db.connection() as conn:
        await conn.execute(
            "DELETE FROM designer_run_events WHERE run_id = %s",
            (run_id,),
        )
        await conn.execute(
            "DELETE FROM run_registry WHERE run_id = %s",
            (run_id,),
        )
        await conn.execute(
            "INSERT INTO run_registry "
            "(run_id, user_id, chat_id, user_message_id, request_digest, status, "
            "agent_id, revision_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (run_id) DO UPDATE SET status = EXCLUDED.status",
            (run_id, user_id, "chat-1", f"msg-{run_id}", "digest", status, agent_id, revision_id),
        )


# ---------------------------------------------------------------------------
# 1. List agent runs — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agent_runs_success(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    agent = await _create_agent(api)
    agent_id = agent["agent_id"]

    await _insert_run(designer_db, "run-p10-1", agent_id=agent_id, status="completed")
    await _insert_run(designer_db, "run-p10-2", agent_id=agent_id, status="running")

    resp = await api.get(f"/designer/api/v1/agents/{agent_id}/runs")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "runs" in data
    runs = data["runs"]
    assert len(runs) == 2
    run_ids = [r["run_id"] for r in runs]
    assert "run-p10-1" in run_ids
    assert "run-p10-2" in run_ids
    assert runs[0]["agent_id"] == agent_id
    assert "status" in runs[0]
    assert "created_at" in runs[0]


# ---------------------------------------------------------------------------
# 2. List agent runs — empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agent_runs_empty(api: httpx.AsyncClient) -> None:
    agent = await _create_agent(api)
    agent_id = agent["agent_id"]

    resp = await api.get(f"/designer/api/v1/agents/{agent_id}/runs")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data == {"runs": []}


# ---------------------------------------------------------------------------
# 3. Cross-user access denied (404, no existence disclosure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agent_runs_cross_user_denied(
    api: httpx.AsyncClient, api_b: httpx.AsyncClient
) -> None:
    agent = await _create_agent(api)
    agent_id = agent["agent_id"]

    # api_b is authenticated as USER_B
    resp = await api_b.get(f"/designer/api/v1/agents/{agent_id}/runs")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Filter by actor — user only sees their own runs for the agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agent_runs_actor_isolation(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    agent = await _create_agent(api)
    agent_id = agent["agent_id"]

    # Run by USER_A
    await _insert_run(
        designer_db, "run-user-a", user_id=USER_A, agent_id=agent_id, status="running"
    )
    # Run by USER_B for same agent (e.g. if multi-tenant)
    await _insert_run(
        designer_db, "run-user-b", user_id=USER_B, agent_id=agent_id, status="running"
    )

    resp = await api.get(f"/designer/api/v1/agents/{agent_id}/runs")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-user-a"


# ---------------------------------------------------------------------------
# 5. Fetch revision graph for LiveCanvas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_revision_for_live_canvas(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    agent = await _create_agent(api)
    agent_id = agent["agent_id"]
    etag = agent["etag"]

    save_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/revisions",
        json={"graph": valid_graph},
        headers={"If-Match": etag},
    )
    assert save_resp.status_code == 201
    rev_id = save_resp.json()["revision_id"]

    # Fetch revision detail
    rev_resp = await api.get(f"/designer/api/v1/agents/{agent_id}/revisions/{rev_id}")
    assert rev_resp.status_code == 200
    rev_data = rev_resp.json()
    assert rev_data["revision_id"] == rev_id
    assert "graph_json" in rev_data
    assert len(rev_data["graph_json"]["nodes"]) == len(valid_graph["nodes"])


# ---------------------------------------------------------------------------
# 6. Immediate revocation clears active pointer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_immediate_revocation(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    agent = await _create_agent(api)
    agent_id = agent["agent_id"]
    etag = agent["etag"]

    # Save revision
    saved = await api.post(
        f"/designer/api/v1/agents/{agent_id}/revisions",
        json={"graph": valid_graph},
        headers={"If-Match": etag},
    )
    rev_id = saved.json()["revision_id"]
    row_version = saved.json()["row_version"]

    # Activate
    act_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/activate",
        json={"revision_id": rev_id},
        headers={"If-Match": f'W/"{row_version}"'},
    )
    assert act_resp.status_code == 200
    act_etag = act_resp.json()["etag"]

    # Revoke
    revoke_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/revoke",
        headers={"If-Match": act_etag},
    )
    assert revoke_resp.status_code == 200
    rev_data = revoke_resp.json()
    assert rev_data["revoked"] is True
    assert rev_data["revoked_revision_id"] == rev_id

    # Verify agent state shows active_revision_id is None
    refreshed = await api.get(f"/designer/api/v1/agents/{agent_id}")
    assert refreshed.status_code == 200
    assert refreshed.json()["active_revision_id"] is None


# ---------------------------------------------------------------------------
# 7. Run snapshot bootstrapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_snapshot_endpoint(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    agent = await _create_agent(api)
    agent_id = agent["agent_id"]
    run_id = "run-snap-test"

    await _insert_run(designer_db, run_id, agent_id=agent_id, status="running")

    # Emit events
    await emit_run_event(
        designer_db,
        run_id=run_id,
        agent_id=agent_id,
        revision_id="rev-1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={"started_at": "2026-09-19T14:00:00Z"},
    )
    await emit_run_event(
        designer_db,
        run_id=run_id,
        agent_id=agent_id,
        revision_id="rev-1",
        sequence_number=2,
        event_type=NODE_STARTED,
        payload={"node_id": "model_1"},
    )
    await emit_run_event(
        designer_db,
        run_id=run_id,
        agent_id=agent_id,
        revision_id="rev-1",
        sequence_number=3,
        event_type=CONTEXT_BUDGET_UPDATE,
        payload={"estimated_context_tokens": 1200},
    )
    await emit_run_event(
        designer_db,
        run_id=run_id,
        agent_id=agent_id,
        revision_id="rev-1",
        sequence_number=4,
        event_type=RUN_COMPLETED,
        payload={"completed_at": "2026-09-19T14:00:05Z"},
    )

    resp = await api.get(f"/designer/api/v1/runs/{run_id}/snapshot")
    assert resp.status_code == 200
    snap = resp.json()
    assert snap["run_id"] == run_id
    assert snap["status"] == "completed"
    assert snap["event_count"] == 4
    assert snap["budget"]["estimated_context_tokens"] == 1200
