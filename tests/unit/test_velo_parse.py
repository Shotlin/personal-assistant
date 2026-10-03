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


@pytest.mark.parametrize(
    "text",
    [
        "What are you see right now in screen",
        "what do you see on my screen?",
        "Describe the screen",
        "hey, what can you see",
    ],
)
def test_describe_phrasings_route_to_describe_screen(text: str) -> None:
    command = parse(text)
    assert command is not None and command.recipe == "describe_screen"


@pytest.mark.parametrize(
    ("text", "index"),
    [("Play the second video", 2), ("play 3rd one", 3), ("open the first result", 1)],
)
def test_play_ordinal_routes_to_press_item(text: str, index: int) -> None:
    command = parse(text)
    assert command is not None
    assert command.recipe == "press_item" and command.kwargs == {"index": index}


def test_play_without_an_ordinal_is_not_hijacked() -> None:
    assert parse("play some jazz") is None


@pytest.mark.parametrize(
    ("text", "recipe"),
    [
        ("Now open chrome", "open_app"),
        ("okay, can you scroll down please", "scroll"),
        ("Hey, play the second video", "press_item"),
    ],
)
def test_filler_words_do_not_push_a_command_off_the_fast_path(text: str, recipe: str) -> None:
    command = parse(text)
    assert command is not None and command.recipe == recipe


def test_filler_stripping_keeps_app_names_intact() -> None:
    command = parse("Now open chrome")
    assert command is not None and command.kwargs == {"app_name": "chrome"}


@pytest.mark.parametrize(
    ("text", "dest", "app"),
    [
        ("Now you open youtube in chrome", "youtube.com", "chrome"),
        ("open gmail", "mail.google.com", ""),
        ("go to github on safari", "github.com", "safari"),
    ],
)
def test_open_known_site_navigates_locally(text: str, dest: str, app: str) -> None:
    command = parse(text)
    assert command is not None and command.recipe == "navigate"
    assert command.kwargs["destination"] == dest
    assert command.kwargs.get("app_name", "") == app


def test_open_unknown_name_is_still_an_app_launch() -> None:
    command = parse("open Spotify")
    assert command is not None and command.recipe == "open_app"


def test_compound_requests_are_not_swallowed_as_one_app_or_one_button() -> None:
    assert parse("open youtube and play that channel video now") is None or (
        parse("open youtube and play that channel video now").recipe not in {"open_app", "click_named"}
    )
    command = parse("click the create button and open the menu")
    assert command is None or command.recipe != "click_named"
    assert parse("open Spotify").recipe == "open_app"
