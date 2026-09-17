"""Fake-driver-only WP5 integration: no model, network or real desktop."""

import asyncio
from unittest.mock import Mock

import pytest
from langchain_core.tools import StructuredTool

from assistant.agent.context import RunBudget
from assistant.runtime.recipe_errors import RecipeFailure
from assistant.runtime.recipe_executor import RecipeExecutor
from assistant.runtime.recipes import execute_recipe
from assistant.runtime.router import RecipeRequest, match_local_command
from assistant.runtime.session import DesktopRunCancelled, DesktopSessionManager
from assistant.tools.policy import cua_run_budget
from assistant.tools.result_normalizer import ImageRef, ToolOutcome
from tests.helpers.fake_driver import DriverCall, FakeDriver, call_names


class Ledger:
    def __init__(self):
        self.rows = []
        self.events = []

    async def record_action(self, run_id, step_id, tool_name):
        self.rows.append({"state": "planned", "tool_name": tool_name, "step_id": step_id})
        self.events.append("planned")
        return len(self.rows) - 1

    async def mark_action(self, ledger_id, state, evidence_ref=""):
        self.rows[ledger_id]["state"] = state
        self.events.append(state)


class Desktop(FakeDriver):
    def __init__(self, ledger=None):
        super().__init__()
        self.ledger = ledger
        self.foreground = "calculator"
        self.display = "42"
        self.url = "https://www.google.com/search?q=rust+async"
        self.loaded = True
        self.error = None
        self.observations = []

    def tools(self):
        def tool(name):
            async def dispatch(
                app_id: str = "", text: str = "", key: str = "", session: str = "",
                include_screenshot: bool = False,
            ):
                if self.ledger:
                    assert self.ledger.rows[-1]["state"] == "planned"
                    self.ledger.events.append("dispatch")
                self.calls.append(DriverCall(name, {"app_id": app_id, "text": text,
                                                    "key": key, "session": session}))
                if self.error:
                    raise self.error
                if name == "get_window_state":
                    if self.observations:
                        return self.observations.pop(0)
                    return ToolOutcome("ok", "confirmed", structured={
                        "foreground_app": self.foreground, "display_value": self.display,
                        "url": self.url, "loaded": self.loaded,
                    })
                return ToolOutcome("ok", "confirmed", "acknowledged")
            return StructuredTool.from_function(
                coroutine=dispatch, name=name, description="Fake native tool",
            )
        return {name: tool(name) for name in
                ["launch_app", "type_text", "press_key", "get_window_state"]}


async def test_each_native_mutation_counts_once_and_observations_are_free():
    desktop = Desktop()
    budget = Mock(wraps=RunBudget())
    executor = RecipeExecutor(cua_tools_by_name=desktop.tools(), budget=budget)
    await executor.launch_app("calculator")
    await executor.type_text("6*7")
    await executor.read_state()
    assert [c.args[0] for c in budget.consume.call_args_list] == ["launch_app", "type_text"]


async def test_ledger_is_planned_before_dispatch_then_confirmed():
    ledger = Ledger()
    desktop = Desktop(ledger)
    executor = RecipeExecutor(cua_tools_by_name=desktop.tools(), run_store=ledger, run_id="run")
    await executor.launch_app("chrome")
    assert ledger.events == ["planned", "dispatch", "confirmed"]
    assert len(ledger.rows) == 1  # raw dispatch: never a second ledger row


@pytest.mark.parametrize("error,state", [
    (RuntimeError("driver error"), "failed"), (TimeoutError(), "unknown"),
    (asyncio.CancelledError(), "unknown"), (DesktopRunCancelled("stopped"), "unknown"),
])
async def test_ledger_marks_dispatch_failure_or_uncertainty(error, state):
    ledger = Ledger()
    desktop = Desktop(ledger)
    desktop.error = error
    executor = RecipeExecutor(cua_tools_by_name=desktop.tools(), run_store=ledger, run_id="run")
    with pytest.raises((RecipeFailure, TimeoutError, asyncio.CancelledError, DesktopRunCancelled)):
        await executor.launch_app("chrome")
    assert ledger.rows[0]["state"] == state
    assert call_names(desktop) == ["launch_app"]


async def test_cancelled_run_marks_unknown_without_dispatch_or_budget():
    ledger = Ledger()
    desktop = Desktop(ledger)
    budget = RunBudget()
    async with DesktopSessionManager(desktop).open("cancel") as run:
        run.cancel()
        executor = RecipeExecutor(cua_tools_by_name=desktop.tools(), run=run,
                                  budget=budget, run_store=ledger, run_id="cancel")
        with pytest.raises(DesktopRunCancelled):
            await executor.launch_app("chrome")
    assert ledger.rows[0]["state"] == "unknown"
    assert desktop.calls == []
    assert budget.used == 0


async def test_calculator_happy_path_single_session():
    desktop = Desktop()
    budget = RunBudget()
    async with DesktopSessionManager(desktop).open("calc") as run:
        executor = RecipeExecutor(cua_tools_by_name=desktop.tools(), budget=budget, run=run)
        result = await execute_recipe(match_local_command("what is 6*7"), executor)
        assert result["ok"] is True
        assert result["display_value"] == "42"
        actions = [c for c in desktop.calls if c.name in desktop.tools()]
        assert all(c.args["session"] == run.session_id for c in actions)
    assert call_names(desktop).count("start_session") == 1
    assert call_names(desktop).count("end_session") == 1
    assert budget.used == 4  # launch, clear, type, evaluate key


async def test_wrong_foreground_never_relaunches_or_types():
    desktop = Desktop()
    desktop.foreground = "mail"
    executor = RecipeExecutor(cua_tools_by_name=desktop.tools())
    result = await execute_recipe(RecipeRequest("open_app.v1", {"app_id": "chrome"}), executor)
    assert result["ok"] is False
    assert call_names(desktop) == ["launch_app", "get_window_state"]


async def test_retry_only_failed_observation_not_launch():
    desktop = Desktop()
    desktop.foreground = "chrome"
    desktop.observations = [ToolOutcome("failed", "unverifiable", "no observation")]
    result = await execute_recipe(match_local_command("Open Chrome"),
                                  RecipeExecutor(cua_tools_by_name=desktop.tools()))
    assert result["ok"] is True
    assert call_names(desktop) == ["launch_app", "get_window_state", "get_window_state"]


async def test_unknown_observation_is_not_retried():
    desktop = Desktop()
    desktop.observations = [ToolOutcome("unknown", "unverifiable")]
    result = await execute_recipe(match_local_command("Open Chrome"),
                                  RecipeExecutor(cua_tools_by_name=desktop.tools()))
    assert result["ok"] is False
    assert call_names(desktop) == ["launch_app", "get_window_state"]


async def test_modal_interruption_stops_before_clear_or_type():
    desktop = Desktop()
    desktop.observations = [ToolOutcome("ok", "confirmed", structured={
        "foreground_app": "calculator", "modal": True})]
    result = await execute_recipe(match_local_command("calculate 6*7"),
                                  RecipeExecutor(cua_tools_by_name=desktop.tools()))
    assert result["ok"] is False
    assert call_names(desktop) == ["launch_app", "get_window_state"]


async def test_returned_tool_failure_stops_macro_and_marks_failed():
    ledger = Ledger()
    desktop = Desktop(ledger)

    async def failed_key(key: str):
        desktop.calls.append(DriverCall("press_key", {"key": key}))
        return ToolOutcome("failed", "unverifiable", "stale target")

    tools = desktop.tools()
    tools["press_key"] = StructuredTool.from_function(
        coroutine=failed_key, name="press_key", description="Failed native key")
    executor = RecipeExecutor(cua_tools_by_name=tools, run_store=ledger, run_id="macro")
    result = await executor.open_url("https://www.google.com/search?q=rust")
    assert result.status == "failed"
    assert call_names(desktop) == ["press_key"]
    assert len(ledger.rows) == 1
    assert ledger.rows[0]["state"] == "failed"


async def test_closed_session_stops_before_new_native_mutation():
    desktop = Desktop()
    budget = RunBudget()
    async with DesktopSessionManager(desktop).open("expired") as run:
        pass
    executor = RecipeExecutor(cua_tools_by_name=desktop.tools(), budget=budget, run=run)
    with pytest.raises(DesktopRunCancelled):
        await executor.type_text("secret")
    assert budget.used == 0
    assert desktop.calls == []


async def test_navigation_macro_has_three_budget_consumptions_and_ledger_rows():
    ledger = Ledger()
    desktop = Desktop(ledger)
    budget = RunBudget()
    executor = RecipeExecutor(cua_tools_by_name=desktop.tools(), budget=budget,
                              run_store=ledger, run_id="macro")
    await executor.open_url("https://www.google.com/search?q=rust")
    assert budget.used == 3
    assert [row["tool_name"] for row in ledger.rows] == ["press_key", "type_text", "press_key"]
    assert all(row["state"] == "confirmed" for row in ledger.rows)
    assert len({row["step_id"] for row in ledger.rows}) == 3


async def test_missing_tool_consumes_no_budget():
    budget = RunBudget()
    executor = RecipeExecutor(cua_tools_by_name={}, budget=budget)
    with pytest.raises(RecipeFailure, match="launch_app"):
        await executor.launch_app("chrome")
    assert budget.used == 0


async def test_calculator_mismatch_no_retry_clicks():
    desktop = Desktop()
    desktop.display = "43"
    result = await execute_recipe(match_local_command("what is 6*7"),
                                  RecipeExecutor(cua_tools_by_name=desktop.tools()))
    assert result["ok"] is False
    assert "mismatch" in result["reason"]
    assert call_names(desktop).count("type_text") == 1
    assert "click" not in call_names(desktop)


async def test_browser_requires_loaded_url_not_just_foreground():
    desktop = Desktop()
    desktop.foreground = "chrome"
    executor = RecipeExecutor(cua_tools_by_name=desktop.tools())
    result = await execute_recipe(match_local_command("search for rust async"), executor)
    assert result["ok"] is True
    assert "launch_app" not in call_names(desktop)
    assert [c.args["text"] for c in desktop.calls if c.name == "type_text"] == [desktop.url]
    desktop.loaded = False
    result = await execute_recipe(match_local_command("search for rust async"), executor)
    assert result["ok"] is False


async def test_screenshot_payload_never_escapes_executor():
    desktop = Desktop()
    desktop.observations = [ToolOutcome("ok", "confirmed", images=[ImageRef("SECRET_BASE64")])]
    result = await RecipeExecutor(cua_tools_by_name=desktop.tools()).read_state()
    assert result.images == []
    assert "SECRET_BASE64" not in repr(result)


async def test_context_budget_is_not_double_consumed():
    budget = RunBudget()
    token = cua_run_budget.set(budget)
    try:
        executor = RecipeExecutor(cua_tools_by_name=Desktop().tools(), budget=budget)
        await executor.launch_app("chrome")
        assert budget.used == 1
    finally:
        cua_run_budget.reset(token)


@pytest.mark.parametrize("expression,value", [
    ("12*(3+4)", "84"), ("15% of 80", "12"), ("2^3", "8"),
    ("50%", "0.5"), ("7%2", "1"), ("-2^2", "-4"), ("1/10+2/10", "0.3"),
])
async def test_safe_arithmetic_supported_operators(expression, value):
    desktop = Desktop()
    desktop.display = value
    result = await execute_recipe(match_local_command(f"calculate {expression}"),
                                  RecipeExecutor(cua_tools_by_name=desktop.tools()))
    assert result["ok"] is True
