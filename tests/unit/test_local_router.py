"""WP5 strict local routing: uncertain intent always falls back."""

import pytest

from assistant.runtime.router import match_local_command


def test_exact_known_command_selects_recipe():
    request = match_local_command("Open Chrome", approved_context={})
    assert request is not None
    assert request.recipe_id == "open_app.v1"
    assert request.arguments == {"app_id": "chrome"}


def test_negation_is_not_turned_into_an_action():
    assert match_local_command("Do not open Chrome", approved_context={}) is None


def test_unknown_project_or_side_effect_falls_back():
    text = "Open Terminal, delete the project, then open Chrome"
    assert match_local_command(text, approved_context={}) is None


@pytest.mark.parametrize("text", [" OPEN   Chrome!! ", "open chrome.", "Open Chrome?"])
def test_normalization(text):
    assert match_local_command(text) == match_local_command("Open Chrome", None)


@pytest.mark.parametrize("app", list(
    "chrome,safari,terminal,calculator,notes,finder,mail".split(",")))
def test_only_known_apps(app):
    assert match_local_command(f"open {app}").arguments == {"app_id": app}


@pytest.mark.parametrize("text", [
    "calculate 12*(3+4)", "what is 15% of 80", "compute 15% of 80", "what is 6*7",
    "calculate -2^2", "calculate (8+2)/5", "calculate 50%", "calculate 7%2",
])
def test_arithmetic_matches(text):
    assert match_local_command(text).recipe_id == "calculator.evaluate.v1"


@pytest.mark.parametrize("text", ["search for rust async", 'search for "rust async"'])
def test_search_matches(text):
    request = match_local_command(text)
    assert request.recipe_id == "browser.search.v1"
    assert request.arguments == {"query": "rust async"}


@pytest.mark.parametrize("text", [
    "", "  ", "open unknown", "open Chrome and delete files", "don't open Chrome",
    "never open Chrome", "do not search for rust", "search", "search for", "search for   ",
    "open `Chrome`", 'open "Chrome"', "open Chrome; open Mail", "search rust then open mail",
    "search rust, delete files", "calculate __import__('os')", "what is 1/0",
    "calculate 2**100000", "calculate 2^999999", "calculate 1+", "calculate 1;2",
    "search for rust and delete files", "search for `rust`", 'search for "rust" extra',
])
def test_doubt_falls_back(text):
    assert match_local_command(text, approved_context=None) is None
