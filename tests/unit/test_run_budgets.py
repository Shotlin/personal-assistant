"""WP6: budget enforcement for the compact-planner route (master plan).

Cumulative accounting: the planner route, a recipe executed through it,
and a general-agent fallback within one run must draw against the SAME
per-run budget. The planner itself makes no desktop mutations; recipe
mutations count exactly as they do on the exact-match route.
"""

from __future__ import annotations

import pytest

from assistant.agent.context import CuaBudgetExceeded, RunBudget
from assistant.runtime.planner import plan_supported_task, validate_plan
from assistant.runtime.router import RecipeRequest
from tests.helpers.scripted_model import ScriptedChatModel


def test_budget_blocks_at_ceiling_with_tool_name() -> None:
    budget = RunBudget(max_actions=2)
    budget.consume("click")
    budget.consume("launch_app")
    with pytest.raises(CuaBudgetExceeded) as excinfo:
        budget.consume("type_text")
    assert "type_text" in str(excinfo.value)


def test_budget_remaining_never_negative() -> None:
    budget = RunBudget(max_actions=1)
    budget.consume("click")
    assert budget.remaining == 0
    with pytest.raises(CuaBudgetExceeded):
        budget.consume("click")
    assert budget.remaining == 0 and budget.used == 2


def test_planner_budget_share_is_zero_without_desktop_work() -> None:
    """A plan that never dispatches a mutation consumes no budget."""
    budget = RunBudget()
    before = budget.used
    _ = validate_plan({"recipe_id": "open_app.v1", "arguments": {"app_id": "chrome"}})
    assert budget.used == before


async def test_planner_call_does_not_consume_action_budget() -> None:
    """The model decision is not a desktop mutation (accounting 11.3)."""
    from langchain_core.messages import AIMessage

    budget = RunBudget(max_actions=3)
    model = ScriptedChatModel(
        responses=[
            AIMessage(content='{"recipe_id": "open_app.v1", "arguments": {"app_id": "mail"}}')
        ]
    )
    result = await plan_supported_task("open mail", None, model)
    assert isinstance(result, RecipeRequest)
    assert budget.used == 0, "planner call must not consume the mutation budget"
