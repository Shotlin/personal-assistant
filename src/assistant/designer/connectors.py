"""Connector lifecycle: MCP (stdio + Streamable HTTP) and OpenAPI tools.

Implements the frozen plan's connector contract (P4 + C3 + Clar 1 +
Fix 3 + Safety 2):

- ``ConnectorRuntimeState``: per-connector discovered schema digest +
  generation + status. Schema discovery happens ONLY at connect,
  Validate/Prepare, reconnect, bounded refresh, or explicit operator
  request — never on the dispatch hot path.
- The activated revision pins ``expected_schema_digest`` +
  ``expected_connector_generation``. The hot path performs a local O(1)
  comparison only (Safety 2 / C3); a mismatch quarantines the connector
  immediately (block dispatch, emit a Live warning event, require
  Review Changes → Validate Again).
- Credential scope (Fix 3): every lease is SHARED, USER_SCOPED, or
  RUN_SCOPED; compilation refuses user-scoped connectors in shared
  runtimes.
- Ephemeral validation leases (Clar 1): a stdio validation may start the
  server process inside a lease — only required credentials, strict
  timeout, closed in ``finally``; discovered tools are listed, never
  executed; no model calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

logger = logging.getLogger("assistant.designer.connectors")

TransportKind = Literal["stdio", "streamable_http"]

#: Bounded discovery budget (Clar 1: strict startup/discovery timeout).
DISCOVERY_TIMEOUT_SECONDS = 20.0


class Quarantined(Exception):
    """Raised by the O(1) dispatch gate when a connector changed."""


class ScopeKind(StrEnum):
    SHARED = "shared"
    USER_SCOPED = "user_scoped"
    RUN_SCOPED = "run_scoped"


@dataclass
class ConnectorRuntimeState:
    """Live state of one connector (C3). Discoverers update this; the
    dispatch hot path only reads it (local O(1) comparison)."""

    connector_id: str
    discovered_schema_digest: str = ""
    generation: int = 0
    status: Literal["offline", "online", "quarantined"] = "offline"
    checked_at: datetime | None = None
    error: str = ""
    quarantine_reason: str = ""


@dataclass(frozen=True)
class NormalizedTool:
    """One discovered tool, pinned by schema digest. New tools are
    selected OFF by default (R10); the review flow enables them."""

    name: str
    description: str
    input_schema: dict[str, Any]
    schema_digest: str

    @classmethod
    def build(cls, name: str, description: str, input_schema: dict[str, Any]) -> NormalizedTool:
        digest = hashlib.sha256(
            json.dumps(
                {"name": name, "input_schema": input_schema},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls(name=name, description=description, input_schema=input_schema,
                   schema_digest=digest)


def schema_digest(tools: list[NormalizedTool]) -> str:
    """Stable digest over the selected tool set (order-insensitive)."""
    material = sorted(t.schema_digest for t in tools)
    return hashlib.sha256("|".join(material).encode("utf-8")).hexdigest()


@dataclass
class ConnectorLease:
    """A bound connector session. ``can_invoke`` re-checks selection AND
    the live runtime state at dispatch (the local O(1) gate)."""

    connector_id: str
    scope: ScopeKind
    tools: list[NormalizedTool] = field(default_factory=list)
    state: ConnectorRuntimeState | None = None
    expected_digest: str = ""
    expected_generation: int = 0
    _resource: Any = None  # underlying MCP session/transport (P5 binds it)

    def can_invoke(self, tool_name: str) -> bool:
        """Dispatch gate: selected tool + runtime state matches the
        activated revision's pinned digest/generation (C3, O(1) local)."""
        if self.state is not None:
            if self.state.status == "quarantined":
                return False
            if self.state.discovered_schema_digest != self.expected_digest:
                return False
            if self.state.generation != self.expected_generation:
                return False
        return any(t.name == tool_name for t in self.tools)

    async def aclose(self) -> None:
        resource = self._resource
        self._resource = None
        closer = getattr(resource, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001 -- close must never raise
                logger.warning("connector_lease_close_failed", extra={
                    "event": "connector_lease_close_failed",
                    "connector_id": self.connector_id,
                })


@dataclass
class ValidationLease:
    """Ephemeral validation lease (Clar 1). Holds a possibly-live stdio
    process; closed in ``finally`` by the caller. Discovered tools are
    listed only — invoking them through a validation lease is refused."""

    connector_id: str
    tools: list[NormalizedTool] = field(default_factory=list)
    schema_digest: str = ""
    _resource: Any = None
    _closed: bool = False

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> None:
        raise RuntimeError(
            "validation leases list tools; they never execute them (Clar 1)"
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        resource = self._resource
        self._resource = None
        closer = getattr(resource, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001
                logger.warning("validation_lease_close_failed", extra={
                    "event": "validation_lease_close_failed",
                    "connector_id": self.connector_id,
                })
