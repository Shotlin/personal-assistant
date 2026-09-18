"""WP6: compact same-model planner (master plan WP6 contracts).

- validate_plan accepts ONLY an enumerated recipe id with exactly its
  validated arguments; any extra executable field (e.g. "shell") raises
  InvalidPlan. Truncated/partial output never validates.
- plan_supported_task makes normally ONE model call; recovery is bounded
  (at most one repair attempt, never an open-ended retry loop).
- The compact prompt carries only the current objective and the supported
  recipe descriptions - never full history, tool schemas, or state dumps.
"""

from __future__ import annotations

import pytest

from assistant.runtime.planner import (
    InvalidPlan,
    NeedsClarification,
    UnsupportedTask,
    plan_supported_task,
    validate_plan,
)
from assistant.runtime.router import RecipeRequest
from tests.helpers.scripted_model import ScriptedChatModel


def test_unknown_executable_payload_is_rejected() -> None:
    with pytest.raises(InvalidPlan):
        validate_plan(
            {
                "recipe_id": "open_app.v1",
                "arguments": {"app_id": "chrome"},
                "shell": "unexpected executable payload",
            }
        )


def test_validate_plan_accepts_exact_open_app() -> None:
    request = validate_plan(
        {"recipe_id": "open_app.v1", "arguments": {"app_id": "chrome"}}
    )
    assert request == RecipeRequest(recipe_id="open_app.v1", arguments={"app_id": "chrome"})


def test_validate_plan_rejects_unknown_recipe_id() -> None:
    with pytest.raises(InvalidPlan):
        validate_plan(
            {"recipe_id": "format_disk.v9", "arguments": {"path": "C:"}}
        )


def test_validate_plan_rejects_unsupported_arguments() -> None:
    with pytest.raises(InvalidPlan):
        validate_plan(
            {"recipe_id": "open_app.v1", "arguments": {"app_id": "chrome", "as_root": "1"}}
        )
    with pytest.raises(InvalidPlan):
        validate_plan({"recipe_id": "open_app.v1", "arguments": {"app_id": "unknown-app"}})


def test_validate_plan_rejects_truncated_output() -> None:
    with pytest.raises(InvalidPlan):
        validate_plan('{"recipe_id": "open_app.v1", "arguments": {"app_id": "ch')


def test_validate_plan_rejects_non_dict_payload() -> None:
    with pytest.raises(InvalidPlan):
        validate_plan("open chrome please")


async def test_natural_phrasing_uses_one_model_call() -> None:
    model = ScriptedChatModel(
        responses=[
            __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(
                content='{"recipe_id": "open_app.v1", "arguments": {"app_id": "chrome"}}'
            )
        ]
    )
    result = await plan_supported_task("please launch chrome for me", None, model)
    assert isinstance(result, RecipeRequest)
    assert result.recipe_id == "open_app.v1"
    assert model.call_index == 1, "normal conditions use exactly one planner call"
    # Compact context: only the objective + recipe menu, never history dumps.
    assert len(model.seen) == 1
    texts = [str(m.content) for m in model.seen[0]]
    assert any("please launch chrome for me" in t for t in texts)
    assert any("open_app.v1" in t for t in texts)
    assert not any("conversation" in t.lower() and "history" in t.lower() for t in texts)


async def test_recovery_is_bounded_to_one_repair_attempt() -> None:
    from langchain_core.messages import AIMessage

    model = ScriptedChatModel(
        responses=[
            AIMessage(content='{"recipe_id": "open_app.v1", '),  # truncated
            AIMessage(content='{"recipe_id": "open_app.v1", "arguments": {"app_id": "notes"}}'),
        ]
    )
    result = await plan_supported_task("open notes", None, model)
    assert isinstance(result, RecipeRequest)
    assert result.arguments == {"app_id": "notes"}
    assert model.call_index == 2, "exactly one bounded repair attempt after the first call"


async def test_recovery_exhausted_is_not_open_ended() -> None:
    from langchain_core.messages import AIMessage

    model = ScriptedChatModel(
        responses=[
            AIMessage(content="not json at all"),
            AIMessage(content="still not json"),
        ]
    )
    result = await plan_supported_task("do the thing", None, model)
    assert isinstance(result, UnsupportedTask)
    assert model.call_index == 2, "never a third attempt"


async def test_clarification_short_circuits_recovery() -> None:
    from langchain_core.messages import AIMessage

    model = ScriptedChatModel(
        responses=[
            AIMessage(content='{"decision": "clarification", "question": "Which app?"}')
        ]
    )
    result = await plan_supported_task("open it", None, model)
    assert isinstance(result, NeedsClarification)
    assert "Which app?" in result.question
    assert model.call_index == 1


async def test_unsupported_decision_maps_without_retry() -> None:
    from langchain_core.messages import AIMessage

    model = ScriptedChatModel(
        responses=[AIMessage(content='{"decision": "unsupported"}')]
    )
    result = await plan_supported_task("write me a poem", None, model)
    assert isinstance(result, UnsupportedTask)
    assert model.call_index == 1
