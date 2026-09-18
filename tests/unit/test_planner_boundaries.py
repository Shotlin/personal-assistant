"""Strict compact-planner wire boundaries and bounded repair."""

import json

import pytest
from langchain_core.messages import AIMessage

from assistant.runtime.planner import (
    MAX_PLAN_CHARS,
    InvalidPlan,
    UnsupportedTask,
    plan_supported_task,
    validate_plan,
)
from assistant.runtime.router import RecipeRequest
from tests.helpers.scripted_model import ScriptedChatModel

VALID = '{"recipe_id":"open_app.v1","arguments":{"app_id":"chrome"}}'


@pytest.mark.parametrize("as_bytes", [False, True])
def test_size_checked_before_json_decode(as_bytes: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = VALID + " " * (MAX_PLAN_CHARS + 1 - len(VALID))

    def must_not_decode(*args: object, **kwargs: object) -> None:
        pytest.fail("oversized reply reached JSON decoder")

    monkeypatch.setattr("assistant.runtime.planner.json.loads", must_not_decode)
    with pytest.raises(InvalidPlan, match="exceeds"):
        validate_plan(raw.encode() if as_bytes else raw)


def test_exact_size_limit_is_accepted() -> None:
    raw = VALID + " " * (MAX_PLAN_CHARS - len(VALID))
    assert validate_plan(raw).arguments == {"app_id": "chrome"}


@pytest.mark.parametrize("payload", [
    {"decision": "clarification", "question": "Which?", "reason": "extra"},
    {"decision": "clarification", "question": "Which?", "arguments": {}},
    {"decision": "unsupported", "arguments": {}},
    {"decision": "unsupported", "question": None},
    {"decision": "unsupported", "reason": None},
    {"decision": True},
    {"decision": 42},
    {"decision": "something-else"},
])
def test_decisions_require_exact_menu_shapes(payload: dict[str, object]) -> None:
    with pytest.raises(InvalidPlan):
        validate_plan(json.dumps(payload))


@pytest.mark.parametrize("raw", [
    b"\xff",
    "[" * 1500 + "]" * 1500,
    '{"recipe_id":"open_app.v1","recipe_id":"open_app.v1","arguments":{"app_id":"chrome"}}',
    '{"recipe_id":"open_app.v1","arguments":{"app_id":"terminal","app_id":"chrome"}}',
])
def test_invalid_wire_payloads_raise_domain_error(raw: str | bytes) -> None:
    with pytest.raises(InvalidPlan):
        validate_plan(raw)


@pytest.mark.parametrize("raw", [
    VALID + " " * MAX_PLAN_CHARS,
    '{"decision":"clarification","question":"Which?","arguments":{}}',
    '{"recipe_id":"open_app.v1","arguments":{"app_id":"terminal","app_id":"chrome"}}',
    "[" * 1500 + "]" * 1500,
])
async def test_bad_wire_payload_repairs_once_then_falls_back(raw: str) -> None:
    model = ScriptedChatModel(responses=[AIMessage(content=raw)])
    result = await plan_supported_task("launch chrome please", None, model)
    assert isinstance(result, UnsupportedTask)
    assert model.call_index == 2


async def test_oversized_first_reply_can_be_repaired() -> None:
    model = ScriptedChatModel(responses=[
        AIMessage(content=VALID + " " * MAX_PLAN_CHARS), AIMessage(content=VALID),
    ])
    result = await plan_supported_task("launch chrome please", None, model)
    assert isinstance(result, RecipeRequest)
    assert result.arguments == {"app_id": "chrome"}
    assert model.call_index == 2
