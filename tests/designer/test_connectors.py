"""P4 tests: connectors (R10/R11, C3, Clar 1, Fix 3, Safety 2).

Real stdio transport contract test against the fixture MCP server plus
unit tests for the O(1) quarantine gate, scope mapping, and the
validation-lease execution refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from assistant.designer.adapters.mcp import HttpSpec, StdioSpec
from assistant.designer.connectors import (
    ConnectorLease,
    ConnectorRuntimeState,
    NormalizedTool,
    ScopeKind,
    ValidationLease,
    schema_digest,
)
from assistant.designer.errors import DesignerError

FIXTURE_SERVER = Path(__file__).parent / "fixture_mcp_server.py"


# ---------------------------------------------------------------------------
# Spec validation (R10: operator-approved stdio only)
# ---------------------------------------------------------------------------


def test_stdio_spec_rejects_relative_command() -> None:
    with pytest.raises(DesignerError):
        StdioSpec(command="npx", args=("latest", "something"))


def test_stdio_spec_rejects_shell_metacharacters() -> None:
    with pytest.raises(DesignerError):
        StdioSpec(command="/bin/evil; rm -rf /", args=())


def test_http_spec_blocks_metadata_endpoints() -> None:
    with pytest.raises(DesignerError):
        HttpSpec(url="https://169.254.169.254/latest/meta-data")


def test_http_spec_blocks_non_tls_remote() -> None:
    with pytest.raises(DesignerError):
        HttpSpec(url="http://internal.example:8080/mcp")


def test_http_spec_allows_loopback_http() -> None:
    HttpSpec(url="http://127.0.0.1:9900/mcp")


# ---------------------------------------------------------------------------
# O(1) dispatch gate (C3): digest/generation mismatch quarantines
# ---------------------------------------------------------------------------


def _tool(name: str) -> NormalizedTool:
    return NormalizedTool.build(name, f"{name} description", {"type": "object"})


def test_lease_gate_passes_when_state_matches() -> None:
    tools = [_tool("echo")]
    state = ConnectorRuntimeState(
        connector_id="c1",
        discovered_schema_digest=schema_digest(tools),
        generation=3,
        status="online",
    )
    lease = ConnectorLease(
        connector_id="c1", scope=ScopeKind.SHARED, tools=tools,
        state=state, expected_digest=schema_digest(tools), expected_generation=3,
    )
    assert lease.can_invoke("echo") is True
    assert lease.can_invoke("delete_file") is False  # not selected


def test_lease_gate_blocks_on_schema_change() -> None:
    tools = [_tool("echo")]
    changed = [_tool("echo"), _tool("search_repository")]
    state = ConnectorRuntimeState(
        connector_id="c1",
        discovered_schema_digest=schema_digest(changed),  # upstream changed overnight
        generation=3,
        status="online",
    )
    lease = ConnectorLease(
        connector_id="c1", scope=ScopeKind.SHARED, tools=tools,
        state=state, expected_digest=schema_digest(tools), expected_generation=3,
    )
    # Local O(1) comparison only: no network, no model call.
    assert lease.can_invoke("echo") is False


def test_lease_gate_blocks_on_generation_change() -> None:
    tools = [_tool("echo")]
    state = ConnectorRuntimeState(
        connector_id="c1",
        discovered_schema_digest=schema_digest(tools),
        generation=4,
        status="online",
    )
    lease = ConnectorLease(
        connector_id="c1", scope=ScopeKind.SHARED, tools=tools,
        state=state, expected_digest=schema_digest(tools), expected_generation=3,
    )
    assert lease.can_invoke("echo") is False


def test_lease_gate_blocks_when_quarantined() -> None:
    tools = [_tool("echo")]
    state = ConnectorRuntimeState(
        connector_id="c1",
        discovered_schema_digest=schema_digest(tools),
        generation=3,
        status="quarantined",
        quarantine_reason="schema differs from validated revision",
    )
    lease = ConnectorLease(
        connector_id="c1", scope=ScopeKind.SHARED, tools=tools,
        state=state, expected_digest=schema_digest(tools), expected_generation=3,
    )
    assert lease.can_invoke("echo") is False


def test_unselected_tool_cannot_execute() -> None:
    """Plan-doc verbatim regression: selection gating at the lease."""
    tools = [_tool("echo")]
    state = ConnectorRuntimeState(
        connector_id="c1",
        discovered_schema_digest=schema_digest(tools),
        generation=1,
        status="online",
    )
    lease = ConnectorLease(
        connector_id="c1", scope=ScopeKind.SHARED, tools=tools,
        state=state, expected_digest=schema_digest(tools), expected_generation=1,
    )
    assert [t.name for t in lease.tools] == ["echo"]
    assert lease.can_invoke("delete_file") is False


# ---------------------------------------------------------------------------
# Scope mapping (Fix 3): compile refuses user-scoped + shared runtime
# ---------------------------------------------------------------------------


def test_scope_mapping() -> None:
    from assistant.designer.adapters.mcp import ConnectorService

    service = ConnectorService()
    assert service.scope_for("shared") is ScopeKind.SHARED
    assert service.scope_for("user:souvik") is ScopeKind.USER_SCOPED
    assert service.scope_for("run") is ScopeKind.RUN_SCOPED


def test_user_scoped_connector_refused_in_shared_runtime() -> None:
    from assistant.designer.compiler import RuntimeScopeRequest, refuse_mismatched_scope

    request = RuntimeScopeRequest(
        runtime_scope=ScopeKind.SHARED,
        connectors={"github": ScopeKind.USER_SCOPED},
    )
    with pytest.raises(DesignerError) as excinfo:
        refuse_mismatched_scope(request)
    assert "github" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Validation lease (Clar 1): lists tools, refuses execution
# ---------------------------------------------------------------------------


async def test_validation_lease_refuses_tool_execution() -> None:
    lease = ValidationLease(connector_id="c1", tools=[_tool("echo")])
    with pytest.raises(RuntimeError):
        await lease.invoke_tool("echo", {"text": "hi"})
    await lease.aclose()


# ---------------------------------------------------------------------------
# Real stdio transport contract test (fixture server)
# ---------------------------------------------------------------------------


async def test_real_stdio_discovery_and_validation_lease() -> None:
    """Real transport: spawn the fixture server over stdio, discover its
    tools, close the lease in finally (Clar 1)."""
    from assistant.designer.adapters.mcp import ConnectorService

    spec = StdioSpec(
        command=sys.executable,
        args=(str(FIXTURE_SERVER),),
    )
    service = ConnectorService()
    lease = await service.validate_connector("fixture-stdio", spec)
    try:
        names = sorted(t.name for t in lease.tools)
        assert names == ["delete_file", "echo"]
        assert lease.schema_digest
        state = service._mcp.state("fixture-stdio")
        assert state.status == "online"
        assert state.generation == 1
        # Both tools are discovered; selection decides what is exposed.
        # The forbidden mutation exists but starts unselected.
    finally:
        await lease.aclose()
