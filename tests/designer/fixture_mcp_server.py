"""Fixture MCP server: one harmless echo tool + one forbidden mutation.

Spawned by connector tests over real stdio transport. The forbidden tool
exists to prove that selection gating (not driver capability) is what
prevents its dispatch.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("designer-fixture")


@mcp.tool()
def echo(text: str) -> str:
    """Return the input text unchanged (harmless)."""
    return text


@mcp.tool()
def delete_file(path: str) -> str:
    """Pretend to delete a file (forbidden mutation; never selected)."""
    return f"would delete {path}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
