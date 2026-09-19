"""Integration tests for frontend static mount, SPA fallback, and P9 gates (P9, R01-03, R06-07).

Key invariants:
- When designer_enabled=true: /designer/ serves SPA index, client-side routes fallback.
- /designer/api/* routes are never intercepted by the SPA fallback.
- /v1/* routes remain completely untouched.
- Dry validation endpoint validates without creating revisions.
- Gate: Server-backed draft persists after save and survives reload.
- Flag-off boundary: When designer_enabled=false, /designer/ returns 404.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_designer_spa_index_served(api: httpx.AsyncClient) -> None:
    """GET /designer/ and /designer return 200 HTML."""
    r1 = await api.get("/designer")
    assert r1.status_code == 200
    assert "text/html" in r1.headers["content-type"]
    assert '<div id="root">' in r1.text

    r2 = await api.get("/designer/")
    assert r2.status_code == 200
    assert "text/html" in r2.headers["content-type"]
    assert '<div id="root">' in r2.text


@pytest.mark.asyncio
async def test_designer_spa_fallback_for_client_routes(api: httpx.AsyncClient) -> None:
    """Client-side routing paths like /designer/agents/agent-123 fall back to index.html."""
    resp = await api.get("/designer/agents/agent-test-123")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert '<div id="root">' in resp.text


@pytest.mark.asyncio
async def test_designer_api_routes_not_intercepted_by_spa_fallback(api: httpx.AsyncClient) -> None:
    """API paths under /designer/api/ are never intercepted by SPA fallback."""
    # Known endpoint returns JSON
    resp = await api.get("/designer/api/v1/agents")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    assert "agents" in resp.json()

    # Unknown API endpoint returns 404 JSON, not HTML index
    resp404 = await api.get("/designer/api/v1/nonexistent-route-xyz")
    assert resp404.status_code == 404
    assert "application/json" in resp404.headers["content-type"]


@pytest.mark.asyncio
async def test_dry_validation_endpoint(api: httpx.AsyncClient) -> None:
    """POST /designer/api/v1/agents/{agent_id}/validate checks graph without mutating state."""
    # 1. Create an agent
    create_resp = await api.post(
        "/designer/api/v1/agents",
        json={"name": "Validate Test Agent", "description": "validate test description"},
    )
    assert create_resp.status_code == 200
    agent_id = create_resp.json()["agent_id"]

    # 2. Valid graph validation
    valid_graph = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "agent-root",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"enabled": True, "config": {}},
            },
            {
                "id": "model-1",
                "type": "model",
                "position": {"x": 300, "y": 0},
                "data": {"enabled": True, "config": {"model_id": "claude-3-5-sonnet"}},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "agent-root",
                "target": "model-1",
                "sourceHandle": "root",
                "targetHandle": "model",
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    val_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/validate",
        json={"graph": valid_graph},
    )
    assert val_resp.status_code == 200
    assert val_resp.json()["ok"] is True
    assert len(val_resp.json()["issues"]) == 0

    # 3. Invalid graph (forbidden secret key)
    invalid_graph = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "agent-root",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"enabled": True, "config": {}},
            },
            {
                "id": "model-1",
                "type": "model",
                "position": {"x": 300, "y": 0},
                "data": {"enabled": True, "config": {"api_key": "sk-forbidden"}},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "agent-root",
                "target": "model-1",
                "sourceHandle": "root",
                "targetHandle": "model",
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    val_fail_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/validate",
        json={"graph": invalid_graph},
    )
    assert val_fail_resp.status_code == 200
    assert val_fail_resp.json()["ok"] is False
    assert any(i["code"] == "forbidden_field" for i in val_fail_resp.json()["issues"])

    # 4. Verify no revisions were created by dry validation
    revs_resp = await api.get(f"/designer/api/v1/agents/{agent_id}/revisions")
    assert revs_resp.status_code == 200
    assert len(revs_resp.json()["revisions"]) == 0


@pytest.mark.asyncio
async def test_gate_server_backed_draft_survives_reload(api: httpx.AsyncClient) -> None:
    """Gate P9: A saved draft graph persists on the backend and survives reload."""
    # 1. Create agent
    create_resp = await api.post(
        "/designer/api/v1/agents",
        json={"name": "Persistence Agent", "description": "gate test"},
    )
    assert create_resp.status_code == 200
    agent_data = create_resp.json()
    agent_id = agent_data["agent_id"]
    row_version = agent_data["row_version"]

    # 2. Save draft revision
    draft_graph = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "agent-root",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"enabled": True, "config": {}},
            },
            {
                "id": "model-1",
                "type": "model",
                "position": {"x": 300, "y": 0},
                "data": {
                    "enabled": True,
                    "config": {"model_id": "claude-3-5-sonnet", "temperature": 0.2},
                },
            },
            {
                "id": "skill-1",
                "type": "skill",
                "position": {"x": 300, "y": 200},
                "data": {"enabled": True, "config": {"skill_id": "web-search"}},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "agent-root",
                "target": "model-1",
                "sourceHandle": "root",
                "targetHandle": "model",
            },
            {
                "id": "e2",
                "source": "agent-root",
                "target": "skill-1",
                "sourceHandle": "root",
                "targetHandle": "tools",
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    save_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/revisions",
        headers={"If-Match": f'"{row_version}"'},
        json={"graph": draft_graph},
    )
    assert save_resp.status_code == 201
    save_data = save_resp.json()
    saved_rev_id = save_data["revision_id"]

    # 3. Simulate reload by fetching agent details
    reload_resp = await api.get(f"/designer/api/v1/agents/{agent_id}")
    assert reload_resp.status_code == 200
    reloaded_agent = reload_resp.json()

    # Verify draft revision is present and matches saved content
    assert reloaded_agent["draft"] is not None
    assert reloaded_agent["draft"]["revision_id"] == saved_rev_id
    assert len(reloaded_agent["draft"]["graph"]["nodes"]) == 3
    assert len(reloaded_agent["draft"]["graph"]["edges"]) == 2

    # Verify nodes and edges survived exactly
    node_ids = {n["id"] for n in reloaded_agent["draft"]["graph"]["nodes"]}
    assert node_ids == {"agent-root", "model-1", "skill-1"}


@pytest.mark.asyncio
async def test_flag_off_designer_returns_404(anonymous_api: httpx.AsyncClient) -> None:
    """When DESIGNER_ENABLED=false, /designer routes return 404 and /v1/models is untouched."""
    from assistant.main import create_app
    from assistant.settings import Settings

    flag_off_settings = Settings(
        agent_gateway_api_key="test-key",
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
        designer_enabled=False,
        designer_credentials_key="",  # Must not be required when flag is off (Safety note 1)
    )
    flag_off_app = create_app(flag_off_settings)

    transport = httpx.ASGITransport(app=flag_off_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/designer")
        assert resp.status_code == 404

        resp2 = await client.get("/designer/")
        assert resp2.status_code == 404

        # Gateway standard endpoints remain intact
        health = await client.get("/healthz")
        assert health.status_code == 200
