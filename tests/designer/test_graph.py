"""P2 tests: graph validation (R06), immutable drafts, ETag CAS (R12).

The plan-doc minimum regressions are covered verbatim plus the additional
cases the plan mandates (missing/two roots, missing/two models, wrong edge
types, duplicates, disconnected nodes, size limits, forbidden fields,
stale writes, layout vs semantic hash).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from assistant.designer.schemas import UnsupportedSchemaError, parse_graph_document
from assistant.designer.validation import layout_hash, semantic_hash, validate_graph


def make_graph(**overrides: Any) -> dict[str, Any]:
    """A minimal valid graph: agent root + one enabled model."""
    graph = {
        "schema_version": 1,
        "nodes": [
            {"id": "agent-root", "type": "agent", "position": {"x": 0, "y": 0},
             "data": {"enabled": True, "config": {}}},
            {"id": "model-1", "type": "model", "position": {"x": 300, "y": 0},
             "data": {"enabled": True, "config": {}}},
        ],
        "edges": [
            {"id": "e1", "source": "agent-root", "target": "model-1",
             "sourceHandle": "root", "targetHandle": "model"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    for key in overrides:
        if key == "nodes":
            graph["nodes"] = overrides["nodes"]
        elif key == "edges":
            graph["edges"] = overrides["edges"]
    return graph


def node(kind: str, node_id: str, *, enabled: bool = True, config: dict | None = None) -> dict:
    return {
        "id": f"{kind}-{node_id}", "type": kind,
        "position": {"x": 100, "y": 100},
        "data": {"enabled": enabled, "config": config or {}},
    }


def edge(source: str, target: str, handle: str) -> dict:
    return {"id": f"e-{source}-{target}", "source": source, "target": target,
            "sourceHandle": "root", "targetHandle": handle}


# ---------------------------------------------------------------------------
# Schema versioning (C4)
# ---------------------------------------------------------------------------


def test_future_schema_version_rejected() -> None:
    with pytest.raises(UnsupportedSchemaError):
        parse_graph_document({**make_graph(), "schema_version": 2})


def test_missing_schema_version_rejected() -> None:
    payload = make_graph()
    del payload["schema_version"]
    with pytest.raises(UnsupportedSchemaError):
        parse_graph_document(payload)


def test_current_schema_version_accepted() -> None:
    graph = parse_graph_document(make_graph())
    assert graph.schema_version == 1


# ---------------------------------------------------------------------------
# Graph semantics (R06)
# ---------------------------------------------------------------------------


def test_valid_graph_passes() -> None:
    report = validate_graph(parse_graph_document(make_graph()))
    assert report.ok, report.to_dict()


def test_missing_root_fails() -> None:
    graph = make_graph()
    graph["nodes"] = [n for n in graph["nodes"] if n["type"] != "agent"]
    graph["edges"] = []
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "missing_root" for i in report.issues)


def test_two_roots_fail() -> None:
    graph = make_graph()
    graph["nodes"].append(
        {"id": "agent-root-2", "type": "agent", "position": {},
         "data": {"enabled": True, "config": {}}}
    )
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "multiple_roots" for i in report.issues)


def test_missing_connected_model_fails() -> None:
    graph = make_graph()
    graph["edges"] = []
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "missing_model" for i in report.issues)


def test_two_connected_models_fail() -> None:
    graph = make_graph()
    graph["nodes"].append(node("model", "2"))
    graph["edges"].append(edge("agent-root", "model-2", "model"))
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "multiple_models" for i in report.issues)


def test_agent_to_agent_edge_fails() -> None:
    graph = make_graph()
    graph["nodes"].append(
        {"id": "agent-2", "type": "agent", "position": {}, "data": {"enabled": True, "config": {}}}
    )
    graph["edges"].append(edge("agent-root", "agent-2", "agent"))
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "agent_to_agent" for i in report.issues)


def test_edge_from_resource_fails() -> None:
    graph = make_graph()
    graph["edges"].append(edge("model-1", "agent-root", "root"))
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "invalid_edge" for i in report.issues)


def test_duplicate_edge_fails() -> None:
    graph = make_graph()
    graph["edges"].append(edge("agent-root", "model-1", "model"))
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "duplicate_edge" for i in report.issues)


def test_disconnected_nodes_marked_not_attached_but_valid() -> None:
    graph = make_graph()
    graph["nodes"].append(node("skill", "1"))
    graph_document = parse_graph_document(graph)
    report = validate_graph(graph_document)
    assert report.ok, report.to_dict()
    skill = next(n for n in graph_document.nodes if n.type == "skill")
    assert skill.data.config.get("_attachment") == "not_attached"


def test_two_connected_prompt_nodes_fail() -> None:
    graph = make_graph()
    graph["nodes"].append(node("prompt", "1"))
    graph["nodes"].append(node("prompt", "2"))
    graph["edges"].append(edge("agent-root", "prompt-1", "prompt"))
    graph["edges"].append(edge("agent-root", "prompt-2", "prompt"))
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "duplicate_singleton" for i in report.issues)


def test_two_connected_memory_nodes_same_kind_fail() -> None:
    graph = make_graph()
    graph["nodes"].append(node("memory", "1", config={"kind": "user"}))
    graph["nodes"].append(node("memory", "2", config={"kind": "user"}))
    graph["edges"].append(edge("agent-root", "memory-1", "memory"))
    graph["edges"].append(edge("agent-root", "memory-2", "memory"))
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "duplicate_memory_kind" for i in report.issues)


def test_secret_material_in_config_fails() -> None:
    graph = make_graph()
    graph["nodes"][1]["data"]["config"] = {"api_key": "sk-1234567890abcdef1234"}
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "forbidden_field" for i in report.issues)


def test_secret_shaped_text_fails() -> None:
    graph = make_graph()
    graph["nodes"][1]["data"]["config"] = {"note": "Bearer abcdefghijklmnopqrstuv"}
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "secret_material" for i in report.issues)


def test_node_limit_enforced() -> None:
    graph = make_graph()
    graph["nodes"] = graph["nodes"] + [
        {"id": f"skill-{i}", "type": "skill", "position": {},
         "data": {"enabled": True, "config": {}}}
        for i in range(250)
    ]
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "too_many_nodes" for i in report.issues)


# ---------------------------------------------------------------------------
# Layout vs semantic hash
# ---------------------------------------------------------------------------


def test_layout_change_does_not_change_semantic_hash() -> None:
    doc_original = parse_graph_document(make_graph())
    moved = make_graph()
    moved["nodes"][0]["position"] = {"x": 999, "y": 888}
    moved["viewport"] = {"x": 5, "y": 5, "zoom": 1.5}
    doc_moved = parse_graph_document(moved)
    assert semantic_hash(doc_original) == semantic_hash(doc_moved)
    assert layout_hash(doc_original) != layout_hash(doc_moved)


# ---------------------------------------------------------------------------
# HTTP lifecycle: save never activates, ETag CAS (R12)
# ---------------------------------------------------------------------------


async def _create_and_save(
    api: httpx.AsyncClient, graph: dict[str, Any]
) -> tuple[dict[str, Any], httpx.Response]:
    created = await api.post("/designer/api/v1/agents", json={"name": "Test Agent"})
    assert created.status_code == 200, created.text
    agent = created.json()
    saved = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/revisions",
        json={"graph": graph},
        headers={"If-Match": agent["etag"]},
    )
    return agent, saved


async def test_save_does_not_activate(api: httpx.AsyncClient) -> None:
    agent, saved = await _create_and_save(api, make_graph())
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["revision_id"]
    fetched = await api.get(f"/designer/api/v1/agents/{agent['agent_id']}")
    assert fetched.json()["active_revision_id"] is None


async def test_stale_write_conflicts(api: httpx.AsyncClient) -> None:
    agent, _first = await _create_and_save(api, make_graph())
    # Save again with the ORIGINAL etag (now stale after the first save).
    stale = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/revisions",
        json={"graph": make_graph()},
        headers={"If-Match": agent["etag"]},
    )
    assert stale.status_code == 409


async def test_missing_if_match_conflicts(api: httpx.AsyncClient) -> None:
    created = await api.post("/designer/api/v1/agents", json={"name": "No ETag"})
    agent = created.json()
    missing = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/revisions",
        json={"graph": make_graph()},
    )
    assert missing.status_code == 409


async def test_invalid_graph_returns_node_specific_issues(api: httpx.AsyncClient) -> None:
    created = await api.post("/designer/api/v1/agents", json={"name": "Invalid"})
    agent = created.json()
    bad = await api.post(
        f"/designer/api/v1/agents/{agent['agent_id']}/revisions",
        json={"graph": {**make_graph(), "schema_version": 7}},
        headers={"If-Match": agent["etag"]},
    )
    assert bad.status_code == 400
    assert "schema_version" in bad.text


async def test_other_users_agent_is_not_found(
    api_b: httpx.AsyncClient, api: httpx.AsyncClient
) -> None:
    created = await api.post("/designer/api/v1/agents", json={"name": "Private"})
    agent = created.json()
    foreign = await api_b.get(f"/designer/api/v1/agents/{agent['agent_id']}")
    assert foreign.status_code == 404, "cross-user access must be 404, not 403-leak"


async def test_archive_removes_from_list(api: httpx.AsyncClient) -> None:
    created = await api.post("/designer/api/v1/agents", json={"name": "Doomed"})
    agent = created.json()
    archived = await api.delete(f"/designer/api/v1/agents/{agent['agent_id']}")
    assert archived.status_code == 200
    listed = await api.get("/designer/api/v1/agents")
    ids = [a["agent_id"] for a in listed.json()["agents"]]
    assert agent["agent_id"] not in ids
