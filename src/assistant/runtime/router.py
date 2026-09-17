"""Strict Phase-1 scope: exact local commands only; WP6 extends coverage via model."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

APP_IDS = frozenset({"chrome", "safari", "terminal", "calculator", "notes", "finder", "mail"})


@dataclass(frozen=True)
class RecipeRequest:
    recipe_id: str
    arguments: dict[str, str]


def match_local_command(
    text: str, approved_context: Mapping[str, object] | None = None,
) -> RecipeRequest | None:
    """Match only a complete, unambiguous command; context never widens scope.

    A single balanced pair of quotes around an entire search query is data,
    not executable syntax. All other quoting and clause markers fall back.
    """
    normalized = " ".join(text.strip().split()).rstrip(".!?").strip()
    if not normalized or len(normalized) > 500:
        return None
    if re.search(r"[,;`\n\r]|\b(?:and|then|never|not|don't|do\s+not)\b", text, re.I):
        return None
    lower = normalized.lower()
    app = re.fullmatch(r"open ([a-z]+)", lower)
    if app and app[1] in APP_IDS:
        return RecipeRequest("open_app.v1", {"app_id": app[1]})
    arithmetic = re.fullmatch(r"(?:what is|calculate|compute) (.+)", lower)
    if arithmetic:
        from assistant.runtime.recipe_errors import RecipeFailure
        from assistant.runtime.recipes import evaluate_expression

        try:
            evaluate_expression(arithmetic[1])
        except RecipeFailure:
            return None
        return RecipeRequest("calculator.evaluate.v1", {"expression": arithmetic[1]})
    search = re.fullmatch(r"search(?: for)? (.+)", normalized, re.I)
    if search:
        query = search[1]
        if query.lower() == "for":
            return None
        if query.startswith(('"', "'")) and query.endswith(query[0]):
            query = query[1:-1].strip()
        if not query or any(char in query for char in "\"'`"):
            return None
        return RecipeRequest("browser.search.v1", {"query": query})
    return None
