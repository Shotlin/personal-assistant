"""Rendering never turns a dispatch acknowledgement into verified success."""

import pytest
from assistant.runtime.recipe_result import render_result

from assistant.tools.result_normalizer import ToolOutcome


def test_open_success_requires_matching_observation():
    result = {"recipe_id": "open_app.v1", "ok": True, "app_id": "chrome",
              "evidence": ToolOutcome("ok", "confirmed", structured={"foreground_app": "chrome"})}
    assert render_result(result) == "Opened chrome."


@pytest.mark.parametrize("evidence", [None, "", "launched", ToolOutcome("failed", "unverifiable")])
def test_no_success_without_evidence(evidence):
    assert "could not verify" in render_result({
        "recipe_id": "open_app.v1", "ok": True, "app_id": "chrome", "evidence": evidence,
    })


def test_open_failure():
    assert render_result({"recipe_id": "open_app.v1", "ok": False,
                          "app_id": "chrome", "reason": "wrong foreground"}) == (
        "I could not verify that chrome opened: wrong foreground.")


def test_calculator_success_and_failure():
    assert render_result({"recipe_id": "calculator.evaluate.v1", "ok": True,
                          "display_value": "42", "expected_value": 42.0,
                          "evidence": ToolOutcome("ok", "confirmed",
                                                  structured={"display_value": "42"})}) == "= 42"
    assert not render_result({"recipe_id": "calculator.evaluate.v1", "ok": True,
                              "display_value": "42"}).startswith("=")
    assert not render_result({"recipe_id": "calculator.evaluate.v1", "ok": False,
                              "reason": "display mismatch"}).startswith("=")


def test_browser_success_and_unverified_failure():
    result = {"recipe_id": "browser.search.v1", "ok": True, "query": "rust async",
              "evidence": ToolOutcome("ok", "confirmed", structured={
                  "url": "https://www.google.com/search?q=rust+async", "loaded": True})}
    assert render_result(result) == "Searching the web for rust async."
    result["evidence"] = None
    assert "could not verify" in render_result(result)


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        render_result({"recipe_id": "invented.v1", "ok": True})
