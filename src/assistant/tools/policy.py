"""Application-side CUA policy (spec sections 13.6 and 17).

Defense in depth: even though Cua Driver enforces permissions natively
(bounded mode + capability manifest), our application filters discovered
MCP tools against its own allowlist, gates mutating actions with a
per-run budget, and never auto-enables newly introduced CUA tools.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from assistant.agent.context import RunBudget

logger = logging.getLogger("assistant.tools.policy")

#: Phase 1 application-side allowlist (spec section 13.6).
#:
#: Base names come from the spec; additional names are the cua-driver
#: v0.28.1 native registry names verified against the published macOS MCP
#: tool reference (2026-09-17). Deliberate additions only -- never
#: auto-enable a tool that appears after a driver update.
CUA_ALLOWED_TOOL_NAMES = frozenset(
    {
        # Spec section 13.6 expected names.
        "list_apps",
        "list_windows",
        "get_window_state",
        "screenshot",  # accepted if the installed driver exposes it
        "launch_app",
        "click",
        "type_text",
        "scroll",
        "press_key",
        # cua-driver v0.28.1 native observation tools.
        "get_desktop_state",
        "get_screen_size",
        "get_accessibility_tree",
        "verify_state",
        "bring_to_front",
        # cua-driver v0.28.1 native interaction tools.
        "double_click",
        "hotkey",
        "set_value",
    }
)

#: Tools that only observe state; at least one must be available at startup.
OBSERVATION_TOOL_NAMES = frozenset(
    {
        "list_apps",
        "list_windows",
        "get_window_state",
        "screenshot",
        "get_desktop_state",
        "get_accessibility_tree",
    }
)

#: Tools that change desktop state; they consume the per-run budget.
MUTATING_TOOL_NAMES = frozenset(
    {
        "launch_app",
        "click",
        "double_click",
        "type_text",
        "scroll",
        "press_key",
        "hotkey",
        "set_value",
        "bring_to_front",
    }
)

#: Per-run mutating-action budget. Set by the gateway before each agent run;
#: tool wrappers read it via this context variable.
cua_run_budget: contextvars.ContextVar[RunBudget | None] = contextvars.ContextVar(
    "cua_run_budget", default=None
)


class CuaUnavailableError(RuntimeError):
    """Raised when CUA is required but no observation tools are available."""


@dataclass(frozen=True)
class CuaToolFilterResult:
    """Outcome of filtering discovered CUA MCP tools."""

    enabled: list[BaseTool]
    discovered_names: list[str]
    enabled_names: list[str]
    skipped_names: list[str]


def filter_cua_tools(tools: Sequence[BaseTool]) -> CuaToolFilterResult:
    """Keep only tools in the application allowlist (spec section 13.6)."""
    discovered_names = [getattr(tool, "name", repr(tool)) for tool in tools]
    enabled = [tool for tool in tools if getattr(tool, "name", None) in CUA_ALLOWED_TOOL_NAMES]
    enabled_names = [getattr(tool, "name", "?") for tool in enabled]
    skipped = [name for name in discovered_names if name not in CUA_ALLOWED_TOOL_NAMES]
    return CuaToolFilterResult(
        enabled=enabled,
        discovered_names=discovered_names,
        enabled_names=enabled_names,
        skipped_names=skipped,
    )


def assert_observation_available(enabled_names: list[str]) -> None:
    """Fail when CUA is enabled but no observation tool is usable (spec 13.6)."""
    if not (set(enabled_names) & OBSERVATION_TOOL_NAMES):
        raise CuaUnavailableError(
            "CUA_ENABLED=true but none of the required observation tools "
            f"{sorted(OBSERVATION_TOOL_NAMES)} are available; discovered tools were "
            f"{sorted(set(enabled_names))}. Install/verify Cua Driver and its permissions."
        )


def wrap_mutating_tool(tool: BaseTool) -> BaseTool:
    """Gate a mutating CUA tool with the per-run budget and action logging.

    Observation tools pass through unchanged. When no per-run budget is
    installed (direct scripts), the action is logged but not counted; the
    gateway always installs a budget for agent runs (spec section 17).
    """
    if getattr(tool, "name", None) not in MUTATING_TOOL_NAMES:
        return tool

    original = getattr(tool, "coroutine", None)
    if original is None:
        return tool

    async def gated(**kwargs: Any) -> Any:
        budget = cua_run_budget.get()
        tool_name = getattr(tool, "name", "?")
        if budget is not None:
            budget.consume(tool_name)  # raises CuaBudgetExceeded at the ceiling
        logger.info(
            "cua_mutating_action",
            extra={"event": "cua_mutating_action", "tool": tool_name},
        )
        return await original(**kwargs)

    return StructuredTool(
        name=getattr(tool, "name", "cua_tool"),
        description=getattr(tool, "description", "") or "",
        args_schema=getattr(tool, "args_schema", None),
        coroutine=gated,
    )


def apply_tool_policy(tools: Sequence[BaseTool]) -> tuple[list[BaseTool], list[str]]:
    """Wrap mutating tools with the budget gate; return (wrapped, wrapped_names)."""
    wrapped = [wrap_mutating_tool(tool) for tool in tools]
    return wrapped, [getattr(tool, "name", "?") for tool in wrapped]


async def invoke_for_verification(
    tool: BaseTool,
    args: dict[str, Any],
    runner: Callable[..., Awaitable[Any]] | None = None,
) -> Any:
    """Invoke a tool for verification scripts (kept explicit, never on the hot path)."""
    if runner is not None:
        return await runner(tool, args)
    return await tool.ainvoke(args)
