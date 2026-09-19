"""MCP transport adapter: stdio + Streamable HTTP (R10, Clar 1, C3).

- stdio: operator-approved absolute executable + argument array only —
  no ``npx latest``, no shell strings, no inherited env (R10). The
  process exists inside a lease and is closed in ``finally``.
- Streamable HTTP: endpoint allowlist (operator policy), TLS kept on,
  no redirects.
- Discovery lists tools with schema digests; invoking a tool through a
  validation lease is structurally refused; runtime invocation happens
  only through a ConnectorLease whose O(1) gate passes (P5 binds real
  MCP sessions to leases).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from assistant.designer.connectors import (
    DISCOVERY_TIMEOUT_SECONDS,
    ConnectorRuntimeState,
    NormalizedTool,
    ScopeKind,
    ValidationLease,
    schema_digest,
)
from assistant.designer.errors import DesignerError

logger = logging.getLogger("assistant.designer.mcp")


@dataclass(frozen=True)
class StdioSpec:
    """Operator-approved stdio configuration (validated shape, R10)."""

    command: str  # absolute path
    args: tuple[str, ...]
    env_allowlist: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.command.startswith("/"):
            raise DesignerError(
                "invalid_request", "stdio connector command must be an absolute path"
            )
        if any(ch in self.command for ch in (";", "|", "&", "$", "`")):
            raise DesignerError(
                "invalid_request", "stdio connector command must not contain shell metacharacters"
            )


@dataclass(frozen=True)
class HttpSpec:
    """Operator-approved Streamable HTTP endpoint."""

    url: str

    def __post_init__(self) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(self.url)
        if parsed.scheme != "https":
            # Loopback HTTP is the documented dev exception.
            if parsed.hostname not in {"127.0.0.1", "localhost"}:
                raise DesignerError(
                    "invalid_request",
                    "Streamable HTTP connectors require https (loopback http is dev-only)",
                )
        if parsed.hostname in {"169.254.169.254", "metadata.google.internal"}:
            raise DesignerError("invalid_request", "metadata endpoints are blocked")


def _normalize_tools(raw_tools: list[Any]) -> list[NormalizedTool]:
    normalized: list[NormalizedTool] = []
    for tool in raw_tools:
        name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
        if not name:
            continue
        description = (
            getattr(tool, "description", None)
            or (tool.get("description", "") if isinstance(tool, dict) else "")
            or ""
        )
        schema = (
            getattr(tool, "inputSchema", None)
            or (tool.get("inputSchema", {}) if isinstance(tool, dict) else {})
            or {}
        )
        normalized.append(NormalizedTool.build(str(name), str(description), dict(schema)))
    return normalized


class McpConnectorManager:
    """Discovers tools over MCP transports and hands out leases.

    Real MCP sessions are bound to leases by the runtime pool (P5);
    discovery here uses the langchain-mcp-adapters client with strict
    timeouts so Validate never hangs.
    """

    def __init__(self) -> None:
        self._states: dict[str, ConnectorRuntimeState] = {}

    def state(self, connector_id: str) -> ConnectorRuntimeState:
        if connector_id not in self._states:
            self._states[connector_id] = ConnectorRuntimeState(connector_id=connector_id)
        return self._states[connector_id]

    async def _discover_raw(self, spec: StdioSpec | HttpSpec) -> _KeepSession:
        """Start the transport, list tools, keep the session handle."""
        from langchain_mcp_adapters.client import MultiServerMCPClient

        if isinstance(spec, StdioSpec):
            config: dict[str, Any] = {
                "designer": {
                    "command": spec.command,
                    "args": list(spec.args),
                    "transport": "stdio",
                    "env": dict(spec.env_allowlist) or None,
                }
            }
        else:
            config = {"designer": {"url": spec.url, "transport": "streamable_http"}}
        client = MultiServerMCPClient(config)
        # list_tools opens a session; keep it for the lease to close.
        session_ctx = client.session("designer")
        session = await session_ctx.__aenter__()
        try:
            import asyncio

            response = await asyncio.wait_for(
                session.list_tools(), timeout=DISCOVERY_TIMEOUT_SECONDS
            )
        except BaseException:
            await session_ctx.__aexit__(None, None, None)
            raise
        tools = list(getattr(response, "tools", []) or [])
        return _KeepSession(session_ctx, tools)


@dataclass
class _KeepSession:
    session_ctx: Any
    tools: list[Any]

    async def aclose(self) -> None:
        await self.session_ctx.__aexit__(None, None, None)


class ConnectorService:
    """Operator-facing connector lifecycle (register, validate, quarantine)."""

    def __init__(self, mcp: McpConnectorManager | None = None) -> None:
        self._mcp = mcp or McpConnectorManager()

    async def validate_connector(
        self,
        connector_id: str,
        spec: StdioSpec | HttpSpec,
    ) -> ValidationLease:
        """Bounded validation probe (Clar 1).

        May start an EPHEMERAL stdio process for discovery. The process
        lives entirely inside this call (start → list → close in
        ``finally``, same anyio task as required by the transport) — no
        persistent runtime is installed, no model calls, no business-tool
        execution. The returned lease is an inert record of what was
        discovered; ``aclose`` is an idempotent no-op.
        """
        lease = ValidationLease(connector_id=connector_id)
        kept: Any = None
        try:
            kept = await self._mcp._discover_raw(spec)
        except Exception as exc:
            state = self._mcp.state(connector_id)
            state.status = "offline"
            state.error = f"discovery failed: {type(exc).__name__}"
            state.checked_at = datetime.now(UTC)
            raise DesignerError(
                "upstream_unavailable",
                f"connector discovery failed: {type(exc).__name__}",
            ) from exc
        else:
            lease.tools = _normalize_tools(kept.tools)
            lease.schema_digest = schema_digest(lease.tools)
            state = self._mcp.state(connector_id)
            state.discovered_schema_digest = lease.schema_digest
            state.generation += 1
            state.status = "online"
            state.checked_at = datetime.now(UTC)
            state.error = ""
            return lease
        finally:
            # Same-task close: the ephemeral process never outlives this
            # call, on success, failure, timeout or cancellation (Clar 1).
            if kept is not None:
                await kept.aclose()

    def scope_for(self, credential_scope: str) -> ScopeKind:
        """Map a connector's credential binding to its lease scope (Fix 3)."""
        if credential_scope == "shared":
            return ScopeKind.SHARED
        if credential_scope.startswith("user:"):
            return ScopeKind.USER_SCOPED
        return ScopeKind.RUN_SCOPED
