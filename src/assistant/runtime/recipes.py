"""Three bounded local recipes. All desktop I/O goes through one executor."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import quote_plus

from assistant.runtime.recipe_errors import RecipeFailure
from assistant.runtime.router import APP_IDS, RecipeRequest
from assistant.tools.result_normalizer import ToolOutcome

RecipeResult = dict[str, object]


class RecipeRuntime(Protocol):
    def begin_recipe(self, recipe_id: str) -> None: ...
    async def launch_app(self, app_id: str) -> ToolOutcome: ...
    async def verify_foreground(self, app_id: str) -> ToolOutcome: ...
    async def clear_display(self) -> ToolOutcome: ...
    async def type_text(self, text: str) -> ToolOutcome: ...
    async def read_display(self) -> ToolOutcome: ...
    async def open_url(self, url: str) -> ToolOutcome: ...
    async def read_state(self) -> ToolOutcome: ...


class _Arithmetic:
    """Bounded recursive precedence parser; never eval, shell or Python AST."""

    def __init__(self, expression: str) -> None:
        if not expression or len(expression) > 200:
            raise RecipeFailure("Arithmetic expression is empty or too long")
        self.tokens = re.findall(r"\d+(?:\.\d*)?|\.\d+|of|[+*/^%()-]", expression)
        if "".join(self.tokens) != re.sub(r"\s+", "", expression):
            raise RecipeFailure("Unsupported arithmetic syntax")
        if len(self.tokens) > 80 or "**" in expression or "//" in expression:
            raise RecipeFailure("Unsupported arithmetic syntax")
        self.pos = 0

    def peek(self) -> str:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ""

    def take(self) -> str:
        token = self.peek()
        self.pos += 1
        return token

    def parse(self, minimum: int = 0) -> float:
        token = self.take()
        if token in {"+", "-"}:
            value = self.parse(30) * (-1 if token == "-" else 1)
        elif token == "(":
            value = self.parse()
            if self.take() != ")":
                raise RecipeFailure("Unbalanced parentheses")
        else:
            try:
                value = float(token)
            except ValueError as exc:
                raise RecipeFailure("Expected a number") from exc
        while self.peek() in {"+", "-", "*", "/", "^", "%"}:
            operator = self.peek()
            precedence = {"+": 10, "-": 10, "*": 20, "/": 20, "%": 20, "^": 30}[operator]
            if precedence < minimum:
                break
            self.take()
            if operator == "%" and self.peek() in {"", ")", "+", "-", "*", "/", "of"}:
                value /= 100
                if self.peek() == "of":
                    self.take()
                    value *= self.parse(21)
                continue
            right = self.parse(precedence if operator == "^" else precedence + 1)
            if operator == "^" and (abs(right) > 100 or (value < 0 and not right.is_integer())):
                raise RecipeFailure("Exponent outside local arithmetic limits")
            try:
                if operator == "+":
                    value += right
                elif operator == "-":
                    value -= right
                elif operator == "*":
                    value *= right
                elif operator == "/":
                    value /= right
                elif operator == "%":
                    value %= right
                else:
                    value = math.pow(value, right)
            except (ArithmeticError, ValueError) as exc:
                raise RecipeFailure("Arithmetic is undefined or out of range") from exc
            if not math.isfinite(value) or abs(value) > 1e100:
                raise RecipeFailure("Arithmetic result outside local limits")
        return value


def evaluate_expression(expression: str) -> float:
    """+ - * / ^, binary % remainder, postfix percent, and percent-of."""
    parser = _Arithmetic(expression)
    value = parser.parse()
    if parser.pos != len(parser.tokens) or not math.isfinite(value) or abs(value) > 1e100:
        raise RecipeFailure("Unsupported arithmetic expression")
    return value


def parse_display(value: object) -> float | None:
    """Parse finite display text; commas only in conventional thousands groups."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("−", "-")
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+|\d*\.\d+)(?:\.\d+)?"
                        r"(?:[eE][+-]?\d+)?", text):
        return None
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def foreground_matches(evidence: object, app_id: str) -> bool:
    return (isinstance(evidence, ToolOutcome) and evidence.status == "ok"
            and evidence.structured.get("foreground_app") == app_id
            and not evidence.structured.get("modal", False))


def search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)


def page_matches(evidence: object, query: str) -> bool:
    return (isinstance(evidence, ToolOutcome) and evidence.status == "ok"
            and evidence.structured.get("url") == search_url(query)
            and evidence.structured.get("loaded") is True
            and not evidence.structured.get("modal", False))


def calculator_matches(evidence: object, display: object, expected: object) -> bool:
    observed = parse_display(display)
    return (isinstance(evidence, ToolOutcome) and evidence.status == "ok"
            and evidence.structured.get("display_value", evidence.text) == display
            and observed is not None and isinstance(expected, (int, float))
            and math.isfinite(expected)
            and math.isclose(observed, expected, rel_tol=1e-9, abs_tol=1e-12))


def _acknowledged(outcome: ToolOutcome) -> None:
    if outcome.status != "ok":
        raise RecipeFailure("Native step failed or its effect is unknown")


async def _open(executor: RecipeRuntime, app_id: str) -> ToolOutcome:
    _acknowledged(await executor.launch_app(app_id))
    observation = await executor.verify_foreground(app_id)
    # Only failed observation gets one read-only retry, never another launch.
    if observation.status == "failed" and not observation.structured.get("foreground_app"):
        await asyncio.sleep(0.05)
        observation = await executor.verify_foreground(app_id)
    return observation


async def open_app(executor: RecipeRuntime, arguments: Mapping[str, str]) -> RecipeResult:
    app_id = arguments.get("app_id", "")
    result: RecipeResult = {"recipe_id": "open_app.v1", "app_id": app_id, "ok": False}
    try:
        if app_id not in APP_IDS or set(arguments) != {"app_id"}:
            raise RecipeFailure("Unsupported application arguments")
        observation = await _open(executor, app_id)
        result["evidence"] = observation
        if not foreground_matches(observation, app_id):
            raise RecipeFailure("Requested app was not observed in the foreground")
        result["ok"] = True
    except (RecipeFailure, TimeoutError) as exc:
        result["reason"] = str(exc) or "Observation timed out; not retried"
    return result


async def calculator_evaluate(
    executor: RecipeRuntime, arguments: Mapping[str, str],
) -> RecipeResult:
    result: RecipeResult = {"recipe_id": "calculator.evaluate.v1", "ok": False}
    try:
        if set(arguments) != {"expression"}:
            raise RecipeFailure("Unsupported calculator arguments")
        expression = arguments["expression"]
        expected = evaluate_expression(expression)
        result["expected_value"] = expected
        observation = await _open(executor, "calculator")
        result["evidence"] = observation
        if not foreground_matches(observation, "calculator"):
            raise RecipeFailure("Calculator was not observed in the foreground")
        _acknowledged(await executor.clear_display())
        _acknowledged(await executor.type_text(expression))
        display = await executor.read_display()
        result["evidence"] = display
        result["display_value"] = display.structured.get("display_value", display.text)
        if not calculator_matches(display, result["display_value"], expected):
            raise RecipeFailure("Calculator display mismatch or unreadable result")
        result["ok"] = True
    except (RecipeFailure, TimeoutError) as exc:
        result["reason"] = str(exc) or "Observation timed out; not retried"
    return result


async def browser_search(executor: RecipeRuntime, arguments: Mapping[str, str]) -> RecipeResult:
    query = arguments.get("query", "")
    result: RecipeResult = {"recipe_id": "browser.search.v1", "query": query, "ok": False}
    try:
        if set(arguments) != {"query"} or not query.strip() or len(query) > 500:
            raise RecipeFailure("Unsupported search arguments")
        observation = await executor.read_state()
        _acknowledged(observation)
        if observation.structured.get("modal", False):
            raise RecipeFailure("A modal interrupted the browser workflow")
        if not foreground_matches(observation, "chrome"):
            observation = await _open(executor, "chrome")
        result["evidence"] = observation
        if not foreground_matches(observation, "chrome"):
            raise RecipeFailure("Browser was not observed in the foreground")
        _acknowledged(await executor.open_url(search_url(query)))
        observation = await executor.read_state()
        result["evidence"] = observation
        if not page_matches(observation, query):
            raise RecipeFailure("Search page load could not be verified")
        result["ok"] = True
    except (RecipeFailure, TimeoutError) as exc:
        result["reason"] = str(exc) or "Observation timed out; not retried"
    return result


async def execute_recipe(request: RecipeRequest, runtime: RecipeRuntime) -> RecipeResult:
    """Execute one validated recipe, never a model call or recovery planner."""
    recipes = {"open_app.v1": open_app, "calculator.evaluate.v1": calculator_evaluate,
               "browser.search.v1": browser_search}
    recipe = recipes.get(request.recipe_id)
    if recipe is None:
        raise RecipeFailure(f"Unsupported recipe: {request.recipe_id}")
    runtime.begin_recipe(request.recipe_id)
    return await recipe(runtime, request.arguments)
