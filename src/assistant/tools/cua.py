"""Cua Driver MCP connection (spec sections 13.2-13.7).

Connects over stdio via ``langchain-mcp-adapters``, discovers the actual
tool inventory from the installed driver, filters it through the
application allowlist, and fails startup when observation tools are
missing while CUA is enabled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from assistant.settings import Settings
from assistant.tools.policy import (
    apply_tool_policy,
    assert_observation_available,
    filter_cua_tools,
)

logger = logging.getLogger("assistant.tools.cua")


@dataclass(frozen=True)
class CuaConnection:
    """Filtered CUA tool inventory plus what the driver actually exposed."""

    tools: list[BaseTool]
    tool_names: list[str]
    discovered_names: list[str]
    skipped_names: list[str]


def _require_manifest(settings: Settings) -> Path:
    if not settings.cua_enabled:
        raise RuntimeError("CUA is disabled; load_cua_tools requires CUA_ENABLED=true")
    manifest = Path(settings.cua_capability_manifest_path)
    if not settings.cua_capability_manifest_path.startswith("/"):
        raise RuntimeError("CUA_CAPABILITY_MANIFEST_PATH must be absolute")
    if not manifest.is_file():
        raise RuntimeError(
            f"CUA capability manifest not found at {manifest}; author it against the "
            "installed Cua Driver schema and start the driver in bounded mode."
        )
    return manifest


async def load_cua_tools(settings: Settings) -> CuaConnection:
    """Connect to the Cua Driver MCP server and return the filtered tools."""
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
    )
