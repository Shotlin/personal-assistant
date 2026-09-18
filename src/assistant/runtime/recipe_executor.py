"""Trusted WP5 executor: raw allowlisted tools, one ledger/budget gate per call.

Supply raw normalized CUA tools, NOT policy-wrapped model-facing tools (those
flatten evidence and own a second budget/session gate). All methods share the
provided DesktopRun. Keyboard macros are expanded into separate native calls.
The driver must support these explicit semantic argument/evidence contracts;
missing tools or incompatible schemas fail closed, never guess coordinates.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import replace
from typing import Protocol

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from assistant.runtime.recipe_errors import RecipeFailure
from assistant.runtime.runs import ActionState, RunActionLedger
from assistant.runtime.session import DesktopRunCancelled
from assistant.tools.policy import CUA_ALLOWED_TOOL_NAMES, MUTATING_TOOL_NAMES, OBSERVATION_DEFAULTS
from assistant.tools.result_normalizer import ToolOutcome, normalize_mcp_result

#: Semantic app identities (master plan WP5): recipe vocabulary stays
#: stable (chrome/notes/...) while the real driver's launch schema wants
#: macOS bundle identifiers. Verified live against cua-driver v0.28.2.
APP_BUNDLE_IDS: dict[str, str] = {
    "chrome": "com.google.Chrome",
    "safari": "com.apple.Safari",
    "terminal": "com.apple.Terminal",
    "calculator": "com.apple.calculator",
    "notes": "com.apple.Notes",
    "finder": "com.apple.finder",
    "mail": "com.apple.Mail",
}


class Budget(Protocol):
    def consume(self, tool_name: str) -> None: ...


class ActionStore(Protocol):
    async def record_action(self, run_id: str, step_id: str, tool_name: str) -> int: ...
    async def mark_action(
        self, ledger_id: int, state: ActionState, evidence_ref: str = "",
    ) -> None: ...


class ActiveRun(Protocol):
    @property
    def session_id(self) -> str: ...
    def require_active(self) -> None: ...
    def action(self) -> AbstractAsyncContextManager[None]: ...


def _clean(value: object) -> object:
    """Drop image payloads even from unexpected nested structured content."""
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_url"}:
            return "[screenshot retained locally]"
        return {str(k): _clean(v) for k, v in value.items()
                if not any(term in str(k).lower() for term in
                           ("base64", "screenshot", "image", "data"))}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?:data:image/\S+|[A-Za-z0-9+/]{200,}={0,2})",
                      "[image payload omitted]", value)[:4000]
    return value


def _pid_from(outcome: ToolOutcome) -> int | None:
    """Extract the launched app's pid from structured evidence or text.

    cua-driver launch_app reports 'Launched <App> (pid NNN)...'; the
    adapter path may or may not expose a structured pid field, so the
    text remains a legitimate evidence source (bounded, own process).
    """
    pid = outcome.structured.get("pid")
    if isinstance(pid, int) and pid > 0:
        return pid
    match = re.search(r"\bpid (\d+)", outcome.text)
    if match:
        return int(match.group(1))
    return None


def _outcome(raw: object) -> ToolOutcome:
    if isinstance(raw, ToolOutcome):
        outcome = raw
    elif isinstance(raw, str):
        failed = raw.lower().startswith(("error:", "[run cancelled"))
        outcome = ToolOutcome("failed" if failed else "ok", "unverifiable", text=raw)
    elif isinstance(raw, dict):
        outcome = ToolOutcome(
            str(raw.get("status", "failed" if raw.get("ok") is False else "ok")),
            str(raw.get("effect", "not_applicable")), structured=raw,
        )
    else:
        outcome = normalize_mcp_result(raw)
    structured = _clean(outcome.structured)
    return replace(outcome, images=[], text=str(_clean(outcome.text)),
                   structured=structured if isinstance(structured, dict) else {})


class RecipeExecutor:
    def __init__(
        self, *, cua_tools_by_name: Mapping[str, BaseTool],
        run_store: ActionStore | None = None, run_id: str | None = None,
        budget: Budget | None = None, run: ActiveRun | None = None,
        action_ledger: RunActionLedger | None = None,
    ) -> None:
        self._tools = dict(cua_tools_by_name)
        self._store = run_store
        self._ledger = action_ledger
        self._run_id = run_id
        self._budget = budget
        self._run = run
        self._recipe = "local"
        self._step = 0
        self._launched_pid: int | None = None

    def begin_recipe(self, recipe_id: str) -> None:
        """Bind ledger labels; create one executor per serial request."""
        self._recipe = recipe_id

    def _require_tools(self, *names: str) -> None:
        for name in names:
            if name not in CUA_ALLOWED_TOOL_NAMES or name not in self._tools:
                raise RecipeFailure(f"Required native tool unavailable: {name}")

    async def _invoke(self, name: str, arguments: Mapping[str, object]) -> ToolOutcome:
        arguments = dict(arguments)
        self._require_tools(name)  # unsupported actions never spend budget
        tool = self._tools[name]
        self._step += 1
        ledger_id = None
        if self._ledger is not None and name in MUTATING_TOOL_NAMES:
            ledger_id = await self._ledger.plan(tool_name=name)
        elif self._store is not None and self._run_id is not None:
            ledger_id = await self._store.record_action(
                self._run_id, step_id=f"{self._recipe}:{self._step}", tool_name=name,
            )

        async def mark(state: ActionState, evidence: str = "") -> None:
            if self._ledger is not None and ledger_id is not None:
                await self._ledger.observe(ledger_id, state, evidence)
            elif self._store is not None and ledger_id is not None:
                await self._store.mark_action(ledger_id, state, evidence)

        try:
            if self._run is not None:
                self._run.require_active()
            async with self._run.action() if self._run is not None else nullcontext():
                if self._run is not None:
                    self._run.require_active()
                    if "session" in tool.args:
                        arguments["session"] = self._run.session_id
                for key, value in OBSERVATION_DEFAULTS.get(name, {}).items():
                    if key in tool.args:
                        arguments.setdefault(key, value)
                # Validate native schema before spending budget or dispatching.
                schema = tool.get_input_schema()
                if issubclass(schema, BaseModel):
                    schema.model_validate(arguments)
                else:
                    schema.parse_obj(arguments)
                if self._budget is not None and name in MUTATING_TOOL_NAMES:
                    self._budget.consume(name)
                outcome = _outcome(await tool.ainvoke(arguments))
                if self._run is not None:
                    self._run.require_active()
        except (TimeoutError, asyncio.CancelledError, DesktopRunCancelled):
            await mark("unknown")
            raise
        except Exception as exc:
            await mark("failed")
            # Keep the driver's own actionable text (e.g. the live-observed
            # 'permissions_pending: macOS Accessibility or Screen Recording
            # permission is still pending...'), not just the exception type:
            # a bare 'DesktopDriverError' hides what the user must fix.
            detail = str(exc).strip() or type(exc).__name__
            raise RecipeFailure(f"{name} failed: {detail[:300]}") from exc
        state: ActionState = (
            "confirmed" if outcome.status == "ok" else
            "unknown" if outcome.status == "unknown" else "failed"
        )
        await mark(state, outcome.summary())
        return outcome

    async def launch_app(self, app_id: str) -> ToolOutcome:
        from assistant.runtime.router import APP_IDS

        if app_id not in APP_IDS:
            raise RecipeFailure("Unsupported application")
        # Semantic lowering (verified against the real cua-driver v0.28.2
        # schema, 2026-09-17): launch_app exposes bundle_id/name with
        # additionalProperties=false -- there is no app_id parameter, so a
        # raw app_id payload is rejected by the driver's schema.
        payload: dict[str, object] = {"bundle_id": APP_BUNDLE_IDS[app_id]}
        tool_args = self._tools["launch_app"].args if "launch_app" in self._tools else {}
        if "bundle_id" not in tool_args:
            if "app_id" not in tool_args:
                raise RecipeFailure("launch_app lacks a supported identity parameter")
            payload = {"app_id": app_id}
        outcome = await self._invoke("launch_app", payload)
        self._launched_pid = _pid_from(outcome)
        return outcome

    async def read_state(self) -> ToolOutcome:
        """Fresh observation via any available allowlisted observation tool.

        cua-driver exposes window/desktop state under different native
        names; a recipe needs identity evidence, not one specific name.
        get_window_state requires pid+window_id in v0.28.2 (verified
        live), so it is only usable with a prior window listing.
        """
        for name in ("get_desktop_state", "get_accessibility_tree",
                     "get_window_state"):
            if name in self._tools:
                return await self._invoke(name, {})
        raise RecipeFailure("Required native tool unavailable: any observation tool")

    async def verify_foreground(self, app_id: str) -> ToolOutcome:
        """Fresh foreground evidence from list_apps' per-app active flag.

        get_desktop_state carries NO foreground identity (verified live
        2026-09-18: display/screenshot fields only), so deriving identity
        from it can never satisfy ``foreground_matches`` on the real
        driver. list_apps reports ``active`` per app; when list_apps is
        unavailable the legacy read_state path remains the fallback.
        """
        if "list_apps" in self._tools:
            outcome = await self._invoke("list_apps", {})
            if outcome.status == "ok":
                target = APP_BUNDLE_IDS.get(app_id, "")
                for app in outcome.structured.get("apps", []) or []:
                    if not isinstance(app, dict):
                        continue
                    identity = (
                        app.get("bundle_id") == target
                        or str(app.get("name", "")).lower() == app_id
                    )
                    if not identity:
                        continue
                    active = app.get("active") is True
                    return ToolOutcome(
                        status="ok" if active else "failed",
                        effect="not_applicable",
                        text=f"{app.get('name')}: active={app.get('active')}",
                        structured={
                            "foreground_app": app_id if active else None,
                            "foreground_name": app.get("name"),
                            "pid": app.get("pid"),
                            "modal": False,
                        },
                    )
                return ToolOutcome(
                    "failed",
                    "unverifiable",
                    text=f"{app_id} is not running",
                    structured={"foreground_app": None, "modal": False},
                )
        return await self.read_state()

    async def activate(self, app_id: str) -> ToolOutcome:
        """Bring the launched app to the foreground via its pid.

        macOS may open a freshly launched app behind the current
        frontmost app (observed live 2026-09-18: 'Launched Calculator
        (pid NNN) in background'). With multiple top-level windows the
        driver refuses a pid-only activation and reports eligible
        window candidates (verified live 2026-09-18); the first
        candidate is then targeted explicitly. This is a budgeted
        mutation like any other native action.
        """
        self._require_tools("bring_to_front")
        pid = self._launched_pid
        if pid is None:
            raise RecipeFailure("No launch pid available to bring to front")
        outcome = await self._invoke("bring_to_front", {"pid": pid})
        if outcome.status == "ok":
            return outcome
        window_id = self._window_id_from(outcome)
        if window_id is None:
            return outcome
        return await self._invoke(
            "bring_to_front", {"pid": pid, "window_id": window_id}
        )

    @staticmethod
    def _pick_window_id(candidates: object) -> int | None:
        """Usable window id from a driver candidate list.

        Prefer the on-screen, titled window (the one a user means by
        'open the app'); fall back to the first listed candidate.
        """
        if not isinstance(candidates, list):
            return None
        usable: list[tuple[int, int]] = []  # (score, window_id)
        for candidate in candidates:
            window_id = (
                candidate.get("window_id")
                if isinstance(candidate, dict)
                else candidate
            )
            if not isinstance(window_id, int) or window_id <= 0:
                continue
            score = 0
            if isinstance(candidate, dict):
                if candidate.get("is_on_screen") is True:
                    score += 2
                if str(candidate.get("title", "")).strip():
                    score += 1
            usable.append((score, window_id))
        if not usable:
            return None
        return max(usable, key=lambda item: (item[0], -item[1]))[1]

    def _window_id_from(self, outcome: ToolOutcome) -> int | None:
        """Extract a window id from refusal evidence or the launch report."""
        window_id = self._pick_window_id(outcome.structured.get("candidates"))
        if window_id is not None:
            return window_id
        windows = outcome.structured.get("windows")
        if isinstance(windows, list):
            return self._pick_window_id(windows)
        return None

    async def clear_display(self) -> ToolOutcome:
        return await self._invoke("press_key", {"key": "Escape"})

    async def type_text(self, text: str) -> ToolOutcome:
        return await self._invoke("type_text", {"text": text})

    async def read_display(self) -> ToolOutcome:
        # Evaluation is an explicit mutation, not hidden inside an observation.
        if "press_key" not in self._tools or not any(
            name in self._tools for name in
            ("get_window_state", "list_windows", "get_desktop_state")
        ):
            raise RecipeFailure("Required native tool unavailable: press_key and observation")
        outcome = await self._invoke("press_key", {"key": "Enter"})
        if outcome.status != "ok":
            return outcome
        return await self.read_state()

    async def open_url(self, url: str) -> ToolOutcome:
        self._require_tools("press_key", "type_text")
        for name, args in [
            ("press_key", {"key": "Command+l"}),
            ("type_text", {"text": url}),
            ("press_key", {"key": "Enter"}),
        ]:
            outcome = await self._invoke(name, args)
            if outcome.status != "ok":
                return outcome
        return outcome

    async def browser_navigate(self, url: str) -> ToolOutcome:
        return await self.open_url(url)
