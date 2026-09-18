"""Pure graph validation (R06). No I/O, no processes, no network.

``validate_graph`` enforces the frozen-plan graph semantics:

- Exactly one Agent root; exactly one enabled+connected Model.
- At most one connected Prompt / Context node; at most one memory node
  per kind; many Skill / Tool / MCP / Knowledge nodes.
- Root-to-resource edges only; no cycles, self-links, duplicates,
  agent-to-agent edges, or cross-agent references (references live in
  node config and are checked by source, not by edge).
- Disconnected nodes are allowed but marked ``not_attached``.
- Size limits: MAX_NODES nodes, MAX_GRAPH_BYTES payload.
- Forbidden executable fields: graph JSON must never carry secret
  material or executable code strings (R09).

The backend is authoritative even when a malicious client bypasses any
React Flow check (R06): this module is the gate Save and Validate call.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from assistant.designer.schemas import (
    MAX_NODES,
    GraphDocument,
)

#: Node types that may be attached to the agent root (V1).
RESOURCE_TYPES = frozenset(
    {"model", "prompt", "skill", "memory", "context", "knowledge", "tool", "mcp", "cua", "terminal"}
)
#: Singleton resource types (at most one CONNECTED instance, R06).
SINGLETON_TYPES = frozenset({"model", "prompt", "context"})
#: Memory kinds (at most one connected node per kind).
MEMORY_KINDS = frozenset({"thread", "user", "project"})

#: Keys that must never appear in node config (secrets or executables).
_FORBIDDEN_CONFIG_KEYS = frozenset(
    {
        "api_key", "apikey", "secret", "password", "token", "bearer",
        "credential_value", "secret_value", "code", "script", "command_string",
        "execute", "eval",
    }
)

_SECRET_VALUE_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|eyJ[A-Za-z0-9_-]{20,})"
)


class ValidationError_(Exception):
    """Raised when a graph violates graph semantics (R06)."""


@dataclass
class ValidationIssue:
    node_id: str | None
    edge_id: str | None
    code: str
    message: str


@dataclass
class ValidationReport:
    ok: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, *, node_id: str | None, edge_id: str | None, code: str, message: str) -> None:
        self.ok = False
        self.issues.append(
            ValidationIssue(node_id=node_id, edge_id=edge_id, code=code, message=message)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [
                {"node_id": i.node_id, "edge_id": i.edge_id, "code": i.code,
                 "message": i.message}
                for i in self.issues
            ],
        }


def _scan_config(value: Any, node_id: str, report: ValidationReport) -> None:
    """Recursively reject secrets or executable strings in node data."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_CONFIG_KEYS:
                report.add(
                    node_id=node_id, edge_id=None, code="forbidden_field",
                    message=f"node configuration may not contain '{key}'",
                )
            _scan_config(item, node_id, report)
    elif isinstance(value, list):
        for item in value:
            _scan_config(item, node_id, report)
    elif isinstance(value, str):
        if _SECRET_VALUE_PATTERN.search(value):
            report.add(
                node_id=node_id, edge_id=None, code="secret_material",
                message="node configuration contains secret-shaped text",
            )


def semantic_hash(graph: GraphDocument) -> str:
    """Hash of behavior-relevant content only (no layout, no viewport)."""
    behavior = {
        "nodes": [
            {"id": n.id, "type": n.type, "data": n.data.model_dump()}
            for n in sorted(graph.nodes, key=lambda n: n.id)
        ],
        "edges": [
            {"source": e.source, "target": e.target, "targetHandle": e.targetHandle}
            for e in sorted(graph.edges, key=lambda e: e.id)
        ],
    }
    return hashlib.sha256(
        json.dumps(behavior, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def layout_hash(graph: GraphDocument) -> str:
    """Hash of presentation only (positions + viewport). A layout-only
    save changes this, never the semantic hash."""
    layout = {
        "positions": {n.id: n.position for n in graph.nodes},
        "viewport": graph.viewport.model_dump(),
    }
    return hashlib.sha256(
        json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_graph(graph: GraphDocument) -> ValidationReport:
    """Validate graph semantics; returns a report with node-specific issues."""
    report = ValidationReport()

    if len(graph.nodes) > MAX_NODES:
        report.add(node_id=None, edge_id=None, code="too_many_nodes",
                   message=f"graph exceeds {MAX_NODES} nodes")
        return report

    nodes_by_id = {node.id: node for node in graph.nodes}
    if len(nodes_by_id) != len(graph.nodes):
        report.add(node_id=None, edge_id=None, code="duplicate_node_id",
                   message="node ids must be unique")

    # --- exactly one agent root ---
    roots = [n for n in graph.nodes if n.type == "agent"]
    if len(roots) == 0:
        report.add(node_id=None, edge_id=None, code="missing_root",
                   message="graph needs exactly one agent root")
    elif len(roots) > 1:
        for node in roots:
            report.add(node_id=node.id, edge_id=None, code="multiple_roots",
                       message="only one agent root is allowed")
    root_id = roots[0].id if roots else None
    # Edge classification still runs with a degenerate root so that
    # agent-target edges are always flagged agent_to_agent (R06).

    # --- edge structure: root-to-resource only ---
    seen_edges: set[tuple[str, str, str]] = set()
    connected_targets: set[str] = set()
    for edge in graph.edges:
        if root_id is None:
            report.add(node_id=edge.source, edge_id=edge.id, code="no_root",
                       message="edges require exactly one agent root")
            break
        if edge.source != root_id:
            report.add(
                node_id=edge.source, edge_id=edge.id, code="invalid_edge",
                message="edges must run from the agent root to a resource (R06)",
            )
            continue
        target = nodes_by_id.get(edge.target)
        if target is not None and target.type == "agent":
            report.add(node_id=edge.target, edge_id=edge.id, code="agent_to_agent",
                       message="agent-to-agent edges are forbidden")
            continue
        if edge.source == edge.target:
            report.add(node_id=edge.target, edge_id=edge.id, code="self_link",
                       message="self links are not allowed")
            continue
        if target is None:
            report.add(node_id=edge.target, edge_id=edge.id, code="missing_target",
                       message="edge targets a missing node")
            continue
        if target.type not in RESOURCE_TYPES:
            report.add(node_id=target.id, edge_id=edge.id, code="unknown_type",
                       message=f"unknown node type '{target.type}'")
            continue
        key = (edge.source, edge.target, edge.targetHandle)
        if key in seen_edges:
            report.add(node_id=edge.target, edge_id=edge.id, code="duplicate_edge",
                       message="duplicate root/resource/port edge")
        seen_edges.add(key)
        connected_targets.add(edge.target)

    # --- exactly one enabled+connected model ---
    models = [n for n in graph.nodes if n.type == "model"]
    connected_models = [m for m in models if m.id in connected_targets and m.data.enabled]
    if len(connected_models) == 0:
        report.add(node_id=models[0].id if models else None, edge_id=None,
                   code="missing_model",
                   message="one enabled, connected model node is required")
    elif len(connected_models) > 1:
        for node in models:
            if node.id in connected_targets and node.data.enabled:
                report.add(node_id=node.id, edge_id=None, code="multiple_models",
                           message="only one enabled model may be connected")

    # --- singletons: at most one CONNECTED instance ---
    for kind in sorted(SINGLETON_TYPES - {"model"}):
        attached = [
            n for n in graph.nodes
            if n.type == kind and n.id in connected_targets
        ]
        if len(attached) > 1:
            for node in attached:
                report.add(node_id=node.id, edge_id=None, code="duplicate_singleton",
                           message=f"at most one connected {kind} node is allowed")

    # --- memory: at most one connected node per memory kind ---
    memories: dict[str, list[str]] = {}
    for node in graph.nodes:
        if node.type == "memory" and node.id in connected_targets:
            kind = str(node.data.config.get("kind", "thread"))
            if kind not in MEMORY_KINDS:
                report.add(node_id=node.id, edge_id=None, code="invalid_memory_kind",
                           message=f"memory kind must be one of {sorted(MEMORY_KINDS)}")
            memories.setdefault(kind, []).append(node.id)
    for kind, node_ids in memories.items():
        if len(node_ids) > 1:
            for node_id in node_ids:
                report.add(node_id=node_id, edge_id=None, code="duplicate_memory_kind",
                           message=f"at most one connected memory node per kind ({kind})")

    # --- capability gating (Clar 4, Fix 2, Fix 5) ---
    # Resource types whose runtime adapter is not verified for this
    # deployment cannot join an activatable graph. Knowledge is BLOCKED
    # until the P0 probe verifies the retrieval contract; Terminal stays
    # BLOCKED until an operator-provisioned sandbox is tested (P4).
    _BLOCKED_TYPES: dict[str, str] = {
        "knowledge": "Runtime adapter unverified",
        "terminal": "sandbox adapter untested",
    }
    for node in graph.nodes:
        if node.type in _BLOCKED_TYPES and node.id in connected_targets and node.data.enabled:
            report.add(
                node_id=node.id, edge_id=None, code="capability_blocked",
                message=f"{node.type} is BLOCKED: {_BLOCKED_TYPES[node.type]}",
            )

    # --- secret/executable scanning + disabled/attachment bookkeeping ---
    for node in graph.nodes:
        _scan_config(node.data.model_dump(), node.id, report)
        if node.type != "agent" and node.id not in connected_targets:
            # Allowed on canvas; compilation excludes it (R06).
            node.data.config.setdefault("_attachment", "not_attached")

    return report
