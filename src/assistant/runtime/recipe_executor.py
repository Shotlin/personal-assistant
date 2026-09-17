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
from assistant.runtime.runs import ActionState
from assistant.runtime.session import DesktopRunCancelled
from assistant.tools.policy import CUA_ALLOWED_TOOL_NAMES, MUTATING_TOOL_NAMES, OBSERVATION_DEFAULTS
from assistant.tools.result_normalizer import ToolOutcome, normalize_mcp_result


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
    ) -> None:
        self._tools = dict(cua_tools_by_name)
        self._store = run_store
        self._run_id = run_id
        self._budget = budget
        self._run = run
        self._recipe = "local"
        self._step = 0

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
        if self._store is not None and self._run_id is not None:
            ledger_id = await self._store.record_action(
                self._run_id, step_id=f"{self._recipe}:{self._step}", tool_name=name,
            )

        async def mark(state: ActionState, evidence: str = "") -> None:
            if self._store is not None and ledger_id is not None:
                await self._store.mark_action(ledger_id, state, evidence)

        try:
            if self._run is not None:
                self._run.require_active()
            async with self._run.action() if self._run is not None else nullcontext():
                if self._run is not None:
                    self._run.require_active()
                    if "session" not in tool.args:
                        raise RecipeFailure(f"{name} cannot bind the trusted desktop session")
                    arguments["session"] = self._run.session_id
                for key, value in OBSERVATION_DEFAULTS.get(name, {}).items():
                    if key in tool.args:
                        arguments.setdefault(key, value)
                # Validate native schema before spending budget or dispatching.
                if set(arguments) - set(tool.args):
                    raise RecipeFailure(f"{name} does not support the required argument schema")
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
            raise RecipeFailure(f"{name} failed: {type(exc).__name__}") from exc
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
        return await self._invoke("launch_app", {"app_id": app_id})

    async def read_state(self) -> ToolOutcome:
        return await self._invoke("get_window_state", {})

    async def verify_foreground(self, app_id: str) -> ToolOutcome:
        """Return fresh evidence; the recipe compares observed identity itself."""
        return await self.read_state()

    async def clear_display(self) -> ToolOutcome:
        return await self._invoke("press_key", {"key": "Escape"})

    async def type_text(self, text: str) -> ToolOutcome:
        return await self._invoke("type_text", {"text": text})

    async def read_display(self) -> ToolOutcome:
        # Evaluation is an explicit mutation, not hidden inside an observation.
        self._require_tools("press_key", "get_window_state")
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
