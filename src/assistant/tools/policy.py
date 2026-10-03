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
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool

from assistant.agent.context import CuaBudgetExceeded, RunBudget
from assistant.runtime.cua_faults import (
    RECOVERY_FOR,
    CuaFault,
    FaultRecovery,
    classify_exception,
    classify_text,
)
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

class ActionClass:
    """How much trust one desktop call deserves. Deterministic, never a guess.

    Architecture D1 moved the ceiling out of the driver's manifest and into this
    module, so the distinction has to be made here: ordinary desktop work runs
    without asking, and only the classes that can do lasting or external damage
    stop for the user.
    """

    READ = "read"
    ACT = "act"
    SENSITIVE = "sensitive"


#: Tools whose effect is not a desktop interaction but a change to a process or
#: to something outside this machine.
SENSITIVE_TOOL_NAMES = frozenset(
    {
        "kill_app",
        "terminate_app",
        "browser_download",
        "execute_javascript",
        "browser_prepare",
    }
)

#: Applications whose contents are credentials, system authority, or a shell that
#: will run whatever is typed into them. Acting *inside* these is not a benign
#: desktop task, whatever the intended keystrokes are.
SENSITIVE_TARGET_BUNDLES = frozenset(
    {
        "com.apple.systempreferences",
        "com.apple.controlcenter",
        "com.apple.keychainaccess",
        "com.apple.Terminal",
    }
)

_SENSITIVE_TARGET_MARKERS = ("1password", "bitwarden", "keychain", "keeppass", "kaspersky")

#: The same authority under its display name, and the other shells on a Mac.
_SENSITIVE_TARGET_ALIASES = ("system settings", "terminal", "iterm")

#: Actions that address one specific surface, and therefore need a resolved
#: pid + window and a snapshot taken from it.
AIMED_ACTION_TOOLS = frozenset(
    {
        "click",
        "double_click",
        "type_text",
        "scroll",
        "press_key",
        "hotkey",
        "set_value",
    }
)

#: Raising or foregrounding a window *cascades* it, so every element frame read
#: before the call is stale afterwards. Measured live on 2026-09-24.
INVALIDATES_SNAPSHOTS_TOOLS = frozenset({"bring_to_front"})

#: Below this, a surface has no pixel that an action can land on. Measured in
#: Phase 4: the menu bar reads 33 points tall and every aim against it fails.
AIMABLE_MIN_POINTS = 80.0

#: How many windows one observation describes to the model. A loop that cannot
#: see the id cannot use it, so this is the aimable shortlist, not a dump.
MAX_LISTED_WINDOWS = 12


def _points(bounds: dict[str, Any], key: str) -> float:
    value = bounds.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _window_enumeration(structured: dict[str, Any]) -> str:
    """The window list, in the form the loop has to act on.

    The driver answers ``list_windows`` with a count and a structured payload, and
    Sani forwards text to the model. So the model was told "Found 5 window(s)."
    and then invented ids -- ``window_id 1``, then 2, 3, 4, 5 -- burning the run.
    Caught live in the Phase 7 battery on 2026-09-25.
    """
    windows = structured.get("windows")
    if not isinstance(windows, list) or not windows:
        return ""
    lines: list[str] = []
    for window in windows[:MAX_LISTED_WINDOWS]:
        if not isinstance(window, dict):
            continue
        window_id = window.get("window_id")
        if not isinstance(window_id, int):
            continue
        bounds = window.get("bounds") if isinstance(window.get("bounds"), dict) else {}
        title = " ".join(str(window.get("title") or "").split())[:60]
        note = ""
        if not window.get("is_on_screen"):
            note = ", off screen"
        elif (
            _points(bounds, "width") < AIMABLE_MIN_POINTS
            or _points(bounds, "height") < AIMABLE_MIN_POINTS
        ):
            note = ", too small to aim"
        lines.append(
            f'- window_id {window_id} "{title}" '
            f"{_points(bounds, 'width'):.0f}x{_points(bounds, 'height'):.0f}"
            f"+{_points(bounds, 'x'):.0f}+{_points(bounds, 'y'):.0f}{note}"
        )
    if len(windows) > MAX_LISTED_WINDOWS:
        lines.append(f"- ... {len(windows) - MAX_LISTED_WINDOWS} more")
    return "\n".join(lines)


def _identity_shape(value: str) -> str:
    """One comparable form for ``com.apple.Terminal``, ``Terminal``, ``terminal.app``.

    The loop names an app however the user named it, and the identity remembered
    for a pid is whichever of the two a payload happened to carry. Matching whole
    reverse-DNS ids meant the ordinary shape of a model's own call --
    ``launch_app(name="Terminal")`` -- walked straight past the gate; proven live
    in the Phase 7 acceptance battery on 2026-09-25.
    """
    text = value.strip().lower()
    if text.endswith(".app"):
        text = text[: -len(".app")]
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.replace(" ", "").replace("-", "").replace("_", "")


#: Everything the gate refuses to touch without asking, in comparable form.
_SENSITIVE_TARGET_NAMES = frozenset(
    {_identity_shape(name) for name in SENSITIVE_TARGET_BUNDLES}
    | {"systemsettings", "terminal", "iterm"}
)


def _is_sensitive_identifier(value: str) -> bool:
    shape = _identity_shape(value)
    return shape in _SENSITIVE_TARGET_NAMES or any(
        marker in shape for marker in _SENSITIVE_TARGET_MARKERS
    )


def _target_identifier(
    kwargs: dict[str, Any], apps: dict[int, str] | None
) -> str | None:
    """Which application this call will actually affect.

    ``click`` and ``type_text`` carry a pid, never a bundle id, so the identity of
    the target has to come from what has already been observed this run. Typing
    into an already-open Terminal is exactly as dangerous as launching one, and a
    gate that looked only at the arguments would miss it.
    """
    direct = kwargs.get("bundle_id") or kwargs.get("name") or kwargs.get("target_app")
    if isinstance(direct, str) and direct:
        return direct
    pid = kwargs.get("pid")
    if isinstance(pid, int) and apps:
        return apps.get(pid)
    return None


def classify_cua_call(
    name: str, kwargs: dict[str, Any], apps: dict[int, str] | None = None
) -> str:
    """One of :class:`ActionClass`, from the call and the target it resolves to."""
    if name in SENSITIVE_TOOL_NAMES:
        return ActionClass.SENSITIVE
    target = _target_identifier(kwargs, apps)
    sensitive = bool(target) and _is_sensitive_identifier(str(target))
    if name in OBSERVATION_TOOL_NAMES:
        # Reading a credential surface is not acting on it.
        return ActionClass.READ
    if name == "launch_app":
        return ActionClass.SENSITIVE if sensitive else ActionClass.ACT
    if name in MUTATING_TOOL_NAMES or name in AIMED_ACTION_TOOLS:
        return ActionClass.SENSITIVE if sensitive else ActionClass.ACT
    return ActionClass.ACT


class DesktopContextStore:
    """What Sani was working on, kept between turns of the same conversation.

    ``Open Chrome`` -> ``Open YouTube`` -> ``play a hip-hop song`` -> ``scroll
    down three times`` is one task spoken in four messages, and a per-run state
    forgets the application between them. Only identity carries over: the pid to
    bundle mapping and the surface in focus. Snapshots never do -- the screen has
    moved on, and the model must look again before it acts.
    """

    def __init__(self, *, ttl_seconds: float = 600.0, max_conversations: int = 8) -> None:
        self._ttl = ttl_seconds
        self._cap = max_conversations
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, conversation: str, *, now: float | None = None) -> dict[str, Any] | None:
        """The carried context, or ``None`` when there is nothing worth carrying."""
        import time

        if not conversation:
            return None
        entry = self._entries.get(conversation)
        if entry is None:
            return None
        seen_at, state = entry
        if (now if now is not None else time.monotonic()) - seen_at > self._ttl:
            del self._entries[conversation]
            return None
        return {
            "focus": state.get("focus"),
            "apps": dict(state.get("apps") or {}),
        }

    def save(self, conversation: str, state: dict[str, Any], *, now: float | None = None) -> None:
        """Persist only what is safe to reuse; the oldest conversation goes first."""
        import time

        if not conversation:
            return
        focus = state.get("focus")
        apps = {
            pid: identifier
            for pid, identifier in (state.get("apps") or {}).items()
            if isinstance(identifier, str)
        }
        if focus is None and not apps:
            self._entries.pop(conversation, None)
            return
        self._entries[conversation] = (
            now if now is not None else time.monotonic(),
            {"focus": focus, "apps": apps},
        )
        while len(self._entries) > self._cap:
            self._entries.pop(next(iter(self._entries)))

    def clear(self) -> None:
        self._entries.clear()


#: One instance per sidecar process; the sidecar is single-user and single-owner.
desktop_contexts = DesktopContextStore()

#: What the model is told to do next, per fault. This is the recovery ladder: a
#: refusal is the last resort, not the first response to uncertainty.
RECOVERY_GUIDANCE: dict[str, str] = {
    FaultRecovery.RECONNECT_TRANSPORT.value: (
        "Sani reconnects the driver itself; observe again before doing anything else."
    ),
    FaultRecovery.RECOVER_DAEMON.value: (
        "Sani restarts its own driver; observe again, and only report a blocker if it "
        "still cannot be reached."
    ),
    FaultRecovery.REAPPROVE_POLICY.value: (
        "The driver policy needs re-approving by Sani; this is not a macOS permission "
        "problem, so do not ask the user to change permissions."
    ),
    FaultRecovery.GRANT_PERMISSION.value: (
        "macOS has not authorized Sani. Tell the user which switch to enable; do not "
        "retry the action."
    ),
    FaultRecovery.REVIVE_SESSION.value: (
        "The driver session ended; Sani revives it. Re-observe the screen."
    ),
    FaultRecovery.RETARGET_APP.value: (
        "The application is not running or not installed: launch it, or pick the app "
        "the user actually means. Do not ask for a pid."
    ),
    FaultRecovery.REENUMERATE_WINDOWS.value: (
        "That window is gone or was never visible: call list_windows again and aim at "
        "the window that is on screen now."
    ),
    FaultRecovery.REOBSERVE.value: (
        "This target refused that action. Observe again and take a different route "
        "(another element, the menu bar, a hotkey, or a coordinate click on the pixel "
        "rung) instead of repeating it."
    ),
    FaultRecovery.VERIFY_VISUALLY.value: (
        "The driver acted but the accessibility tree cannot prove the effect. Verify by "
        "observation; if the tree still cannot show it, take one screenshot "
        "(include_screenshot: true) rather than repeating the action."
    ),
    FaultRecovery.ASK_USER.value: "Ask the user the one question that unblocks this.",
    FaultRecovery.ABORT.value: "Stop and report what could not be done, and why.",
}


def _redacted(text: str) -> str:
    """Secret-screen model-facing error text before it can reach a sink."""
    from assistant.observability.logging import redact

    return redact(text)


def fault_message(fault: CuaFault, detail: str) -> str:
    """The same shape as :func:`fault_guidance`, for a fault known by rule.

    A missing pid is a targeting failure because this module decided not to send
    an unaimed action -- the driver was never asked -- so the class must be stated
    rather than inferred from the wording.
    """
    from assistant.runtime.cua_faults import FAULT_MESSAGES

    recovery = RECOVERY_FOR[fault]
    return _redacted(
        f"Error: [{fault.value}] {FAULT_MESSAGES[fault]} {detail} "
        f"-> {RECOVERY_GUIDANCE[recovery.value]}"
    )


def fault_guidance(text: str, exc: BaseException | None = None) -> str:
    """`Error: [fault] what happened -> what to do next`, from real evidence."""
    from assistant.runtime.cua_faults import FAULT_MESSAGES

    fault = classify_exception(exc) if exc is not None else classify_text(text)
    if fault is CuaFault.UNKNOWN:
        return _redacted(f"Error: {text}")
    recovery = RECOVERY_FOR[fault]
    return (
        f"Error: [{fault.value}] {FAULT_MESSAGES[fault]} "
        f"-> {RECOVERY_GUIDANCE[recovery.value]}"
    )


#: Per-run targeting memory: which surface was snapshotted, and when it went
#: stale. Set by the run scope; read and written by the tool wrappers.
cua_target_state: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "cua_target_state", default=None
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

cua_action_ledger: contextvars.ContextVar[_RunActionLedger | None] = contextvars.ContextVar(
    "cua_action_ledger", default=None
)

#: Mission-mode dispatch guard (Jarvis Phase 1, T03). Set only by the bounded
#: executor scope: when present, every mutating call is checked against the
#: mission's authority (permit, scope, budget) before dispatch; a returned
#: string is the refusal the agent sees. Legacy runs leave this unset and
#: behave exactly as before.
MissionDispatchGuard = Callable[[str, dict[str, Any]], Awaitable[str | None]]
mission_dispatch_guard: contextvars.ContextVar[MissionDispatchGuard | None] = (
    contextvars.ContextVar("mission_dispatch_guard", default=None)
)

# Synchronous final admission, after all awaited authorization/audit work.
mission_final_dispatch_check: contextvars.ContextVar[Callable[[], str | None] | None] = (
    contextvars.ContextVar("mission_final_dispatch_check", default=None)
)

#: Mission mode makes the action ledger authoritative: a failed intent write
#: BLOCKS the mutation (fail closed, STORAGE_UNAVAILABLE) instead of the
#: legacy fail-open behavior, which remains for non-mission runs.
mission_audit_strict: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "mission_audit_strict", default=False
)

#: Mission mode withholds screenshots by default: captures are not diverted
#: to artifact files (raw pixels never become retained evidence) and never
#: pass inline to the model. Existing non-mission behavior is unchanged.
mission_screenshots_withheld: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "mission_screenshots_withheld", default=False
)

#: Controller role enforcement (R02/F02): set invocation-locally by the
#: mission controller around Deep graph calls. The REAL wrapped-tool
#: dispatch checks it: PLAN/RECOVER/REVIEW/INFO roles may never dispatch a
#: desktop tool, whatever tool reference the model obtained. Empty means no
#: mission role is active (legacy chat keeps desktop tools).
controller_role_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "controller_role", default=""
)

#: Roles that may not dispatch desktop tools through the wrapped inventory.
DESKTOP_FORBIDDEN_ROLES = frozenset({"PLAN", "RECOVER", "REVIEW", "INFO"})

#: The normalized payloads behind recent wrapped calls, keyed by a monotonic
#: call index. The wrapper runs in its own task, so a ContextVar rebinding here
#: would never reach the caller -- a module-level log mutated in place is
#: visible across that task boundary. A caller captures
#: :meth:`StructuredCallLog.next_index` before its awaited call and reads
#: :meth:`StructuredCallLog.after` immediately afterwards: the entry it gets
#: back is provably the one its own dispatch produced, and an interleaved call
#: to the *same tool name* can no longer hand it a stranger's payload (the
#: failure mode of the previous name-keyed ``LAST_STRUCTURED`` dictionary).
@dataclass(frozen=True)
class StructuredCall:
    """One recorded wrapped call: its index, tool name and typed payload."""

    index: int
    tool: str
    payload: dict[str, Any]


class StructuredCallLog:
    """Bounded, indexed record of recent wrapped calls' structured payloads."""

    def __init__(self, capacity: int = 64) -> None:
        self._capacity = capacity
        self._next = 0
        self._entries: dict[int, StructuredCall] = {}

    @property
    def next_index(self) -> int:
        """The index the *next* recorded call will carry."""
        return self._next

    def record(self, tool: str, payload: dict[str, Any]) -> None:
        index = self._next
        self._next += 1
        self._entries[index] = StructuredCall(index=index, tool=tool, payload=payload)
        while len(self._entries) > self._capacity:
            self._entries.pop(min(self._entries))

    def after(self, index_before: int) -> StructuredCall | None:
        """The first call recorded after ``index_before``, or None.

        Sequential callers read this immediately after their own awaited call,
        so the entry is theirs; a caller that waited too long and finds a
        different tool's entry must treat the read as stale.
        """
        for index in sorted(self._entries):
            if index >= index_before:
                return self._entries[index]
        return None


STRUCTURED_CALLS = StructuredCallLog()


def _structured_of(result: Any) -> dict[str, Any] | None:
    """The normalized payload of a dispatched call, for the fast path's reads."""
    structured = getattr(result, "structured", None)
    return structured if isinstance(structured, dict) else None

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
    conversation: str = "",
    mission_guard: MissionDispatchGuard | None = None,
    mission_strict_audit: bool = False,
    mission_withhold_screenshots: bool = False,
) -> AsyncIterator[None]:
    """Bind run-scoped policy state inside the scope that owns it.

    The gateway sets these per request (or per stream generator) and the
    tokens are reset on exit -- never set at import/wrap time, never left
    to leak across runs (master plan 7.2). ``ledger`` is the optional
    RunActionLedger for durable action accounting around real dispatch.

    The mission-scoped guards (Jarvis Phase 1) follow the same lifecycle:
    they default off, and the bounded executor passes them per dispatch.
    """
    budget_token = cua_run_budget.set(budget)
    run_token = cua_desktop_run.set(run)
    dir_token = cua_artifact_dir.set(artifact_dir)
    ledger_token = cua_action_ledger.set(ledger)
    guard_token = mission_dispatch_guard.set(mission_guard)
    strict_token = mission_audit_strict.set(mission_strict_audit)
    withhold_token = mission_screenshots_withheld.set(mission_withhold_screenshots)
    carried = desktop_contexts.get(conversation) or {}
    state: dict[str, Any] = {
        "snapshots": {},
        "focus": carried.get("focus"),
        "apps": dict(carried.get("apps") or {}),
        "conversation": conversation,
        #: The surface carried in from an earlier turn, surfaced to the model once
        #: so it knows the continuity is deliberate rather than accidental.
        "carried_focus": carried.get("focus"),
        "note_shown": False,
    }
    target_token = cua_target_state.set(state)
    try:
        yield
    finally:
        desktop_contexts.save(conversation, state)
        mission_screenshots_withheld.reset(withhold_token)
        mission_audit_strict.reset(strict_token)
        mission_dispatch_guard.reset(guard_token)
        cua_action_ledger.reset(ledger_token)
        cua_target_state.reset(target_token)
        cua_artifact_dir.reset(dir_token)
        cua_desktop_run.reset(run_token)
        cua_run_budget.reset(budget_token)


#: Text-first observation defaults (latency + token control): skip the
#: base64 screenshot and cap the accessibility tree; the model may opt
#: into screenshots explicitly for visual verification.
OBSERVATION_DEFAULTS: dict[str, dict[str, Any]] = {
    "get_window_state": {
        "include_screenshot": False,
        # The model sees a compact, visible-only list (scene.compact_observation),
        # so the walk can be deep: a 120-element cap returned browser chrome and
        # cut the page itself off ("the tree truncates" -- live 2026-10-01).
        "max_elements": 1500,
        "max_depth": 30,
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
    " Address targets with `element_token` from the latest get_window_state "
    "output (or snapshot_id+element_index); bare element_index is rejected. "
    "Re-observe after important actions and verify the result."
)

_DESCRIPTION_ENRICHED_TOOLS = frozenset(
    {"click", "double_click", "type_text", "press_key", "scroll", "set_value", "hotkey"}
)

#: Token-budget compaction (Phase 1.1): the driver ships verbose per-tool
#: prose (~19.8k chars total ≈ 5-6k tokens of every agent turn). Known
#: tools get a one-line description here; the addressing contract stays
#: on action tools (correctness-critical). Unknown tools keep the
#: driver's own description (fail-open for new driver versions).
COMPACT_TOOL_DESCRIPTIONS: dict[str, str] = {
    "list_apps": "List running apps: pid, bundle_id, name, window ids.",
    "list_windows": "List an app's windows (pid): window_id, title, bounds.",
    "get_window_state": (
        "Read one window's UI: elements with element_token, role, label, "
        "value. THE source of element_token for actions."
    ),
    "verify_state": "Assert an expected UI condition; returns ok or the mismatch.",
    "launch_app": "Launch an app by bundle_id; returns pid + window ids.",
    "bring_to_front": "Bring a window to the foreground (pid/window_id).",
    "click": "Click an element_token (or point) once.",
    "double_click": "Double-click an element_token (or point).",
    "type_text": "Type text into the focused or targeted element.",
    "press_key": "Press a key, with optional modifiers.",
    "hotkey": "Press a key combo given as a combo string.",
    "set_value": "Set an element's value directly (fastest way to fill fields).",
    "scroll": "Scroll at a target by amount and direction.",
    "get_screen_size": "Get screen pixel size and scale factor.",
    "get_desktop_state": "Full-screen PNG (to file) + true size for desktop-coordinate actions.",
    "get_accessibility_tree": "Bounded accessibility tree across apps.",
    "zoom": "Capture an enlarged view of a screen region.",
}


def _targeting_error(name: str, kwargs: dict[str, Any]) -> str | None:
    """Refuse an unaimed action here, not one layer down as a driver complaint.

    ``Missing required integer field: pid`` reaching the user meant an action was
    dispatched at no particular window. The driver's element cache is also scoped
    per ``(pid, window_id)`` and replaced by the next snapshot, so an element token
    used without a snapshot of that surface is not merely untidy -- it can address
    something the model never looked at.
    """
    if name not in AIMED_ACTION_TOOLS:
        return None
    state = cua_target_state.get()
    pid, window_id = kwargs.get("pid"), kwargs.get("window_id")
    missing = [
        field
        for field, value in (("pid", pid), ("window_id", window_id))
        if not isinstance(value, int) or value <= 0
    ]
    if missing:
        return fault_message(
            CuaFault.TARGET,
            f"{name} is missing {' and '.join(missing)}: resolve the target with "
            "list_apps/list_windows first -- never ask the user for a pid.",
        )
    if state is None:
        return None
    key = f"{pid}:{window_id}"
    if kwargs.get("element_token") and not state["snapshots"].get(key):
        return fault_message(
            CuaFault.ACTION,
            f"{name} used an element_token without a current get_window_state of "
            f"pid {pid} window {window_id}.",
        )
    return None


def _with_carried_context(name: str, text: Any) -> Any:
    """Tell the model, once per turn, which surface this conversation left off on.

    Continuity the model cannot see is not continuity: without this line a second
    turn has to guess whether the focus it finds is the one it asked for earlier
    or something else that happens to be in front.
    """
    state = cua_target_state.get()
    if not state or not isinstance(text, str):
        return text
    carried = state.get("carried_focus")
    if carried is None or state.get("note_shown") or name not in OBSERVATION_TOOL_NAMES:
        return text
    state["note_shown"] = True
    pid, window_id = carried
    identifier = state.get("apps", {}).get(pid) or "the application"
    return (
        f"{text}\n[continuing this conversation in {identifier} "
        f"(pid {pid}, window {window_id}); re-snapshot before acting]"
    )


def _remember_target_state(name: str, kwargs: dict[str, Any], result: Any) -> None:
    """Track which surface was snapshotted, and what makes that stale."""
    state = cua_target_state.get()
    if state is None:
        return
    # R03: the observation timestamp is recorded when the observation
    # actually happened; the authority layer consumes it as evidence and
    # refuses stale/absent observations (never stamps one itself).
    import time as _time

    state["observed_at_ms"] = int(_time.time() * 1000)
    if name in INVALIDATES_SNAPSHOTS_TOOLS or kwargs.get("delivery_mode") == "foreground":
        # Raising or foregrounding cascades the window: every frame read before it
        # now points somewhere else.
        state["snapshots"].clear()
        return
    structured = getattr(result, "structured", None) or {}
    if not isinstance(structured, dict):
        return
    _remember_app_identities(name, structured, state)
    if name != "get_window_state":
        return
    pid, window_id = structured.get("pid"), structured.get("window_id")
    snapshot_id = structured.get("snapshot_id")
    if isinstance(pid, int) and isinstance(window_id, int) and isinstance(snapshot_id, str):
        state["snapshots"][f"{pid}:{window_id}"] = snapshot_id
        state["focus"] = (pid, window_id)


def _remember_app_identities(name: str, structured: dict[str, Any], state: dict[str, Any]) -> None:
    """Record which pid belongs to which application, from real observations.

    Only what the driver already reported: ``list_apps`` entries, and the pid and
    bundle id a ``launch_app`` answer carries. Nothing is looked up on the side,
    so the gate can only ever know what the loop has seen.
    """
    apps: dict[int, str] = state.setdefault("apps", {})
    if name == "list_apps":
        state["inventory_observed_at_ms"] = int(time.time() * 1000)
        apps.clear()
        for entry in structured.get("apps") or structured.get("applications") or []:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("pid")
            identifier = entry.get("bundle_id") or entry.get("name")
            if isinstance(pid, int) and pid > 0 and isinstance(identifier, str):
                apps[pid] = identifier
    elif name == "launch_app":
        pid = structured.get("pid")
        identifier = structured.get("bundle_id") or structured.get("name")
        if isinstance(pid, int) and pid > 0 and isinstance(identifier, str):
            apps[pid] = identifier
    elif name == "get_window_state":
        pid = structured.get("pid")
        identifier = structured.get("bundle_id") or structured.get("app_name")
        if isinstance(pid, int) and pid > 0 and isinstance(identifier, str):
            apps.setdefault(pid, identifier)


def _enriched_description(tool: BaseTool) -> str:
    name = getattr(tool, "name", None)
    base = ""
    if isinstance(name, str) and name in COMPACT_TOOL_DESCRIPTIONS:
        base = COMPACT_TOOL_DESCRIPTIONS[name]
    else:
        base = getattr(tool, "description", "") or ""
    if name in _DESCRIPTION_ENRICHED_TOOLS:
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
        # Stop outranks everything: a user who pressed Stop must be told "stopped",
        # never handed a targeting complaint for an action that was already dead.
        if run is not None:
            try:
                run.require_active()
            except DesktopRunCancelled:
                return CANCELLED_ACTION_NOTICE
        # Then the deterministic checks, before the lease is taken: a call this
        # module refuses must not serialize against the desktop, activate a driver
        # session, or spend budget a queued action would wait behind.
        refusal = await _precheck(name, kwargs)
        if refusal is not None:
            return refusal
        try:
            async with run.action() if run is not None else nullcontext():
                if run is not None:
                    run.require_active()
                return await dispatch(kwargs)
        except DesktopRunCancelled:
            return CANCELLED_ACTION_NOTICE

    async def _precheck(tool_name: str, kwargs: dict[str, Any]) -> str | None:
        """The deterministic gate and the targeting invariant, as a refusal."""
        state = cua_target_state.get() or {"snapshots": {}, "focus": None, "apps": {}}
        observed_apps = state.get("apps", {})
        if classify_cua_call(tool_name, kwargs, observed_apps) == ActionClass.SENSITIVE:
            target = _target_identifier(kwargs, observed_apps) or tool_name
            ledger = cua_action_ledger.get()
            if ledger is not None:
                ledger_id = await _ledger_plan(ledger, tool_name, kwargs)
                if ledger_id is not None:
                    await _ledger_observe(ledger, ledger_id, "refused", "sensitive_target")
            logger.info(
                "cua_action_refused_sensitive",
                extra={"event": "cua_action_refused_sensitive", "tool": tool_name},
            )
            return (
                f"Refused: {tool_name} targets {target!r}, which holds credentials, system "
                "authority or a shell. Ask the user to do this step, or name the "
                "non-sensitive application they actually mean. Everything else on "
                "this task may continue."
            )
        return _targeting_error(tool_name, kwargs)

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
        # D3: vision is an on-demand recovery rung. When the model asks for the
        # picture it gets the picture; otherwise the capture stays a local artifact
        # so an ordinary observation never pays for base64.
        wants_inline_image = (
            bool(kwargs.get("include_screenshot")) and name in SCREENSHOT_CAPABLE_TOOLS
        )
        if (
            captures_screenshot
            and artifact_dir
            and kwargs.get("include_screenshot", True)
            and not wants_inline_image
            and not kwargs.get("screenshot_out_file")
            and not mission_screenshots_withheld.get()
        ):
            kwargs["screenshot_out_file"] = _artifact_screenshot_path(artifact_dir, name)
        if mission_screenshots_withheld.get():
            # Raw pixels must never become retained evidence or inline model
            # input in mission mode (file 03 §10): withhold the capture.
            kwargs.pop("screenshot_out_file", None)
            kwargs["include_screenshot"] = False
            wants_inline_image = False
        # R02/F02: role enforcement at the REAL dispatch boundary. A
        # Controller role (PLAN/RECOVER/REVIEW/INFO) may never dispatch a
        # desktop tool through any wrapped reference.
        role = controller_role_var.get()
        if role in DESKTOP_FORBIDDEN_ROLES:
            return (
                f"Refused: the {role} role may not use the desktop tool "
                f"{name}. Reasoning roles submit structured results; only "
                "the bounded executor dispatches desktop actions."
            )
        # R03/F03: the mission guard covers scope-sensitive READS as well
        # as mutations; the authority layer classifies the action and
        # decides what an observation may address.
        guard = mission_dispatch_guard.get()
        if guard is not None:
            refusal = await guard(name, kwargs)
            if refusal is not None:
                return refusal
        if is_mutating:
            # Identical-mutation loop guard: the same tool with the same
            # arguments, three times in a row, is the "let me click" loop the
            # master plan forbids (live 2026-09-26 03:13: one link clicked
            # seven times). One retry is allowed; the third stop is a named
            # blocker, and nothing is dispatched.
            state = cua_target_state.get()
            if state is not None:
                import hashlib as _hashlib

                digest = _hashlib.sha256(
                    repr(sorted(kwargs.items())).encode()
                ).hexdigest()[:16]
                recent = state.get("last_identical_mutation")
                if (
                    isinstance(recent, dict)
                    and recent.get("tool") == name
                    and recent.get("digest") == digest
                ):
                    recent["count"] = int(recent.get("count", 1)) + 1
                    if recent["count"] >= 3:
                        return (
                            f"Error: [loop] the identical {name} action has now been "
                            f"dispatched {recent['count']} times in a row without "
                            "the screen answering it. Repeating it again is not a "
                            "recovery; take a different route (another element, "
                            "the menu bar, a hotkey, verify visually) or report "
                            "what is blocking this step."
                        )
                else:
                    state["last_identical_mutation"] = {"tool": name, "digest": digest, "count": 1}
            budget = cua_run_budget.get()
            if budget is not None:
                budget.consume(name)  # raises CuaBudgetExceeded at the ceiling
            logger.info(
                "cua_mutating_action",
                extra={"event": "cua_mutating_action", "tool": name},
            )
        ledger = cua_action_ledger.get()
        ledger_id: int | None = None
        if is_mutating:
            if ledger is None and mission_audit_strict.get():
                # R04/F04 (RP03): strict mission mode with NO ledger at all
                # is a blocker, exactly like a failed ledger write. No
                # durable intent, no mutation.
                logger.warning(
                    "mission_action_blocked_without_ledger",
                    extra={"event": "mission_action_blocked_without_ledger", "tool": name},
                )
                return (
                    "Refused: no action ledger is bound to this execution "
                    f"({name} was not dispatched; storage unavailable)."
                )
            if ledger is not None:
                ledger_id = await _ledger_plan(ledger, name, kwargs)
                if ledger_id is None and mission_audit_strict.get():
                    # A04 fail-closed fix for the mission path: no durable intent
                    # row means no mutation. The legacy path keeps its documented
                    # fail-open behavior.
                    logger.warning(
                        "mission_action_blocked_without_audit",
                        extra={"event": "mission_action_blocked_without_audit", "tool": name},
                    )
                    return (
                        "Refused: the action ledger could not record the intent for "
                        f"{name} (storage unavailable). Nothing was dispatched; the "
                        "mission will reconcile instead of acting unrecorded."
                    )
        if run is not None:
            run.require_active()
        final_check = mission_final_dispatch_check.get()
        if final_check is not None:
            refusal = final_check()
            if refusal is not None:
                if ledger_id is not None:
                    await _ledger_observe(ledger, ledger_id, "refused", "final_admission")
                return refusal
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
            return fault_guidance(f"outcome unobserved (timeout): {exc}", exc)
        except Exception as exc:  # noqa: BLE001 -- tool errors become agent-visible text
            if ledger_id is not None:
                await _ledger_observe(ledger, ledger_id, "failed", str(exc)[:120])
            # Live 2026-09-18 and 2026-09-24: a session can end mid-run and the
            # driver's own error demands revival -- but the wording changed
            # between driver builds, so the decision is made on the classified
            # fault rather than one phrase. OBSERVATIONS may be retried after one
            # explicit revival (no effect to corrupt). Mutating actions are
            # NEVER blindly retried here -- their outcome is unknown; the
            # model sees the error and decides.
            if (
                not is_mutating
                and run is not None
                and classify_exception(exc) is CuaFault.SESSION
            ):
                try:
                    await run.revive()
                    result = await original(**kwargs)
                    if ledger_id is not None:
                        await _ledger_observe(ledger, ledger_id, "confirmed")
                    return _model_text(result, name=name)
                except Exception as revive_exc:  # noqa: BLE001
                    detail = str(revive_exc).strip() or str(exc)
                    return fault_guidance(f"session revive failed: {detail[:200]}", revive_exc)
            return fault_guidance(str(exc) or type(exc).__name__, exc)
        if ledger_id is not None:
            await _ledger_observe(ledger, ledger_id, "confirmed")
        _remember_target_state(name, kwargs, result)
        payload = _structured_of(result)
        if payload is not None:
            STRUCTURED_CALLS.record(name, payload)
        text = _model_text(result, allow_images=wants_inline_image, name=name)
        return _with_carried_context(name, text)

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

    def _model_text(result: Any, *, allow_images: bool = False, name: str = "") -> Any:
        """Render normalized ToolOutcomes as bounded model-facing text.

        Never a dataclass repr. Images reach the model only when this call asked
        for them (the recovery rung); otherwise a capture is reported by count and
        kept as a local artifact. A refusal the driver reported *successfully* --
        ``effect: refused``, a stale window id -- still gets its next move named
        here, because that is where the model can still act on it.
        """
        from assistant.runtime.cua_faults import FAULT_MESSAGES, classify_payload
        from assistant.tools.result_normalizer import ToolOutcome

        if not isinstance(result, ToolOutcome):
            return result
        blocks = result.model_content(allow_images=allow_images)
        text = "\n".join(str(block.get("text", "")) for block in blocks)
        if allow_images and result.images:
            # The picture is the point of this call, so the blocks go through as
            # they are; flattening them to text is what made the rung useless.
            return blocks
        if result.images and not allow_images:
            text += f"\n[{len(result.images)} screenshot(s) retained locally]"
        if result.truncated:
            text += "\n[observation truncated]"
        if name == "get_window_state" and isinstance(result.structured, dict):
            from assistant.velo.scene import compact_observation

            compact = compact_observation(result.structured)
            if compact:
                # What is ON SCREEN, one line each, tokens included: the raw
                # tree's truncated head is browser chrome and the model never
                # saw the control the user named.
                text = compact
        if name == "list_windows" and isinstance(result.structured, dict):
            enumerated = _window_enumeration(result.structured)
            if enumerated:
                text = f"{text}\n{enumerated}".strip()
        fault = classify_payload(result.structured) if result.structured else None
        if fault is None:
            fault = classify_text(result.text)
        if fault is CuaFault.UNKNOWN and result.effect == "unverifiable":
            fault = CuaFault.UNVERIFIABLE
        if fault is CuaFault.UNVERIFIABLE:
            return (
                f"{text}\n{RECOVERY_GUIDANCE[FaultRecovery.VERIFY_VISUALLY.value]}".strip()
            )
        if fault is not None and fault is not CuaFault.UNKNOWN:
            # The driver can answer a refusal as an ordinary result. Left as raw
            # JSON the model reads it as "done"; named, it reads as a step back.
            return (
                f"{text}\n[{fault.value}] {FAULT_MESSAGES[fault]} "
                f"-> {RECOVERY_GUIDANCE[RECOVERY_FOR[fault].value]}"
            ).strip()
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
