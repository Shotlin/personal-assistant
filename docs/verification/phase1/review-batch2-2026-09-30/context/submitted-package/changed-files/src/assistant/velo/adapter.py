"""The CUA adapter: driver schemas, observation, targeting, typed results.

One execution authority for desktop work (master plan sections 3 and 6): the
controller's recipes, JEV-selected steps and the Deep Agent's own tool calls
all dispatch through the same policy-wrapped tools, so the action-class gate,
the targeting invariant, the lease and the budget are identical on every road.

The adapter adds what a deterministic executor needs and the model loop does
not:

- **Typed results.** ``call`` returns a :class:`ToolReply` whose structured
  payload is read from the policy's indexed call log -- never from rendered
  model text, never from a name-keyed global two overlapping calls can race
  on.
- **Explicit targeting.** Every aimed action carries a validated ``pid`` and
  ``window_id`` resolved from the task's bound target. Nothing here guesses a
  foreground app when the task owns one.
- **Contract failures, not silent filtering.** An argument the tool's schema
  does not declare stops with :class:`AdapterContractError` instead of
  disappearing on the way to the driver.
- **Exact target resolution.** An explicit application name resolves to that
  application -- "Safari" is Safari even while Chrome is running. Only the
  literal words "browser" ask for the browser category, and a category
  resolves through what is already running, not a preference.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from assistant.tools.policy import STRUCTURED_CALLS
from assistant.velo.contracts import (
    ActionOutcome,
    AdapterContractError,
    AppIdentity,
    OutcomeState,
    ProgressKind,
    TaskState,
    ToolReply,
)

logger = logging.getLogger("assistant.velo.adapter")

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]
IsCancelled = Callable[[], bool]

#: Words that ask for the browser *category*. An actual browser name --
#: "safari", "chrome" -- is an identity, and must resolve to that identity
#: even when another browser is running (master plan section 2: the live
#: defect was "open Safari" fronting Chrome).
BROWSER_CATEGORY_WORDS = frozenset({"browser", "web browser", "the browser"})

#: A window below this has no pixel an action can land on (policy measured
#: the menu bar at 33 points); observation may still list it.
AIMABLE_MIN_POINTS = 80.0


def _points(bounds: dict[str, Any], key: str) -> float:
    value = bounds.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


class CuaAdapter:
    """Dispatches fully resolved actions through the wrapped CUA tools."""

    def __init__(
        self,
        tools: dict[str, Any],
        *,
        on_event: OnEvent,
        cancel_check: IsCancelled | None = None,
    ) -> None:
        self._tools = tools
        self._on_event = on_event
        self._cancel_check = cancel_check

    # -- dispatch -------------------------------------------------------------

    async def call(self, tool_name: str, **kwargs: Any) -> ToolReply:
        """One wrapped tool call with a typed payload readback.

        The tool invoked here is always the policy-wrapped object from the
        runtime, so the gate, targeting invariant, lease and budget apply as
        they do for the model. The structured payload is matched to this
        exact dispatch by call index: a read that does not belong to this
        call raises rather than masquerading as evidence.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise AdapterContractError(
                f"the driver does not expose the {tool_name!r} tool this step needs"
            )
        self._validate_schema(tool_name, tool, kwargs)
        index_before = STRUCTURED_CALLS.next_index
        result = await tool.ainvoke(kwargs)
        text = result if isinstance(result, str) else str(result)
        entry = STRUCTURED_CALLS.after(index_before)
        structured: dict[str, Any] = {}
        if entry is not None:
            if entry.tool != tool_name:
                raise AdapterContractError(
                    f"structured readback for {tool_name!r} was interleaved by "
                    f"{entry.tool!r}; refusing to use it as evidence"
                )
            structured = entry.payload
        # D06: the wrapper answers refusals (guard, targeting, sensitive
        # target) as ordinary text, so ok must reflect that — a refused
        # dispatch is never a successful one.
        lowered = text.strip().lower()
        ok = not (lowered.startswith("error") or lowered.startswith("refused"))
        return ToolReply(tool=tool_name, text=text, structured=structured, ok=ok)

    async def acting_call(self, task: TaskState, tool_name: str, **kwargs: Any) -> ToolReply:
        """A desktop-changing call: cancellation and budget first, then dispatch."""
        if self._cancel_check is not None and self._cancel_check():
            task.cancelled = True
        task.check_usable()
        task.used_actions += 1
        await self._on_event("agent.progress", {"message": f"{tool_name} on {task.instruction[:48]}"})
        reply = await self.call(tool_name, **kwargs)
        task.last_outcome = _outcome_from_reply(reply)
        self._register_progress(task, ProgressKind.ACTION)
        # Foregrounding and raising cascade windows: every frame read before
        # the call is stale afterwards (policy measured this live).
        if tool_name == "bring_to_front" or kwargs.get("delivery_mode") == "foreground":
            task.invalidate()
        return reply

    async def observe_call(
        self, task: TaskState, tool_name: str, **kwargs: Any
    ) -> ToolReply:
        """A read-only call, announced and recorded like any other step."""
        if self._cancel_check is not None and self._cancel_check():
            task.cancelled = True
        task.check_usable()
        reply = await self.call(tool_name, **kwargs)
        if reply.structured:
            task.last_observation_digest = self.scene_digest(reply.structured)
        self._register_progress(task, ProgressKind.OBSERVATION)
        return reply

    async def verification_call(
        self, task: TaskState, tool_name: str, **kwargs: Any
    ) -> ToolReply:
        """A read used by outcome verification, which bounds itself.

        Waiting for a page to load means several identical observations in a
        row; that is one bounded verification activity, not decision churn,
        so it registers with the no-progress tracker as a single wait whose
        poll loop enforces its own ceiling.
        """
        if self._cancel_check is not None and self._cancel_check():
            task.cancelled = True
        task.check_usable()
        reply = await self.call(tool_name, **kwargs)
        if reply.structured:
            task.last_observation_digest = self.scene_digest(reply.structured)
        return reply

    @staticmethod
    def _register_progress(task: TaskState, kind: ProgressKind) -> None:
        """No change across actions, observations, waits and recovery ends the task.

        The digest registered is the freshest one known at that moment: an
        action registers the world as it was last observed, so two blind
        mutations with no observation between them count as no progress, and
        an observation registers what it actually saw.
        """
        from assistant.velo.contracts import TaskCancelled

        if not task.tracker.register(kind, task.last_observation_digest):
            raise TaskCancelled(
                f"no visible progress after {task.tracker.steps_without_change} "
                "steps; stopping instead of repeating"
            )

    @staticmethod
    def _validate_schema(tool_name: str, tool: Any, kwargs: dict[str, Any]) -> None:
        """An argument the schema does not declare is a stop, not a filter.

        The historical adapter intersected arguments with the tool schema and
        dropped the excess silently -- an incompatible argument vanished and
        the action ran with the rest. Here the mismatch is the failure.
        """
        declared = getattr(tool, "args", None)
        if not isinstance(declared, dict) or not declared:
            return  # fakes and untyped tools: the driver remains the authority
        unknown = [key for key in kwargs if key not in declared]
        if unknown:
            raise AdapterContractError(
                f"{tool_name} does not declare {sorted(unknown)}; declared arguments "
                f"are {sorted(declared)}"
            )

    # -- observation ----------------------------------------------------------

    async def list_apps(
        self, task: TaskState, *, for_verification: bool = False
    ) -> list[AppIdentity]:
        observe = self.verification_call if for_verification else self.observe_call
        reply = await observe(task, "list_apps")
        payload = reply.structured or _json_payload(reply.text)
        apps: list[AppIdentity] = []
        for entry in payload.get("apps") or payload.get("applications") or []:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("pid")
            apps.append(
                AppIdentity(
                    name=str(entry.get("name") or ""),
                    bundle_id=str(entry.get("bundle_id") or ""),
                    pid=int(pid) if isinstance(pid, int) and pid > 0 else None,
                    running=bool(entry.get("running", pid is not None)),
                    active=bool(entry.get("active")),
                )
            )
        return apps

    async def front_window(self, task: TaskState, pid: int) -> int | None:
        """The on-screen window worth aiming at, or None when there is none."""
        reply = await self.observe_call(task, "list_windows", pid=pid)
        payload = reply.structured or _json_payload(reply.text)
        windows = [w for w in (payload.get("windows") or []) if isinstance(w, dict)]
        on_screen = [w for w in windows if w.get("is_on_screen")]
        aimed = [
            w
            for w in on_screen
            if _points(w.get("bounds") or {}, "height") > AIMABLE_MIN_POINTS
            and _points(w.get("bounds") or {}, "width") > AIMABLE_MIN_POINTS
        ] or on_screen
        if not aimed:
            return None
        window_id = aimed[0].get("window_id")
        return int(window_id) if isinstance(window_id, int) else None

    async def window_is_on_screen(self, task: TaskState, pid: int, window_id: int) -> bool:
        """Whether a carried window id still names an on-screen window.

        The user closes and re-opens windows between commands; a carried id
        that names a dead surface must not be acted through.
        """
        reply = await self.observe_call(task, "list_windows", pid=pid)
        payload = reply.structured or _json_payload(reply.text)
        windows = [w for w in (payload.get("windows") or []) if isinstance(w, dict)]
        return any(
            w.get("window_id") == window_id and w.get("is_on_screen") for w in windows
        )

    async def observe_window(
        self,
        task: TaskState,
        pid: int,
        window_id: int,
        *,
        for_verification: bool = False,
    ) -> dict[str, Any]:
        """The window's element tree; the source of element tokens and evidence.

        ``for_verification`` reads without registering progress: the caller's
        poll loop bounds how long it waits for evidence to appear.
        """
        observe = self.verification_call if for_verification else self.observe_call
        reply = await observe(
            task,
            "get_window_state",
            pid=pid,
            window_id=window_id,
            include_screenshot=False,
            max_elements=120,
        )
        payload = reply.structured or _json_payload(reply.text)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def scene_digest(state: dict[str, Any]) -> str:
        """A stable short digest of an observation, for no-progress tracking."""
        try:
            blob = json.dumps(state, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return ""
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    # -- resolution -----------------------------------------------------------

    @staticmethod
    def resolve_app(
        wanted: str, apps: list[AppIdentity]
    ) -> tuple[AppIdentity | None, str]:
        """The one app ``wanted`` names, plus a human note when fuzzy.

        Returns ``(identity, note)``; ``identity`` is None for zero or several
        matches -- two candidates means the user decides, not the resolver.
        An explicit name never falls into the browser category: "open Safari"
        with Chrome running must resolve to Safari or fail honestly.
        """
        wanted = wanted.strip().removesuffix(".app").strip()
        if not wanted:
            return None, ""
        lowered = wanted.lower()
        if lowered in BROWSER_CATEGORY_WORDS:
            browsers = [a for a in apps if _looks_like_browser(a)]
            # "open browser" while one is up means that one, not a new launch.
            running = [a for a in browsers if a.running and a.pid]
            if len(running) == 1:
                return running[0], ""
            if not running and len(browsers) == 1:
                return browsers[0], ""
            return None, ""
        hits = [a for a in apps if a.identity_matches(wanted)]
        if len(hits) == 1:
            return hits[0], ""
        # An app the driver has not listed may still be launchable by name;
        # that decision belongs to the recipe, which reports what it did.
        return None, ""

    @staticmethod
    def closest_app(wanted: str, apps: list[AppIdentity]) -> AppIdentity | None:
        """One unambiguous near-miss, for what speech recognition heard wrong."""
        import difflib

        names: dict[str, AppIdentity] = {}
        for app in apps:
            if app.name:
                names.setdefault(app.name.lower(), app)
        hits = difflib.get_close_matches(wanted.strip().lower(), list(names), n=2, cutoff=0.8)
        return names[hits[0]] if len(hits) == 1 else None


def _looks_like_browser(app: AppIdentity) -> bool:
    markers = ("chrome", "safari", "arc", "firefox", "edge", "brave", "opera")
    name = app.name.lower()
    bundle = app.bundle_id.lower()
    return any(marker in name or marker in bundle for marker in markers)


def _outcome_from_reply(reply: ToolReply) -> ActionOutcome:
    """The coarse outcome state of a dispatched call, from its own evidence."""
    text = reply.text
    lowered = text.lower()
    if "[run cancelled" in lowered:
        return ActionOutcome(tool=reply.tool, state=OutcomeState.CANCELLED, detail=text[:200])
    if lowered.startswith("refused:") or "error:" in lowered:
        return ActionOutcome(tool=reply.tool, state=OutcomeState.FAILED, detail=text[:200])
    if reply.structured:
        effect = str(reply.structured.get("effect") or "")
        if effect == "suspected_noop":
            return ActionOutcome(tool=reply.tool, state=OutcomeState.NO_EFFECT, detail=text[:200])
    return ActionOutcome(tool=reply.tool, state=OutcomeState.DISPATCHED, detail=text[:200])


def _json_payload(text: str) -> dict[str, Any]:
    """Structured payload recovered from a fake/plain-text reply (tests)."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return {}


__all__ = [
    "AIMABLE_MIN_POINTS",
    "BROWSER_CATEGORY_WORDS",
    "CuaAdapter",
    "TaskCancelled",
]
