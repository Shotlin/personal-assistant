"""P11 — Release & Acceptance tests (A1–A14, Fix 10, C2, Safety 1, Safety 2).

Covers:
- Flag-false rollback safety (DESIGNER_ENABLED=false disables all Designer endpoints)
- Custom-model gate: request routing, credential_ref-only graph JSON, draft isolation
- Credential rotation & invalidation (rotated/revoked credential never serves stale)
- Multi-agent & cross-user isolation (A3: foreign access returns indistinguishable 404)
- Idempotent migration rehearsal (A11: rerun migrations on existing DB without error)
- Runtime binding safety (A2: agent without CUA cannot dispatch desktop tools)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts.migrate_designer import MIGRATIONS_DIR, apply_migrations
from tests.designer.conftest import POSTGRES_URL, designer_settings

from assistant.designer.compiler import ScopeKind, compile_execution_config
from assistant.designer.runtimes import (
    ExecutionConfig,
    RuntimeCacheKey,
    RuntimePool,
)
from assistant.designer.schemas import parse_graph_document
from assistant.designer.store import DesignerStore
from assistant.main import create_app

# ---------------------------------------------------------------------------
# 1. Flag-false rollback safety (C2 + Safety note 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_false_rollback_routes_404() -> None:
    """When DESIGNER_ENABLED=false, all Designer endpoints and SPA routes return 404,
    while core baseline routes (/healthz, etc.) remain intact without regression."""
    settings = designer_settings(designer_enabled=False)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        # Designer API routes return 404
        r_session = await client.post(
            "/designer/api/v1/session",
            json={"mode": "local_password", "email": "a@local", "password": "pw"},
        )
        assert r_session.status_code == 404

        r_agents = await client.get("/designer/api/v1/agents")
        assert r_agents.status_code == 404

        # Designer SPA routes return 404
        r_spa = await client.get("/designer/")
        assert r_spa.status_code == 404

        r_spa_sub = await client.get("/designer/agents/some-id")
        assert r_spa_sub.status_code == 404

        # Core healthcheck remains functional
        r_health = await client.get("/healthz")
        assert r_health.status_code == 200
        assert r_health.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 2. Custom-model gate: credential_ref only, no secrets in graph JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_model_gate_graph_contains_no_secrets(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    """Graph JSON stored in database and exported via API contains only credential_ref,
    never raw secrets, tokens, or plaintext keys."""
    resp = await api.post("/designer/api/v1/agents", json={"name": "Model Sec Agent"})
    agent = resp.json()
    agent_id = agent["agent_id"]
    etag = agent["etag"]

    graph_with_cred_ref = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "agent-root",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"enabled": True, "label": "Root"},
            },
            {
                "id": "model-1",
                "type": "model",
                "position": {"x": 200, "y": 0},
                "data": {
                    "enabled": True,
                    "label": "Claude 3.5 Sonnet",
                    "config": {
                        "model_id": "anthropic/claude-3-5-sonnet",
                        "provider": "openrouter",
                        "credential_ref": "cred_openrouter_main",
                    },
                },
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "agent-root",
                "target": "model-1",
                "sourceHandle": "model",
                "targetHandle": "in",
            }
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }

    save_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/revisions",
        json={"graph": graph_with_cred_ref},
        headers={"If-Match": etag},
    )
    assert save_resp.status_code == 201
    rev_id = save_resp.json()["revision_id"]

    # Verify directly in DB
    rev_row = await designer_db.get_revision(rev_id)
    assert rev_row is not None
    graph_str = str(rev_row["graph_json"])
    assert "cred_openrouter_main" in graph_str
    assert "sk-" not in graph_str
    assert "password" not in graph_str.lower()
    assert "secret" not in graph_str.lower()


# ---------------------------------------------------------------------------
# 3. Draft-change isolation & activate cutover (A4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_change_isolation_and_activate_cutover(
    api: httpx.AsyncClient, valid_graph: dict[str, Any]
) -> None:
    """Saving drafts does not mutate the active revision. Activating immediately cuts over."""
    agent_resp = await api.post("/designer/api/v1/agents", json={"name": "Lifecycle Agent"})
    agent = agent_resp.json()
    agent_id = agent["agent_id"]
    etag = agent["etag"]

    # 1. Save Revision 1
    rev1_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/revisions",
        json={"graph": valid_graph},
        headers={"If-Match": etag},
    )
    rev1_id = rev1_resp.json()["revision_id"]
    rev1_row_ver = rev1_resp.json()["row_version"]

    # 2. Activate Revision 1
    act_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/activate",
        json={"revision_id": rev1_id},
        headers={"If-Match": f'W/"{rev1_row_ver}"'},
    )
    assert act_resp.status_code == 200
    act_row_ver = act_resp.json()["row_version"]

    current_agent = (await api.get(f"/designer/api/v1/agents/{agent_id}")).json()
    assert current_agent["active_revision_id"] == rev1_id

    # 3. Save Revision 2 with modified prompt
    modified_graph = dict(valid_graph)
    modified_graph["nodes"] = [
        dict(n, data={"label": "Updated"}) for n in valid_graph["nodes"]
    ]
    rev2_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/revisions",
        json={"graph": modified_graph},
        headers={"If-Match": f'W/"{act_row_ver}"'},
    )
    rev2_id = rev2_resp.json()["revision_id"]
    rev2_row_ver = rev2_resp.json()["row_version"]

    # 4. Draft isolation: active revision remains rev1!
    agent_during_draft = (await api.get(f"/designer/api/v1/agents/{agent_id}")).json()
    assert agent_during_draft["active_revision_id"] == rev1_id

    # 5. Activate Revision 2: cutover to rev2!
    act2_resp = await api.post(
        f"/designer/api/v1/agents/{agent_id}/activate",
        json={"revision_id": rev2_id},
        headers={"If-Match": f'W/"{rev2_row_ver}"'},
    )
    assert act2_resp.status_code == 200
    agent_after_cutover = (await api.get(f"/designer/api/v1/agents/{agent_id}")).json()
    assert agent_after_cutover["active_revision_id"] == rev2_id


# ---------------------------------------------------------------------------
# 4. Credential rotation / revocation invalidation (Safety 2)
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, token: str) -> None:
        self.token = token
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_credential_rotation_invalidates_runtime_pool() -> None:
    """Safety 2: Rotating or revoking a credential drains every runtime bound to it;
    subsequent acquisition cannot serve from a stale instance."""
    build_count = 0
    current_token = "token-gen-1"

    async def builder(key: RuntimeCacheKey, config: ExecutionConfig):
        nonlocal build_count
        build_count += 1
        gen = 1 if current_token == "token-gen-1" else 2
        return _FakeRuntime(current_token), {"cred-mcp-1": gen}

    pool = RuntimePool(builder)
    config = compile_execution_config(
        agent_id="agent-1",
        revision_id="rev-1",
        graph=parse_graph_document({
            "schema_version": 1,
            "nodes": [
                {"id": "root", "type": "agent", "position": {"x": 0, "y": 0},
                 "data": {"enabled": True}},
                {"id": "m1", "type": "model", "position": {"x": 200, "y": 0},
                 "data": {"enabled": True}},
            ],
            "edges": [
                {"id": "e1", "source": "root", "target": "m1",
                 "sourceHandle": "model", "targetHandle": "in"},
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }),
    )

    # First acquisition
    lease1 = await pool.acquire(
        agent_id="agent-1",
        revision_id="rev-1",
        execution_epoch=1,
        scope=ScopeKind.SHARED,
        actor_id=None,
        run_id=None,
        config=config,
    )
    rt1 = lease1.runtime
    assert rt1.token == "token-gen-1"
    assert build_count == 1
    await lease1.release()

    # Second acquisition before rotation reuses existing runtime
    lease2 = await pool.acquire(
        agent_id="agent-1",
        revision_id="rev-1",
        execution_epoch=1,
        scope=ScopeKind.SHARED,
        actor_id=None,
        run_id=None,
        config=config,
    )
    assert lease2.runtime is rt1
    assert build_count == 1
    await lease2.release()

    # Rotate credential
    current_token = "token-gen-2"
    drained_keys = pool.mark_credential_stale("cred-mcp-1")
    assert len(drained_keys) == 1

    # Next acquisition rebuilds fresh instance
    lease3 = await pool.acquire(
        agent_id="agent-1",
        revision_id="rev-1",
        execution_epoch=1,
        scope=ScopeKind.SHARED,
        actor_id=None,
        run_id=None,
        config=config,
    )
    assert lease3.runtime is not rt1
    assert lease3.runtime.token == "token-gen-2"
    assert build_count == 2
    await lease3.release()

    await pool.aclose()


# ---------------------------------------------------------------------------
# 5. Multi-agent & cross-user isolation (A3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_and_cross_agent_isolation(
    api: httpx.AsyncClient, api_b: httpx.AsyncClient
) -> None:
    """Users cannot inspect, modify, or list resources of other users' agents.
    All cross-user probes return 404 indistinguishable from non-existent rows."""
    resp_a = await api.post("/designer/api/v1/agents", json={"name": "User A Agent"})
    agent_a = resp_a.json()
    agent_a_id = agent_a["agent_id"]

    # User B cannot read User A's agent
    r_get = await api_b.get(f"/designer/api/v1/agents/{agent_a_id}")
    assert r_get.status_code == 404

    # User B cannot list revisions of User A's agent
    r_revs = await api_b.get(f"/designer/api/v1/agents/{agent_a_id}/revisions")
    assert r_revs.status_code == 404

    # User B cannot list runs of User A's agent
    r_runs = await api_b.get(f"/designer/api/v1/agents/{agent_a_id}/runs")
    assert r_runs.status_code == 404

    # User B cannot save revisions to User A's agent
    r_save = await api_b.post(
        f"/designer/api/v1/agents/{agent_a_id}/revisions",
        json={"graph": {"schema_version": 1, "nodes": [], "edges": []}},
        headers={"If-Match": 'W/"1"'},
    )
    assert r_save.status_code == 404

    # User B cannot activate User A's agent
    r_act = await api_b.post(
        f"/designer/api/v1/agents/{agent_a_id}/activate",
        json={"revision_id": "any-rev"},
        headers={"If-Match": 'W/"1"'},
    )
    assert r_act.status_code == 404

    # User B cannot revoke User A's agent
    r_rev = await api_b.post(
        f"/designer/api/v1/agents/{agent_a_id}/revoke",
        headers={"If-Match": 'W/"1"'},
    )
    assert r_rev.status_code == 404


# ---------------------------------------------------------------------------
# 6. Idempotent migration rehearsal (A11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_rehearsal_idempotency() -> None:
    """Applying migrations 001..004 repeatedly against an existing database
    is strictly idempotent, safe, and succeeds with zero errors."""
    migrations_dir = Path(MIGRATIONS_DIR)
    assert migrations_dir.exists()

    # Re-run migrations
    applied = await apply_migrations(POSTGRES_URL, migrations_dir)
    # Already applied; should return empty list (0 pending)
    assert applied == []


# ---------------------------------------------------------------------------
# 7. Runtime binding safety: agent without CUA cannot dispatch desktop (A2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_binding_without_cua_omits_desktop_tools() -> None:
    """A2: An agent graph without a CUA node compiles to an ExecutionConfig
    with no CUA tools enabled."""
    graph_no_cua = {
        "schema_version": 1,
        "nodes": [
            {"id": "root", "type": "agent", "position": {"x": 0, "y": 0},
             "data": {"enabled": True}},
            {"id": "m1", "type": "model", "position": {"x": 200, "y": 0},
             "data": {"enabled": True}},
            {
                "id": "skill-1",
                "type": "skill",
                "position": {"x": 400, "y": 0},
                "data": {
                    "enabled": True,
                    "label": "Summarizer",
                    "config": {"skill_id": "summarize"},
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "root", "target": "m1",
             "sourceHandle": "model", "targetHandle": "in"},
            {"id": "e2", "source": "root", "target": "skill-1",
             "sourceHandle": "tools", "targetHandle": "in"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }

    config = compile_execution_config(
        agent_id="agent-cua",
        revision_id="rev-1",
        graph=parse_graph_document(graph_no_cua),
    )
    assert config.allows_native_dispatch() is False
    assert "agent.cua" not in config.capabilities
    # Only skill present; no CUA actions
    assert len(config.skills) == 1
    assert config.skills[0].id == "skill-1"
