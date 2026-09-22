"""Deterministic evidence-supported recipe responses; no model narration."""

from assistant.runtime.recipes import calculator_matches, foreground_matches, page_matches


def render_result(result: dict[str, object]) -> str:
    """Recheck evidence even when a caller supplies ok=True."""
    kind = result.get("recipe_id")
    evidence = result.get("evidence")
    reason = result.get("reason")
    suffix = f": {reason}" if isinstance(reason, str) and reason else ""
    if kind == "open_app.v1":
        app_id = str(result.get("app_id", "the application"))
        if result.get("ok") is True and foreground_matches(evidence, app_id):
            return f"Opened {app_id}."
        return f"I could not verify that {app_id} opened{suffix}."
    if kind == "calculator.evaluate.v1":
        if result.get("ok") is True and calculator_matches(
            evidence,
            result.get("display_value"),
            result.get("expected_value"),
        ):
            return f"= {result['display_value']}"
        return f"I could not verify the calculator result{suffix}."
    if kind == "browser.search.v1":
        query = result.get("query")
        if (
            result.get("ok") is True
            and isinstance(query, str)
            and query
            and page_matches(evidence, query)
        ):
            return f"Searching the web for {query}."
        return f"I could not verify the search page loaded{suffix}."
    raise ValueError(f"Unknown recipe result kind: {kind!r}")
