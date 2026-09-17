"""Assemble the final tool inventory passed to the Deep Agent (spec 11.5).

The inventory is: deepagents built-ins (managed inside create_deep_agent
via the composite backend) + filtered CUA tools. Nothing else. The
inventory is logged by name only -- never arguments or secrets.
"""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool

logger = logging.getLogger("assistant.tools.registry")


def assemble_tool_inventory(cua_tools: list[BaseTool] | None) -> list[BaseTool]:
    """Return the extra (non-builtin) tools for the agent: filtered CUA tools."""
    tools = list(cua_tools or [])
    logger.info(
        "agent_tool_inventory",
        extra={
            "event": "agent_tool_inventory",
            "extra_tool_names": sorted(getattr(t, "name", "?") for t in tools),
        },
    )
    return tools
