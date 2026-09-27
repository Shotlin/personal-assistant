"""Route A parsing: narrow shapes, exact payloads, honest deferral."""

from __future__ import annotations

import pytest

from assistant.velo.parse import parse


@pytest.mark.parametrize(
    ("text", "recipe"),
    [
        ("Open Safari", "open_app"),
        ("open chrome.", "open_app"),
        ("launch TextEdit", "open_app"),
        ("open youtube.com", "navigate"),
        ("go to docs.python.org", "navigate"),
        ("scroll down", "scroll"),
        ("scroll up 5", "scroll"),
        ("press the third link", "press_ordinal"),
        ("click the 2nd button", "press_ordinal"),
        ("type hello world", "type_text"),
        ("search youtube for jazz", "search_browser"),
        ("search for jazz", "search_browser"),
        ("look up coltrane", "search_browser"),
        ("use Safari instead", "open_app"),
        ("switch to Chrome instead", "open_app"),
    ],
)
def test_imperatives_parse_to_their_recipes(text: str, recipe: str) -> None:
    command = parse(text)
    assert command is not None
    assert command.recipe == recipe


def test_an_explicit_name_is_the_requested_app() -> None:
    command = parse("open Safari")
    assert command is not None
    assert command.requested_app == "Safari"


def test_negation_names_the_wanted_app_not_the_excluded_one() -> None:
    command = parse("open Safari, not Chrome")
    assert command is not None
    assert command.requested_app == "Safari"


def test_a_correction_is_an_open_of_the_new_target() -> None:
    command = parse("Use Safari instead")
    assert command is not None
    assert command.recipe == "open_app"
    assert command.kwargs["app_name"] == "Safari"


def test_search_youtube_carries_the_query_byte_exact() -> None:
    command = parse("search YouTube for John Coltrane")
    assert command is not None
    assert command.kwargs["site"] == "youtube"
    assert command.kwargs["query"] == "John Coltrane"


def test_an_unscoped_search_has_no_site() -> None:
    command = parse("search for jazz records")
    assert command is not None
    assert command.kwargs["query"] == "jazz records"
    assert command.kwargs["site"] == ""


def test_ordinals_map_to_indexes() -> None:
    command = parse("press the third link")
    assert command is not None
    assert command.kwargs == {"kind": "link", "index": 3}


def test_scroll_carries_direction_and_amount() -> None:
    command = parse("scroll down 5")
    assert command is not None
    assert command.kwargs == {"direction": "down", "amount": 5}


@pytest.mark.parametrize(
    "text",
    [
        "What is 2 + 2?",
        "Open Chrome and search for music",
        "Open Safari, then search YouTube",
        "what's the weather",
        "",
        "tell me a story about computers please",
    ],
)
def test_everything_else_defers_to_the_loop(text: str) -> None:
    assert parse(text) is None
