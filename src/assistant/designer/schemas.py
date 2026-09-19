"""Designer graph and revision contracts (frozen plan §2, C4, Clar 3).

``GraphDocument`` is the versioned, transport-level graph format. Domain
configuration lives in node ``data``; React Flow layout (positions,
viewport) is carried separately and never contributes to the semantic
hash (Fix: layout-only saves must not replace runtimes).

``schema_version`` starts at 1; loading rejects unsupported future
versions instead of guessing (C4).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

GRAPH_SCHEMA_VERSION = 1

#: Node kinds the V1 compiler understands (requirements §5-6).
NodeKind = Literal[
    "agent", "model", "prompt", "skill", "memory", "context",
    "knowledge", "tool", "mcp", "cua", "terminal",
]

#: Typed edge ports (R06: an edge attaches a resource to an Agent port).
EdgePort = Literal[
    "model", "prompt", "skill", "memory", "context",
    "knowledge", "tool", "mcp", "cua", "terminal",
]

#: Memory node kinds (R06: at most one per kind).
MemoryKind = Literal["thread", "user", "project"]

MAX_NODES = 200
MAX_GRAPH_BYTES = 1024 * 1024  # 1 MB payload cap


class UnsupportedSchemaError(ValueError):
    """The graph declares a schema_version this gateway does not support."""


class NodeData(BaseModel):
    """Domain configuration of one node. References only — never secrets
    (R09: graph JSON contains credential references, never values)."""

    enabled: bool = True
    # Opaque per-node configuration; validated per kind in validation.py.
    # Secret values are forbidden here and rejected by validation.
    config: dict[str, Any] = {}


class GraphNode(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    type: str  # validated against NodeKind in validation.py
    position: dict[str, float] = {}
    data: NodeData = NodeData()


class GraphEdge(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    source: str  # agent root node id (V1: root-to-resource only)
    target: str  # resource node id
    sourceHandle: str = "root"
    targetHandle: str  # the typed port (EdgePort)


class Viewport(BaseModel):
    x: float = 0
    y: float = 0
    zoom: float = 1


class GraphDocument(BaseModel):
    """The versioned graph document saved as an immutable revision."""

    model_config = {"extra": "forbid"}

    schema_version: int = GRAPH_SCHEMA_VERSION
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    viewport: Viewport = Viewport()


def parse_graph_document(payload: dict[str, Any]) -> GraphDocument:
    """Parse and version-check an incoming graph payload.

    Future schema versions are rejected loudly (C4) rather than silently
    guessed. A missing version field is also rejected: callers must state
    what they are sending.
    """
    if "schema_version" not in payload:
        raise UnsupportedSchemaError("graph payload is missing schema_version")
    version = payload["schema_version"]
    if not isinstance(version, int) or version != GRAPH_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"unsupported graph schema_version {version!r}; "
            f"this gateway supports only {GRAPH_SCHEMA_VERSION}"
        )
    return GraphDocument.model_validate(payload)
