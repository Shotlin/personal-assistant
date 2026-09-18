"""Compact same-model planner (master plan WP6).

Natural phrasing of a supported task costs ONE model decision in normal
conditions: a compact prompt (objective + recipe menu only) asks the same
configured model to pick an enumerated recipe and its arguments. The
complete plan must arrive and validate before anything executes --
truncated or partially streamed output is rejected, never executed.

Recovery is bounded: at most one repair attempt after an invalid plan;
anything else maps to :class:`UnsupportedTask` (clarification requests
short-circuit with the model's question). This is deliberately not an
open-ended retry loop.

No GUI semantics here: the planner only produces or refuses a
:class:`RecipeRequest`; execution stays in the recipe executor.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from assistant.runtime.recipe_errors import RecipeFailure
from assistant.runtime.router import APP_IDS, RecipeRequest

logger = logging.getLogger("assistant.runtime.planner")

MAX_PLAN_CHARS = 4000
MAX_REPAIR_ATTEMPTS = 1

#: Compact menu: only what the planner needs to choose (master plan WP6:
#: "only the current objective, relevant preferences, supported recipe
#: descriptions, and targeted state").
RECIPE_MENU = (
    "Supported recipes (choose recipe_id exactly):\n"
    '- open_app.v1: {"app_id": one of ' + ", ".join(sorted(APP_IDS)) + "}\n"
    '- calculator.evaluate.v1: {"expression": "<arithmetic expression>"}\n'
    '- browser.search.v1: {"query": "<text to search>"}\n'
    "Decide JSON ONLY, no prose, in one of these shapes:\n"
    '{"decision": "clarification", "question": "<one question>"}\n'
    '{"decision": "unsupported"}\n'
    '{"recipe_id": "<id>", "arguments": {...}}'
)


class InvalidPlan(ValueError):
    """The model's plan is malformed, unsafe, or not an enumerated recipe."""


@dataclass(frozen=True)
class NeedsClarification:
    """The planner needs one bounded answer before a recipe can run."""

    question: str


@dataclass(frozen=True)
class UnsupportedTask:
    """The objective is outside the enumerated recipe set (agent fallback)."""

    reason: str = "objective does not match a supported local recipe"


def _recipe_arguments(recipe_id: str, arguments: object) -> dict[str, str]:
    """Validate the enumerated recipe and its exact argument shape."""
    if recipe_id == "open_app.v1":
        if not isinstance(arguments, dict) or set(arguments) != {"app_id"}:
            raise InvalidPlan("open_app.v1 requires exactly {'app_id'}")
        app_id = arguments["app_id"]
        if not isinstance(app_id, str) or app_id not in APP_IDS:
            raise InvalidPlan(f"unsupported app_id {app_id!r}")
        return {"app_id": app_id}
    if recipe_id == "calculator.evaluate.v1":
        if not isinstance(arguments, dict) or set(arguments) != {"expression"}:
            raise InvalidPlan("calculator.evaluate.v1 requires exactly {'expression'}")
        expression = arguments["expression"]
        if not isinstance(expression, str) or not expression.strip() or len(expression) > 200:
            raise InvalidPlan("expression must be a nonempty string of at most 200 chars")
        return {"expression": expression}
    if recipe_id == "browser.search.v1":
        if not isinstance(arguments, dict) or set(arguments) != {"query"}:
            raise InvalidPlan("browser.search.v1 requires exactly {'query'}")
        query = arguments["query"]
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise InvalidPlan("query must be a nonempty string of at most 500 chars")
        return {"query": query.strip()}
    raise InvalidPlan(f"unsupported recipe_id {recipe_id!r}")


def _reject_mixed_shape(plan: dict[str, Any]) -> None:
    """Require exactly one decision shape; never a blend (review P2-6)."""
    has_recipe = "recipe_id" in plan
    has_decision = "decision" in plan
    if has_recipe and has_decision:
        raise InvalidPlan("plan mixes 'decision' and 'recipe_id' shapes")
    if has_recipe and ("question" in plan or "reason" in plan):
        raise InvalidPlan("recipe plan must not carry question/reason fields")
    if has_decision and (plan.get("question") is not None or plan.get("reason") is not None):
        if plan.get("decision") == "clarification" and isinstance(plan.get("question"), str):
            return
        raise InvalidPlan("decision plan carries unexpected question/reason fields")


class _DuplicateKeyRejector:
    """JSON object hook that rejects duplicate keys (last-one-wins hides intent)."""

    def __call__(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InvalidPlan(f"duplicate JSON key {key!r} in plan")
            result[key] = value
        return result


def _decode_plan(plan: object) -> dict[str, Any]:
    """Wire-level decode: size first, strict UTF-8, then strict JSON."""
    if isinstance(plan, (str, bytes)):
        if len(plan) > MAX_PLAN_CHARS:
            raise InvalidPlan(f"plan exceeds {MAX_PLAN_CHARS} char wire limit")
        if isinstance(plan, bytes):
            try:
                plan = plan.decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise InvalidPlan("plan is not valid UTF-8") from exc
        try:
            plan = json.loads(plan, object_pairs_hook=_DuplicateKeyRejector())
        except json.JSONDecodeError as exc:
            raise InvalidPlan(f"plan is not valid JSON: {exc.msg}") from exc
    if not isinstance(plan, dict):
        raise InvalidPlan("plan must be a JSON object")
    return plan


def validate_plan(plan: object) -> RecipeRequest:
    """Validate a plan payload into a RecipeRequest; fail closed.

    Accepts ONLY an enumerated recipe id whose arguments match that
    recipe's schema exactly, or one exact menu decision shape. Any extra
    executable field -- ``shell``, ``command``, ``path``, anything
    unrequested -- is rejected before any execution can observe it.
    Truncated JSON cannot validate because it never parses.
    """
    plan = _decode_plan(plan)
    _reject_mixed_shape(plan)
    allowed_top = {"recipe_id", "arguments", "decision", "question", "reason"}
    unknown_top = set(plan) - allowed_top
    if unknown_top:
        raise InvalidPlan(
            f"unknown executable payload in plan: {sorted(unknown_top)!r}"
        )
    decision = plan.get("decision")
    if "decision" in plan:
        # Decision shapes are exact menu entries; stray metadata on a
        # decision plan is invalid, and the decision string must be one
        # of the two enumerated values (arbitrary strings are not a
        # command channel -- they fall back as UnsupportedTask at the
        # plan_supported_task layer, not by validating as a decision).
        if not isinstance(decision, str) or decision not in ("clarification", "unsupported"):
            raise InvalidPlan("decision must be 'clarification' or 'unsupported'")
        expected = {"question"} if decision == "clarification" else set()
        if set(plan) - {"decision"} != expected:
            raise InvalidPlan(f"{decision} plan must carry exactly {sorted(expected) or 'nothing'}")
        if decision == "clarification":
            question = plan["question"]
            if not isinstance(question, str) or not question.strip():
                raise InvalidPlan("clarification requires a question string")
            raise _ClarificationSignal(question.strip())
        raise _UnsupportedSignal(str(decision))
    recipe_id = plan.get("recipe_id")
    if not isinstance(recipe_id, str):
        raise InvalidPlan("plan is missing a string recipe_id")
    return RecipeRequest(
        recipe_id=recipe_id,
        arguments=_recipe_arguments(recipe_id, plan.get("arguments")),
    )


class _ClarificationSignal(Exception):
    def __init__(self, question: str) -> None:
        self.question = question


class _UnsupportedSignal(Exception):
    def __init__(self, decision: str) -> None:
        self.decision = decision


_PLANNER_PROMPT = (
    "You are the compact planner of a desktop assistant. Choose whether the "
    "user's current objective matches one supported local command, or ask ONE "
    "clarifying question, or mark it unsupported. Reply with JSON only.\n"
    "Current objective:\n{objective}\n\n{menu}"
)


async def plan_supported_task(
    text: str,
    context: object,
    model: Any,
) -> RecipeRequest | NeedsClarification | UnsupportedTask:
    """Decide a recipe for ``text`` with normally one same-model call.

    Recovery is bounded: exactly one repair attempt after an invalid plan
    (master plan WP6: bounded recovery, never an open-ended retry loop).
    Truncated output can never execute: it must re-validate as a complete
    plan or the planner gives up to the general agent.
    """
    from langchain_core.messages import HumanMessage

    prompt = _PLANNER_PROMPT.format(objective=text.strip(), menu=RECIPE_MENU)
    messages = [HumanMessage(content=prompt)]
    for attempt in range(1 + MAX_REPAIR_ATTEMPTS):
        response = await model.ainvoke(messages)
        content = getattr(response, "content", "")
        raw = content if isinstance(content, str) else str(content)
        try:
            return validate_plan(raw)
        except _ClarificationSignal as signal:
            return NeedsClarification(question=signal.question)
        except _UnsupportedSignal:
            return UnsupportedTask()
        except InvalidPlan as exc:
            logger.info(
                "planner_plan_invalid",
                extra={"event": "planner_plan_invalid", "attempt": attempt, "reason": str(exc)},
            )
            messages = [
                HumanMessage(
                    content=(
                        f"{prompt}\n\nYour previous reply was invalid ({exc}). "
                        "Send the complete corrected JSON decision now."
                    )
                )
            ]
    return UnsupportedTask(reason="planner output did not validate; falling back")


_ = RecipeFailure  # re-exported for callers typing executor failures alongside
