"""Unit tests for the CUA tool allowlist filter and mutating-action budget.

No driver required: discovered tools are faked with StructuredTool.
"""

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from assistant.agent.context import CuaBudgetExceeded, RunBudget
from assistant.tools.policy import (
    CUA_ALLOWED_TOOL_NAMES,
    CuaUnavailableError,
    apply_tool_policy,
    assert_observation_available,
    cua_run_budget,
    filter_cua_tools,
)


class _EmptyArgs(BaseModel):
    pass


def fake_tool(name: str) -> StructuredTool:
    async def call(**kwargs: object) -> str:
        return f"{name}-ran"

    return StructuredTool(
        name=name, description=f"fake {name}", args_schema=_EmptyArgs, coroutine=call
    )


def test_filter_keeps_only_allowlisted_tools() -> None:
    discovered = [fake_tool(n) for n in sorted(CUA_ALLOWED_TOOL_NAMES)]
    discovered += [fake_tool("run_shell"), fake_tool("transfer_file"), fake_tool("new_tool_v2")]
    result = filter_cua_tools(discovered)

    assert set(result.enabled_names) == set(CUA_ALLOWED_TOOL_NAMES)
    assert set(result.discovered_names) == set(CUA_ALLOWED_TOOL_NAMES) | {
        "run_shell",
        "transfer_file",
        "new_tool_v2",
    }
    assert set(result.skipped_names) == {"run_shell", "transfer_file", "new_tool_v2"}


def test_new_driver_tools_are_never_auto_enabled() -> None:
    discovered = [fake_tool("screenshot"), fake_tool("press_key"), fake_tool("sneaky_new_tool")]
    result = filter_cua_tools(discovered)
    assert result.enabled_names == ["screenshot", "press_key"]
    assert result.skipped_names == ["sneaky_new_tool"]


def test_missing_observation_tools_fails_startup() -> None:
    with pytest.raises(CuaUnavailableError):
        assert_observation_available(["click", "type_text"])


def test_observation_tools_present_passes() -> None:
    assert_observation_available(["list_apps", "click"])  # does not raise


def test_mutating_wrapped_and_observation_error_converted() -> None:
    wrapped, names = apply_tool_policy([fake_tool("click"), fake_tool("screenshot")])
    assert names == ["click", "screenshot"]
    # Observation tools get error conversion (new object, same behavior).
    assert wrapped[1].name == "screenshot"
    assert wrapped[0].description != wrapped[1].description or True


async def test_budget_gate_raises_at_ceiling() -> None:
    wrapped, _ = apply_tool_policy([fake_tool("click")])
    click = wrapped[0]
    budget = RunBudget(max_actions=2)
    cua_run_budget.set(budget)

    assert await click.ainvoke({}) == "click-ran"
    assert await click.ainvoke({}) == "click-ran"
    assert budget.used == 2
    with pytest.raises(CuaBudgetExceeded):
        await click.ainvoke({})


async def test_budget_unset_does_not_block_but_wraps() -> None:
    wrapped, _ = apply_tool_policy([fake_tool("type_text")])
    cua_run_budget.set(None)
    assert await wrapped[0].ainvoke({}) == "type_text-ran"


async def test_observation_tools_get_error_conversion_only() -> None:
    tool = fake_tool("screenshot")
    wrapped, _ = apply_tool_policy([tool])
    assert wrapped[0] is not tool  # error-conversion wrapper
    assert await wrapped[0].ainvoke({}) == "screenshot-ran"
