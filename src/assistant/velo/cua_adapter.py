"""Velo's only computer interface (Velo spec section 6).

Wraps the repository's existing bounded CUA infrastructure -- the
persistent MCP connection (:func:`assistant.tools.cua.open_cua_connection`),
the run-scoped desktop session with local cancellation
(:class:`assistant.runtime.session.DesktopRun`), the per-run mutating budget
(:class:`assistant.agent.context.RunBudget`), and result normalization
(:func:`assistant.tools.result_normalizer.normalize_mcp_result`) -- behind
three methods:

    observe() -> VeloObservation
    execute(decision, observation, objective) -> VeloActionResult
    verify(decision, before) -> VeloVerification

Raw MCP details never reach JEV. Observation is semantic/accessibility
first; screenshots are never taken by this adapter. Unknown mutation
outcomes are surfaced as ``unknown`` so the loop re-observes instead of
blindly repeating (Velo spec section 15).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from assistant.agent.context import RunBudget
from assistant.runtime.session import DesktopRun
from assistant.tools.cua import CuaConnection
from assistant.tools.result_normalizer import ToolOutcome
from assistant.velo.types import (
    MUTATING_ACTION_KINDS,
    VeloActionKind,
    VeloActionResult,
    VeloCuaError,
    VeloDecision,
    VeloObjective,
    VeloObservation,
    VeloTarget,
    VeloVerification,
)

logger = logging.getLogger("assistant.velo.cua_adapter")

#: Text-first observation defaults, mirroring the gateway policy baseline.
_MAX_ELEMENTS = 120
_MAX_DEPTH = 12

#: Maximum targets the adapter keeps in one observation (JEV Choice options
#: are bounded by the API at 255; stay comfortably below).
_MAX_TARGETS = 120

_LABEL_CHAR_LIMIT = 80

_FRONTMOST_FLAGS = ("frontmost", "is_frontmost", "focused", "active", "foreground")

#: Sani and the CUA helper are never valid desktop targets. Sani's pill and
#: panel are always-on-top, so Sani is routinely the frontmost application; if
#: the adapter observes itself it sees a transparent overlay with no actionable
#: elements, reports ``[0 targets]``, and the loop keeps deciding OBSERVE
#: forever instead of ever moving the cursor.
_SELF_APP_IDS = frozenset({"app.sani.local", "com.trycua.driver"})
_SELF_APP_NAMES = frozenset({"sani", "cua-driver", "cua driver", "cuadriver"})
_ELEMENT_ROLE_KEYS = ("role", "type", "subrole")
_ELEMENT_LABEL_KEYS = ("label", "title", "name", "description")
_ELEMENT_VALUE_KEYS = ("value", "state", "text")
_ELEMENT_TOKEN_KEYS = ("element_token", "token")


def allowed_apps_from_manifest(path: str | Path) -> dict[str, str]:
    """Extract {bundle_id: display_name} from the capability manifest.

    Mechanical translation of the app allowlist; JEV only ever sees these
    launch candidates, never arbitrary app names (Velo spec section 13).
    """
    import yaml

    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise VeloCuaError(f"capability manifest not found at {manifest_path}")
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    # Manifest v3 layout: allow.tools is the tool list; resources.apps is
    # the application allowlist at the top level.
    apps = (data or {}).get("resources", {}).get("apps", [])
    allowed: dict[str, str] = {}
    for app in apps:
        if not isinstance(app, dict):
            continue
        bundle_id = str(app.get("bundle_id", "")).strip()
        if not bundle_id or not app.get("launch", True):
            continue
        allowed[bundle_id] = bundle_id.rsplit(".", 1)[-1]
    return allowed


class VeloCuaAdapter:
    """Observe / execute / verify against the persistent CUA connection."""

    def __init__(
        self,
        connection: CuaConnection,
        run: DesktopRun,
        budget: RunBudget,
        *,
        allowed_apps: dict[str, str] | None = None,
    ) -> None:
        self._tools = dict(connection.tools_by_name)
        self._run = run
        self._budget = budget
        self.allowed_apps = dict(allowed_apps or {})
        self.screenshot_count = 0
        self._last_observation: VeloObservation | None = None

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    async def observe(self) -> VeloObservation:
        """Cheapest useful signal: semantic/accessibility state first.

        Never captures screenshots and never polls (Velo spec section 6).
        """
        foreground = ""
        args: dict[str, Any] = {
            "include_screenshot": False,
            "max_elements": _MAX_ELEMENTS,
            "max_depth": _MAX_DEPTH,
        }
        apps = [app for app in await self._apps() if not _is_self_app(app)]
        outcome: ToolOutcome | None = None
        if apps:
            frontmost = next((app for app in apps if _is_frontmost(app)), None)
            app = frontmost or _fallback_app(apps, self.allowed_apps)
            if app is not None:
                foreground = _string_field(app, ("name", "localizedName")) or _string_field(
                    app, ("bundle_id",)
                )
                pid = app.get("pid")
                if isinstance(pid, int):
                    args["pid"] = pid
                    window_id = await self._resolve_window_id(pid, app)
                    if window_id is not None:
                        args["window_id"] = window_id
                outcome = await self._call("get_window_state", args)
        observation = self._observation_from_window_state(outcome, foreground)
        if observation is None:
            tree = await self._call("get_accessibility_tree", {"max_elements": _MAX_ELEMENTS})
            observation = self._observation_from_tree(tree, foreground)
        self._last_observation = observation
        return observation

    async def _apps(self) -> list[dict[str, Any]]:
        outcome = await self._call("list_apps", {})
        return _coerce_records(outcome, ("apps", "applications"))

    async def _resolve_window_id(
        self, pid: int, app: dict[str, Any]
    ) -> int | None:
        """Find a window to snapshot for ``pid``.

        ``get_window_state`` rejects a snapshot without a ``window_id``, and
        ``list_apps`` reports an empty ``windows`` list for every application,
        so the id has to come from ``list_windows``.
        """
        from_list = _first_window_id(app)
        if from_list is not None:
            return from_list
        if "list_windows" not in self._tools:
            return None
        outcome = await self._call("list_windows", {"pid": pid})
        data = _structured_dict(outcome) or {}
        windows = data.get("windows")
        if not isinstance(windows, list):
            return None
        return _pick_window_id(windows)

    def _observation_from_window_state(
        self, outcome: ToolOutcome | None, foreground: str
    ) -> VeloObservation | None:
        if outcome is None:
            return None
        data = _structured_dict(outcome)
        if data is None:
            return None
        window = data.get("window") if isinstance(data.get("window"), dict) else data
        elements = _first_list(data, ("elements", "ui_elements"), fallback_container=window)
        if elements is None:
            return None
        title = _string_field(window, ("title", "window_title"))
        url = _string_field(data, ("url",)) or _string_field(window, ("url",))
        focused = _string_field(data, ("focused_element", "focused"))
        modal = bool(data.get("modal", False))
        targets = _targets_from_elements(elements)
        return VeloObservation(
            foreground_app=foreground,
            window_title=title,
            url=url,
            focused_element=focused,
            modal=modal,
            targets=targets,
        )

    def _observation_from_tree(self, outcome: ToolOutcome, foreground: str) -> VeloObservation:
        data = _structured_dict(outcome) or {}
        elements = _first_list(data, ("elements", "tree", "nodes"))
        targets = _targets_from_elements(elements or [], allow_missing_tokens=True)
        return VeloObservation(
            foreground_app=foreground,
            window_title=_string_field(data, ("window_title", "title")),
            url=_string_field(data, ("url",)),
            focused_element=_string_field(data, ("focused_element", "focused")),
            modal=bool(data.get("modal", False)),
            targets=targets,
        )

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        decision: VeloDecision,
        observation: VeloObservation,
        objective: VeloObjective,
    ) -> VeloActionResult:
        """Dispatch one validated decision; the only mutation entry point."""
        if decision.status.name != "ACT" or decision.action is None:
            raise VeloCuaError(f"non-action decision reached the executor: {decision.status}")
        if decision.action not in MUTATING_ACTION_KINDS:
            raise VeloCuaError(f"non-mutating action {decision.action} is not executable here")
        tool_name, kwargs = self._build_tool_call(decision, observation, objective)
        if tool_name not in self._tools:
            raise VeloCuaError(f"required CUA tool {tool_name!r} is not available")
        self._budget.consume(decision.action.value)
        outcome = await self._call(tool_name, kwargs)
        return _action_result(outcome, tool_name)

    def _build_tool_call(
        self,
        decision: VeloDecision,
        observation: VeloObservation,
        objective: VeloObjective,
    ) -> tuple[str, dict[str, Any]]:
        action = decision.action
        assert action is not None  # narrowed by execute()
        target = observation.target(decision.target_id) if decision.target_id else None
        if action is VeloActionKind.LAUNCH_APP:
            bundle_id = self._resolve_bundle_id(decision.app_name)
            return "launch_app", {"bundle_id": bundle_id}
        if action is VeloActionKind.CLICK:
            if target is None:
                raise VeloCuaError(f"unknown target id {decision.target_id!r} for CLICK")
            if not target.element_token:
                raise VeloCuaError(f"target {target.id} has no element_token to click")
            return "click", {"element_token": target.element_token}
        if action is VeloActionKind.TYPE_USER_TEXT:
            text = objective.payload_text(decision.payload_id)
            if text is None:
                raise VeloCuaError(f"unknown payload id {decision.payload_id!r}")
            kwargs: dict[str, Any] = {"text": text}
            if target is not None and target.element_token:
                kwargs["element_token"] = target.element_token
            return "type_text", kwargs
        if action is VeloActionKind.PRESS_KEY:
            return "press_key", {"key": decision.key}
        if action is VeloActionKind.HOTKEY:
            # The driver takes `keys` as a list; `combo` is not one of its
            # arguments at all, and _filter_kwargs would silently drop it and
            # send an empty call.
            return "hotkey", {"keys": [part for part in decision.combo.split("+") if part]}
        if action is VeloActionKind.SCROLL:
            kwargs = {"direction": decision.direction, "amount": 5}
            if target is not None and target.element_token:
                kwargs["element_token"] = target.element_token
            return "scroll", kwargs
        raise VeloCuaError(f"unsupported action kind {action}")

    def _resolve_bundle_id(self, app_name: str) -> str:
        if app_name in self.allowed_apps:
            return app_name
        lowered = app_name.lower()
        for bundle_id, name in self.allowed_apps.items():
            if name.lower() == lowered or bundle_id.lower() == lowered:
                return bundle_id
        raise VeloCuaError(f"app {app_name!r} is not in the capability manifest allowlist")

    # ------------------------------------------------------------------
    # verification
    # ------------------------------------------------------------------

    async def verify(self, decision: VeloDecision, before: VeloObservation) -> VeloVerification:
        """Fresh observation after a mutation; never a cached read."""
        after = await self.observe()
        changed = after.signature() != before.signature()
        return VeloVerification(
            observation=after,
            changed=changed,
            note="" if changed else "scene unchanged after action",
        )

    # ------------------------------------------------------------------
    # tool plumbing
    # ------------------------------------------------------------------

    async def _call(self, tool_name: str, kwargs: dict[str, Any]) -> ToolOutcome:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise VeloCuaError(f"CUA tool {tool_name!r} is not available")
        filtered = _filter_kwargs(tool, kwargs)
        async with self._run.action():
            try:
                result = await tool.ainvoke(filtered)
            except Exception as exc:  # noqa: BLE001 -- driver errors are outcomes
                logger.warning(
                    "velo_cua_call_failed",
                    extra={"event": "velo_cua_call_failed", "tool": tool_name},
                )
                return ToolOutcome(
                    status="failed",
                    effect="unverifiable",
                    text=f"{tool_name} error: {exc}",
                )
        if not isinstance(result, ToolOutcome):
            return ToolOutcome(
                status="unknown",
                effect="unverifiable",
                text=f"{tool_name} returned unrecognized evidence",
            )
        return result


# ----------------------------------------------------------------------
# mechanical normalization helpers (no reasoning, no rules engine)
# ----------------------------------------------------------------------


def _filter_kwargs(tool: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep only arguments the tool schema declares; drop the rest."""
    schema = getattr(tool, "args", None)
    if not isinstance(schema, dict) or not schema:
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in schema}


def _structured_dict(outcome: ToolOutcome) -> dict[str, Any] | None:
    if outcome.structured:
        return outcome.structured
    text = outcome.text.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _first_list(
    data: dict[str, Any],
    keys: tuple[str, ...],
    *,
    fallback_container: dict[str, Any] | None = None,
) -> list[Any] | None:
    for container in (data, fallback_container or {}):
        for key in keys:
            value = container.get(key)
            if isinstance(value, list):
                return value
    return None


def _coerce_records(outcome: ToolOutcome, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    data = _structured_dict(outcome) or {}
    records = _first_list(data, keys) or []
    return [record for record in records if isinstance(record, dict)]


def _is_frontmost(app: dict[str, Any]) -> bool:
    return any(bool(app.get(flag)) for flag in _FRONTMOST_FLAGS)


def _is_self_app(app: dict[str, Any]) -> bool:
    bundle_id = (_string_field(app, ("bundle_id", "bundleId")) or "").lower()
    name = (_string_field(app, ("name", "localizedName")) or "").lower()
    return bundle_id in _SELF_APP_IDS or name in _SELF_APP_NAMES


def _runnable(app: dict[str, Any]) -> bool:
    """An application Sani could plausibly observe: running, with a real pid."""
    pid = app.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    if "running" in app:
        return bool(app.get("running"))
    return True


def _fallback_app(
    apps: list[dict[str, Any]], allowed_apps: dict[str, str]
) -> dict[str, Any] | None:
    """Pick an observation target when nothing reports itself frontmost.

    No application is flagged active for stretches of a normal session, so
    this is the common path rather than a rare one. Choosing the first running
    entry in driver order lands on whatever happens to be listed -- routinely a
    browser Sani is not allowed to drive -- so the allowlist narrows the choice
    and recency decides within it.
    """
    runnable = [app for app in apps if _runnable(app)]
    if not runnable:
        return None
    if allowed_apps:
        permitted = [app for app in runnable if app.get("bundle_id") in allowed_apps]
        if permitted:
            # `last_used` is an ISO-8601 UTC string, which orders as text.
            return max(permitted, key=lambda app: str(app.get("last_used") or ""))
    return runnable[0]


def _pick_window_id(windows: list[Any]) -> int | None:
    """Choose the window to observe from a real ``list_windows`` payload.

    A driver reports a dozen windows per application -- off-Space, minimized
    and 1x1 placeholder surfaces alongside the one the user can see. Only a
    visible window can be aimed at, so on-screen windows win outright and the
    largest of them is the one worth reading.
    """
    records = [window for window in windows if isinstance(window, dict)]

    def area(window: dict[str, Any]) -> float:
        bounds = window.get("bounds")
        if not isinstance(bounds, dict):
            return 0.0
        try:
            return float(bounds.get("width", 0)) * float(bounds.get("height", 0))
        except (TypeError, ValueError):
            return 0.0

    ranked = [window for window in records if window.get("is_on_screen")] or records
    if not ranked:
        return None
    best = max(ranked, key=area)
    for key in ("window_id", "id"):
        value = best.get(key)
        if isinstance(value, int):
            return value
    return None


def _first_window_id(app: dict[str, Any]) -> int | None:
    windows = app.get("windows")
    if not isinstance(windows, list) or not windows:
        return None
    first = windows[0]
    if isinstance(first, dict):
        for key in ("window_id", "id"):
            value = first.get(key)
            if isinstance(value, int):
                return value
    if isinstance(first, int):
        return first
    return None


def _string_field(record: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(record, dict):
        return ""
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value[:_LABEL_CHAR_LIMIT]
    return ""


def _targets_from_elements(
    elements: list[Any], *, allow_missing_tokens: bool = False
) -> tuple[VeloTarget, ...]:
    targets: list[VeloTarget] = []
    for position, element in enumerate(elements[:_MAX_TARGETS]):
        if not isinstance(element, dict):
            continue
        role = _string_field(element, _ELEMENT_ROLE_KEYS)
        label = _string_field(element, _ELEMENT_LABEL_KEYS)
        value = _string_field(element, _ELEMENT_VALUE_KEYS)
        if not label and not value:
            continue
        token = ""
        for key in _ELEMENT_TOKEN_KEYS:
            candidate = element.get(key)
            if isinstance(candidate, str) and candidate.strip():
                token = candidate
                break
        if not token and not allow_missing_tokens:
            continue
        targets.append(
            VeloTarget(
                id=token or f"t{position + 1}",
                role=role or "element",
                label=label,
                value=value,
                element_token=token,
            )
        )
    return tuple(targets)


def _action_result(outcome: ToolOutcome, tool_name: str) -> VeloActionResult:
    """Fail-closed mapping: unconfirmed effects are unknown, never ok."""
    if outcome.status == "failed":
        return VeloActionResult(status="failed", effect=outcome.effect, detail=outcome.text[:200])
    if outcome.effect in {"confirmed", "not_applicable"}:
        return VeloActionResult(status="ok", effect=outcome.effect, detail=outcome.text[:200])
    return VeloActionResult(
        status="unknown",
        effect=outcome.effect,
        detail=outcome.text[:200] or f"{tool_name} outcome unconfirmed",
    )
