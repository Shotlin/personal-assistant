"""Cua Driver MCP connection (spec sections 13.2-13.7).

Two ways to talk to the driver:

- :func:`open_cua_connection` -- persistent stdio session owned by the
  gateway. The transport lease stays open for the process lifetime, so
  driver-side *sessions* (agent cursor, per-run state) survive across
  tool calls. Used by the application lifespan.
- :func:`load_cua_tools` -- stateless discovery via the MCP adapter
  (spawns a short-lived proxy per call). Fine for verification scripts;
  sessions do NOT persist through it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from assistant.settings import Settings
from assistant.tools.policy import (
    apply_tool_policy,
    assert_observation_available,
    filter_cua_tools,
)

logger = logging.getLogger("assistant.tools.cua")

if TYPE_CHECKING:  # pragma: no cover
    from mcp import ClientSession


@dataclass(frozen=True)
class CuaConnection:
    """Filtered CUA tool inventory plus what the driver actually exposed."""

    tools: list[BaseTool]
    tool_names: list[str]
    discovered_names: list[str]
    skipped_names: list[str]
    tools_by_name: dict[str, BaseTool] = field(default_factory=dict)


def _require_manifest(settings: Settings) -> Path:
    if not settings.cua_enabled:
        raise RuntimeError("CUA is disabled; CUA tools require CUA_ENABLED=true")
    manifest = Path(settings.cua_capability_manifest_path)
    if not settings.cua_capability_manifest_path.startswith("/"):
        raise RuntimeError("CUA_CAPABILITY_MANIFEST_PATH must be absolute")
    if not manifest.is_file():
        raise RuntimeError(
            f"CUA capability manifest not found at {manifest}; author it against the "
            "installed Cua Driver schema and start the driver in bounded mode."
        )
    return manifest


def _filtered_connection(discovered: list[BaseTool]) -> CuaConnection:
    result = filter_cua_tools(discovered)
    logger.info(
        "cua_tools_filtered",
        extra={
            "event": "cua_tools_filtered",
            "discovered": sorted(result.discovered_names),
            "enabled": sorted(result.enabled_names),
            "skipped": sorted(result.skipped_names),
        },
    )
    assert_observation_available(result.enabled_names)
    wrapped, wrapped_names = apply_tool_policy(result.enabled)
    return CuaConnection(
        tools=wrapped,
        tool_names=wrapped_names,
        discovered_names=result.discovered_names,
        skipped_names=result.skipped_names,
        tools_by_name={getattr(t, "name", "?"): t for t in wrapped},
    )


async def load_cua_tools(settings: Settings) -> CuaConnection:
    """Stateless discovery through the MCP adapter (verification scripts)."""
    _require_manifest(settings)
    client = MultiServerMCPClient(
        {
            "cua": {
                "command": settings.cua_command,
                "args": ["mcp"],
                "transport": "stdio",
            }
        }
    )
    discovered = await client.get_tools(server_name="cua")
    return _filtered_connection(list(discovered))


@asynccontextmanager
async def open_cua_connection(settings: Settings) -> AsyncIterator[CuaConnection]:
    """Persistent stdio MCP session owned by the gateway.

    The transport lease stays open, which keeps driver-side sessions
    (agent cursor, run state) alive across tool calls. Yields the filtered
    tool inventory and closes the session on exit.
    """
    _require_manifest(settings)

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(command=settings.cua_command, args=["mcp"])

    async with stdio_client(server_params) as (read_stream, write_stream):
        session = ClientSession(read_stream, write_stream)
        async with session as mcp_session:
            await mcp_session.initialize()

            tools_response = await mcp_session.list_tools()
            discovered: list[BaseTool] = []
            for tool in tools_response.tools:
                discovered.append(
                    StructuredTool(
                        name=tool.name,
                        description=tool.description or "",
                        args_schema=tool.inputSchema,
                        coroutine=_caller(mcp_session, tool.name),
                    )
                )
            yield _filtered_connection(discovered)


def _caller(session: ClientSession, name: str) -> Any:
    """Build an async callable that routes one tool call over ``session``.

    Results pass through :func:`normalize_mcp_result`: the model receives
    bounded text (never base64 or raw MCP objects) while structured
    evidence stays available in the outcome for local verification.
    """

    from assistant.tools.result_normalizer import normalize_mcp_result

    async def call(**kwargs: Any) -> Any:
        result = await session.call_tool(name, kwargs)
        outcome = normalize_mcp_result(result)
        blocks = outcome.model_content(allow_images=False)
        text = "\n".join(str(block.get("text", "")) for block in blocks)
        if outcome.images:
            text += f"\n[{len(outcome.images)} screenshot(s) retained locally]"
        if outcome.truncated:
            text += "\n[observation truncated]"
        return text or "(no content)"

    return call
