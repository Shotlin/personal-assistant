"""Application-side CUA policy (spec sections 13.6 and 17).

Defense in depth: even though Cua Driver enforces permissions natively
(bounded mode + capability manifest), our application filters discovered
MCP tools against its own allowlist, gates mutating actions with a
per-run budget, and never auto-enables newly introduced CUA tools.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool

from assistant.agent.context import CuaBudgetExceeded, RunBudget
from assistant.runtime.session import DesktopRunCancelled

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
        "zoom",
        # cua-driver v0.28.1 native interaction tools.
        "double_click",
        "hotkey",
        "set_value",
    }
)

#: Session/cursor lifecycle controls. Controller-owned (master plan 7.2):
#: the DesktopSessionManager calls these through the trusted path; they
#: must never appear in the model-visible tool inventory.
SESSION_LIFECYCLE_TOOL_NAMES = frozenset(
    {
        "start_session",
        "end_session",
        "set_agent_cursor_enabled",
        "set_agent_cursor_motion",
        "set_agent_cursor_theme",
        "get_agent_cursor_state",
        "get_session",
        "list_sessions",
        "get_session_state",
        "escalate_session",
    }
)

assert CUA_ALLOWED_TOOL_NAMES.isdisjoint(SESSION_LIFECYCLE_TOOL_NAMES), (
    "session lifecycle tools must stay out of the model-visible inventory"
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

#: Per-run driver session id. Set by the gateway; observation/action wrappers
#: inject it into every call that accepts a ``session`` argument so all
#: actions of one run share the visible agent cursor.
cua_artifact_dir: contextvars.ContextVar[str] = contextvars.ContextVar(
    "cua_artifact_dir", default=""
)
cua_current_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cua_current_session", default=None
)

#: Per-run desktop session handle (WP3). Set by the gateway inside the
#: execution scope that owns the run; tool wrappers read it at call time
#: to lazily activate the driver session, force the trusted session id,
#: and check local cancellation before every new action.
if TYPE_CHECKING:  # pragma: no cover
    from assistant.runtime.session import DesktopRun as _DesktopRun

cua_desktop_run: contextvars.ContextVar[_DesktopRun | None] = contextvars.ContextVar(
    "cua_desktop_run", default=None
)

#: Per-run action ledger writer (WP4). Set inside cua_run_scope; mutating
#: dispatch records 'planned' before and a terminal state after the native
#: call. Observations never write rows.
if TYPE_CHECKING:  # pragma: no cover
    from assistant.runtime.runs import RunActionLedger as _RunActionLedger

cua_action_ledger: contextvars.ContextVar[_RunActionLedger | None] = (
    contextvars.ContextVar("cua_action_ledger", default=None)
)

#: Returned to the model instead of executing an action after a local
#: stop was requested (master plan 7.5: stop is local, not a model ask).
CANCELLED_ACTION_NOTICE = "[Run cancelled by user; no action taken.]"


@asynccontextmanager
async def cua_run_scope(
    *,
    budget: RunBudget | None,
    run: Any | None,
    artifact_dir: str = "",
    ledger: Any | None = None,
) -> AsyncIterator[None]:
    """Bind run-scoped policy state inside the scope that owns it.

    The gateway sets these per request (or per stream generator) and the
    tokens are reset on exit -- never set at import/wrap time, never left
    to leak across runs (master plan 7.2). ``ledger`` is the optional
    RunActionLedger for durable action accounting around real dispatch.
    """
    budget_token = cua_run_budget.set(budget)
    run_token = cua_desktop_run.set(run)
    dir_token = cua_artifact_dir.set(artifact_dir)
    ledger_token = cua_action_ledger.set(ledger)
    try:
        yield
    finally:
        cua_action_ledger.reset(ledger_token)
        cua_artifact_dir.reset(dir_token)
        cua_desktop_run.reset(run_token)
        cua_run_budget.reset(budget_token)

#: Text-first observation defaults (latency + token control): skip the
#: base64 screenshot and cap the accessibility tree; the model may opt
#: into screenshots explicitly for visual verification.
OBSERVATION_DEFAULTS: dict[str, dict[str, Any]] = {
    "get_window_state": {
        "include_screenshot": False,
        "max_elements": 120,
        "max_depth": 12,
    },
    "get_accessibility_tree": {"max_elements": 120},
}

#: Tools that can emit screenshots. When the model explicitly asks for a
#: screenshot, the PNG is diverted to the artifact store (never base64 in
#: the prompt) and the file path is reported back.
SCREENSHOT_CAPABLE_TOOLS = frozenset({"get_window_state", "get_desktop_state", "zoom"})


def _artifact_screenshot_path(artifact_dir: str, tool_name: str) -> str:
    from datetime import datetime
    from pathlib import Path

    base = Path(artifact_dir)
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")[:-3]
    return str(base / f"{tool_name}-{stamp}.png")


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


#: v0.28.2 addressing contract appended to tool descriptions so the model
#: addresses targets correctly on the first attempt (spec section 13.2).
ADDRESSING_CONTRACT = (
    " Driver contract (cua-driver v0.28.2): address a target element with "
    "`element_token` from the latest get_window_state `elements` output, or "
    "with `snapshot_id` plus `element_index`; a bare element_index is rejected. "
    "After important UI actions, re-run get_window_state before the next action "
    "and verify the observed state."
)

_DESCRIPTION_ENRICHED_TOOLS = frozenset(
    {"click", "double_click", "type_text", "press_key", "scroll", "set_value", "hotkey"}
)


def _enriched_description(tool: BaseTool) -> str:
    base = getattr(tool, "description", "") or ""
    if getattr(tool, "name", None) in _DESCRIPTION_ENRICHED_TOOLS:
        return f"{base}{ADDRESSING_CONTRACT}"
    return base


def wrap_tool_errors(tool: BaseTool) -> BaseTool:
    """Convert driver-side failures of any CUA tool into agent-visible text.

    The MCP adapter raises on ``isError`` results (including validation
    errors like wrong argument shapes). The agent must see the error text
    to recover per spec rule 15, so every enabled CUA tool gets this
    conversion; mutating tools additionally carry the budget gate.

    Also applies, in one place:
    - session injection: every call that accepts ``session`` joins the
      run's driver session (visible cursor continuity),
    - observation defaults for ``get_window_state``: text-first payloads
      (no base64 screenshot, bounded element tree) so each step stays
      fast and cheap. The model can still opt into screenshots
      explicitly.
    """
    name = getattr(tool, "name", "cua_tool")
    original = getattr(tool, "coroutine", None)
    if original is None:
        return tool

    is_mutating = name in MUTATING_TOOL_NAMES
    defaults = OBSERVATION_DEFAULTS.get(name, {})
    accepts_session = isinstance(getattr(tool, "args", None), dict) and "session" in tool.args
    captures_screenshot = name in SCREENSHOT_CAPABLE_TOOLS

    async def safe(**kwargs: Any) -> Any:
        run = cua_desktop_run.get()
        try:
            async with run.action() if run is not None else nullcontext():
                if run is not None:
                    run.require_active()
                return await dispatch(kwargs)
        except DesktopRunCancelled:
            return CANCELLED_ACTION_NOTICE

    async def dispatch(kwargs: dict[str, Any]) -> Any:
        # Read scope at call time, and force the controller's session even
        # when the model supplied a different nonempty value.
        artifact_dir = cua_artifact_dir.get()
        run = cua_desktop_run.get()
        if accepts_session:
            session = run.session_id if run is not None else cua_current_session.get()
            if session:
                kwargs["session"] = session
        for key, value in defaults.items():
            # Schema defaults arrive as None (LangChain fills every schema
            # field), so None means "unset" here -- setdefault would keep
            # the None and silently drop the text-first baseline.
            if kwargs.get(key) is None:
                kwargs[key] = value
        if (
            captures_screenshot
            and artifact_dir
            and kwargs.get("include_screenshot", True)
            and not kwargs.get("screenshot_out_file")
        ):
            kwargs["screenshot_out_file"] = _artifact_screenshot_path(artifact_dir, name)
        if is_mutating:
            budget = cua_run_budget.get()
            if budget is not None:
                budget.consume(name)  # raises CuaBudgetExceeded at the ceiling
            logger.info(
                "cua_mutating_action",
                extra={"event": "cua_mutating_action", "tool": name},
            )
        ledger = cua_action_ledger.get()
        ledger_id: int | None = None
        if is_mutating and ledger is not None:
            ledger_id = await _ledger_plan(ledger, name, kwargs)
        try:
            result = await original(**kwargs)
        except CuaBudgetExceeded:
            if ledger_id is not None:
                await _ledger_observe(ledger, ledger_id, "failed", "budget_exceeded")
            raise
        except asyncio.CancelledError:
            # Outcome was never observed and must never be replayed blind.
            if ledger_id is not None:
                await _ledger_observe(ledger, ledger_id, "unknown")
            raise
        except TimeoutError as exc:
            # Dispatched but no acknowledgement: unknown_effect, readback
            # required (master plan 11.2); agent sees the error text.
            if ledger_id is not None:
                await _ledger_observe(ledger, ledger_id, "unknown", str(exc)[:120])
            return f"Error: outcome unobserved (timeout): {exc}"
        except Exception as exc:  # noqa: BLE001 -- tool errors become agent-visible text
            if ledger_id is not None:
                await _ledger_observe(ledger, ledger_id, "failed", str(exc)[:120])
            return f"Error: {exc}"
        if ledger_id is not None:
            await _ledger_observe(ledger, ledger_id, "confirmed")
        return _model_text(result)

    async def _ledger_plan(ledger: Any, tool_name: str, kwargs: dict[str, Any]) -> int | None:
        """Write the 'planned' row before dispatch; never break the action."""
        import hashlib

        digest = hashlib.sha256(repr(sorted(kwargs.items())).encode()).hexdigest()[:16]
        try:
            return await ledger.plan(tool_name=tool_name, args_digest=digest)
        except Exception:
            logger.exception("action_ledger_plan_failed", extra={"event": "ledger_plan_failed"})
            return None

    async def _ledger_observe(
        ledger: Any, ledger_id: int, outcome: str, evidence: str = ""
    ) -> None:
        try:
            await ledger.observe(ledger_id, outcome, evidence)
        except Exception:
            logger.exception(
                "action_ledger_observe_failed", extra={"event": "ledger_observe_failed"}
            )

    def _model_text(result: Any) -> Any:
        """Render normalized ToolOutcomes as bounded model-facing text.

        Never a dataclass repr, never base64: image payloads are reported
        by count only; structured evidence stays in the outcome for the
        trusted executor (master plan WP2).
        """
        from assistant.tools.result_normalizer import ToolOutcome

        if not isinstance(result, ToolOutcome):
            return result
        blocks = result.model_content(allow_images=False)
        text = "\n".join(str(block.get("text", "")) for block in blocks)
        if result.images:
            text += f"\n[{len(result.images)} screenshot(s) retained locally]"
        if result.truncated:
            text += "\n[observation truncated]"
        return text or "(no content)"

    return StructuredTool(
        name=name,
        description=_enriched_description(tool),
        args_schema=getattr(tool, "args_schema", None),
        coroutine=safe,
    )


def apply_tool_policy(tools: Sequence[BaseTool]) -> tuple[list[BaseTool], list[str]]:
    """Apply budget gates (mutating) and error conversion (all) to CUA tools."""
    wrapped = [wrap_tool_errors(tool) for tool in tools]
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
