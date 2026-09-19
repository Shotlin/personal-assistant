"""P5 tests: compiler, runtime pool, bootstrap, migration, C5 authorization.

Covers (frozen plan): ExecutionConfig compilation, RuntimeCacheKey
scoping with credential-generation staleness (Safety 2), single-flight +
refcounts, Vion bootstrap (Fix 1, R21), migration 003 agent-scoped dedup,
and the five C5 chat-authorization scenarios (they run against the
gateway chat route in test_runtime_gateway.py's harness).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from scripts.bootstrap_designer import VION_SLUG, build_vion_graph
from tests.designer.test_graph import edge, make_graph, node

from assistant.designer.compiler import (
    CAP_CUA,
    CONTEXT_DEFAULTS,
    ExecutionConfig,
    RuntimeScopeRequest,
    compile_execution_config,
    config_hash,
    refuse_mismatched_scope,
)
from assistant.designer.connectors import ScopeKind
from assistant.designer.errors import DesignerError
from assistant.designer.runtimes import RuntimeCacheKey, RuntimePool
from assistant.designer.schemas import parse_graph_document
from assistant.designer.validation import validate_graph

# ---------------------------------------------------------------------------
# Compiler (pure graph -> ExecutionConfig)
# ---------------------------------------------------------------------------


def _compile(graph: dict[str, Any], **resolved: Any) -> ExecutionConfig:
    report = validate_graph(parse_graph_document(graph))
    assert report.ok, report.to_dict()
    return compile_execution_config(
        agent_id="agent-1", revision_id="rev-1",
        graph=parse_graph_document(graph), resolved_dependencies=resolved,
    )


def test_compile_minimal_graph() -> None:
    config = _compile(make_graph())
    assert config.model.model_id == ""
    assert CAP_CUA not in config.capabilities
    assert config.context.recent_turns == CONTEXT_DEFAULTS["recent_turns"]


def test_compile_disconnected_model_raises() -> None:
    graph = make_graph()
    graph["edges"] = []
    with pytest.raises(ValueError, match="model"):
        compile_execution_config(
            agent_id="a", revision_id="r", graph=parse_graph_document(graph)
        )


def test_compile_with_cua_adds_capability() -> None:
    graph = make_graph()
    graph["nodes"].append(node("cua", "1"))
    graph["edges"].append(edge("agent-root", "cua-1", "cua"))
    config = _compile(graph)
    assert config.allows_native_dispatch() is True


def test_compile_without_cua_blocks_native_dispatch() -> None:
    """A2 core: no CUA node in the graph -> no native dispatch capability."""
    config = _compile(make_graph())
    assert config.allows_native_dispatch() is False


def test_compile_mcp_connector_tool_capabilities() -> None:
    graph = make_graph()
    graph["nodes"].append(
        node("mcp", "1", config={"connector_id": "github",
                                 "selected_tool_ids": ["github:search_code"]})
    )
    graph["edges"].append(edge("agent-root", "mcp-1", "mcp"))
    config = _compile(graph)
    assert "mcp:github:github:search_code" in config.capabilities


def test_compile_memory_disconnect_disables_memory() -> None:
    config = _compile(make_graph())
    assert config.memory.thread_recall is False and config.memory.user_memory is False
    graph = make_graph()
    graph["nodes"].append(node("memory", "1", config={"kind": "user"}))
    graph["edges"].append(edge("agent-root", "memory-1", "memory"))
    config2 = _compile(graph)
    assert config2.memory.user_memory is True


def test_compile_knowledge_blocked_adapter_is_inert() -> None:
    """Fix 2: a connected knowledge node mounts NOTHING while the adapter
    is BLOCKED (validation refuses such a graph; compile is still checked
    here to prove the compiler itself is inert)."""
    graph = make_graph()
    graph["nodes"].append(node("knowledge", "1", config={"knowledge_id": "kb-1"}))
    graph["edges"].append(edge("agent-root", "knowledge-1", "knowledge"))
    document = parse_graph_document(graph)
    config = compile_execution_config(
        agent_id="agent-1", revision_id="rev-1", graph=document,
        resolved_dependencies={"knowledge_adapter_status": "BLOCKED"},
    )
    assert config.knowledge_ids == ()
    config2 = compile_execution_config(
        agent_id="agent-1", revision_id="rev-1", graph=document,
        resolved_dependencies={"knowledge_adapter_status": "EXECUTABLE"},
    )
    assert config2.knowledge_ids == ("kb-1",)


def test_config_hash_reflects_capability_change() -> None:
    with_cua_graph = make_graph()
    with_cua_graph["nodes"].append(node("cua", "1"))
    with_cua_graph["edges"].append(edge("agent-root", "cua-1", "cua"))
    a = _compile(make_graph())
    b = _compile(with_cua_graph)
    assert config_hash(a) != config_hash(b)


# ---------------------------------------------------------------------------
# Scope refusal (Fix 3)
# ---------------------------------------------------------------------------


def test_scope_refusal_user_scoped_in_shared() -> None:
    request = RuntimeScopeRequest(
        runtime_scope=ScopeKind.SHARED,
        connectors={"github": ScopeKind.USER_SCOPED},
    )
    with pytest.raises(DesignerError) as excinfo:
        refuse_mismatched_scope(request)
    assert "github" in str(excinfo.value)


def test_scope_refusal_allows_shared_connectors_in_shared_runtime() -> None:
    refuse_mismatched_scope(
        RuntimeScopeRequest(
            runtime_scope=ScopeKind.SHARED,
            connectors={"docs": ScopeKind.SHARED},
        )
    )


# ---------------------------------------------------------------------------
# Runtime pool (Safety 2 + Fix 3 + single-flight)
# ---------------------------------------------------------------------------


def _pool_key(scope_key: str = "shared") -> RuntimeCacheKey:
    return RuntimeCacheKey(
        agent_id="agent-1", revision_id="rev-1",
        execution_epoch=1, credential_scope_key=scope_key,
    )


class FakeRuntime:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_pool_caches_by_key_and_refcounts() -> None:
    builds: list[str] = []

    async def builder(key: RuntimeCacheKey, config: ExecutionConfig):
        builds.append(key.credential_scope_key)
        return FakeRuntime(), {"cred-1": 1}

    pool = RuntimePool(builder)
    config = _compile(make_graph())
    lease_a = await pool.acquire(
        agent_id="agent-1", revision_id="rev-1", execution_epoch=1,
        scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=config,
    )
    lease_b = await pool.acquire(
        agent_id="agent-1", revision_id="rev-1", execution_epoch=1,
        scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=config,
    )
    assert builds == ["shared"]  # one build, two leases
    await lease_a.release()
    await lease_b.release()
    await pool.aclose()


async def test_pool_user_scoped_keys_differ_per_user() -> None:
    """Souvik's GitHub credential must never serve another user (Fix 3):
    different user -> different cache key -> different runtime."""
    builds: list[str] = []

    async def builder(key: RuntimeCacheKey, config: ExecutionConfig):
        builds.append(key.credential_scope_key)
        return FakeRuntime(), {}

    pool = RuntimePool(builder)
    config = _compile(make_graph())
    await (
        await pool.acquire(
            agent_id="agent-1", revision_id="rev-1", execution_epoch=1,
            scope=ScopeKind.USER_SCOPED, actor_id="souvik", run_id=None,
            config=config,
        )
    ).release()
    await (
        await pool.acquire(
            agent_id="agent-1", revision_id="rev-1", execution_epoch=1,
            scope=ScopeKind.USER_SCOPED, actor_id="other", run_id=None,
            config=config,
        )
    ).release()
    assert sorted(builds) == ["user:other", "user:souvik"]
    await pool.aclose()


async def test_pool_credential_rotation_marks_stale_and_rebuilds() -> None:
    """Safety 2: rotation/revocation never serves from a stale runtime."""
    builds: list[int] = []

    async def builder(key: RuntimeCacheKey, config: ExecutionConfig):
        builds.append(key.execution_epoch)
        return FakeRuntime(), {"cred-1": 1}

    pool = RuntimePool(builder)
    config = _compile(make_graph())
    lease1 = await pool.acquire(
        agent_id="agent-1", revision_id="rev-1", execution_epoch=1,
        scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=config,
    )
    await lease1.release()

    drained = await pool.drain_stale("cred-1")
    assert drained == 1
    lease2 = await pool.acquire(
        agent_id="agent-1", revision_id="rev-1", execution_epoch=1,
        scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=config,
    )
    await lease2.release()
    assert len(builds) == 2  # rebuilt after rotation, never reused
    await pool.aclose()


async def test_pool_single_flight_same_key() -> None:
    builds: list[str] = []

    async def builder(key: RuntimeCacheKey, config: ExecutionConfig):
        await asyncio.sleep(0.01)  # simulate slow build
        builds.append("built")
        return FakeRuntime(), {}

    pool = RuntimePool(builder)
    config = _compile(make_graph())
    results = await asyncio.gather(*[
        pool.acquire(
            agent_id="agent-1", revision_id="rev-1", execution_epoch=1,
            scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=config,
        )
        for _ in range(5)
    ])
    assert len(builds) == 1  # one flight, five leases
    assert len(results) == 5
    await pool.aclose()


async def test_pool_idle_close() -> None:
    async def builder(key: RuntimeCacheKey, config: ExecutionConfig):
        return FakeRuntime(), {}

    pool = RuntimePool(builder, idle_close_seconds=100.0)
    config = _compile(make_graph())
    lease = await pool.acquire(
        agent_id="agent-1", revision_id="rev-1", execution_epoch=1,
        scope=ScopeKind.SHARED, actor_id=None, run_id=None, config=config,
    )
    await lease.release()
    closed = await pool.close_idle(now=10_000.0)
    assert closed == 1
    await pool.aclose()


# ---------------------------------------------------------------------------
# Vion bootstrap (Fix 1, R21)
# ---------------------------------------------------------------------------


async def test_bootstrap_creates_vion_agent_active_revision_and_access(
    designer_db: Any,
) -> None:
    from scripts.bootstrap_designer import bootstrap

    result = await bootstrap(
        "postgresql://assistant:assistant@127.0.0.1:5433/assistant",
        cua_enabled=False,
        model_provider="openrouter",
        model_name="z-ai/glm-5.3-flash",
    )
    assert result["alias"] == "personal-assistant-v1"
    agent = await designer_db.get_agent(result["agent_id"])
    assert agent["slug"] == VION_SLUG
    assert agent["active_revision_id"] == result["revision_id"]
    access = await designer_db.get_agent_access(result["agent_id"], "*")
    assert access is not None and access["can_use"] is True
    config = await designer_db.get_agent_config(result["agent_id"])
    assert config["legacy"] is True


async def test_bootstrap_is_idempotent(designer_db: Any) -> None:
    from scripts.bootstrap_designer import bootstrap

    url = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
    first = await bootstrap(url, cua_enabled=False,
                            model_provider="openrouter", model_name="m1")
    second = await bootstrap(url, cua_enabled=False,
                             model_provider="openrouter", model_name="m1")
    assert first["agent_id"] == second["agent_id"]
    assert first["revision_id"] == second["revision_id"]
    revisions = await designer_db.list_revisions(first["agent_id"])
    assert len(revisions) == 1  # no duplicate revision rows


def test_vion_graph_validates_with_cua() -> None:
    payload = build_vion_graph(
        cua_enabled=True, model_provider="openrouter", model_name="m"
    )
    report = validate_graph(parse_graph_document(payload))
    assert report.ok, report.to_dict()
    config = compile_execution_config(
        agent_id="vion", revision_id="r1", graph=parse_graph_document(payload)
    )
    assert config.allows_native_dispatch() is True  # CUA preserved from Phase 1


# ---------------------------------------------------------------------------
# Migration 003: agent-scoped dedup (R20/R21)
# ---------------------------------------------------------------------------


async def test_migration_003_agent_scoped_dedup(require_postgres: None) -> None:
    """Legacy rows keep dedup semantics; same user+message under a
    DIFFERENT agent is now a distinct run (designer agents)."""
    import psycopg
    from scripts.migrate_designer import apply_migrations

    url = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
    await apply_migrations(url, __import__("pathlib").Path("migrations/designer"))
    async with await psycopg.AsyncConnection.connect(url) as conn:
        # Legacy identity (agent_id '') dedups on insert conflict.
        await conn.execute(
            "INSERT INTO run_registry (run_id, user_id, chat_id, user_message_id, "
            "request_digest, agent_id) VALUES ('mig-t1', 'u', 'c', 'msg-1', 'd', '') "
            "ON CONFLICT DO NOTHING"
        )
        duplicate = await conn.execute(
            "INSERT INTO run_registry (run_id, user_id, chat_id, user_message_id, "
            "request_digest, agent_id) VALUES ('mig-t2', 'u', 'c', 'msg-1', 'd', '') "
            "ON CONFLICT (user_id, agent_id, user_message_id) DO NOTHING"
        )
        assert duplicate.rowcount == 0  # deduped under legacy scope
        # Same message under a different agent is a distinct run.
        distinct = await conn.execute(
            "INSERT INTO run_registry (run_id, user_id, chat_id, user_message_id, "
            "request_digest, agent_id) VALUES ('mig-t3', 'u', 'c', 'msg-1', 'd', 'agent-9') "
            "ON CONFLICT (user_id, agent_id, user_message_id) DO NOTHING"
        )
        assert distinct.rowcount == 1
        await conn.execute(
            "DELETE FROM run_registry WHERE run_id IN ('mig-t1', 'mig-t3')"
        )
