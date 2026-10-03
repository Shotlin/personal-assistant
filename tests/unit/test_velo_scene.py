"""Screen understanding against the structure Chrome/YouTube really reports.

Observed live (2026-10-01): a video is an AXHeading with the title plus an
AXLink labelled "<title> <duration>"; only on-screen rows carry a real frame
height; an ad is a link directly before a "Sponsored" label with no heading.
"""

from __future__ import annotations

from assistant.velo import scene
from assistant.velo.parse import parse


def _el(index, role, label, *, y=None, h=22.0, x=100.0):
    element = {"element_index": index, "role": role, "label": label,
               "element_token": f"s:1:{index}"}
    if y is not None:
        element["frame"] = {"x": x, "y": y, "w": 300.0, "h": h}
    return element


def _home_feed() -> dict:
    return {
        "window_title": "(165) YouTube",
        "elements": [
            _el(12, "AXLink", "YouTube Home", y=120, h=40),
            _el(24, "AXLink", "Shorts", y=200, h=40),
            # off-screen virtualised row: ~1pt tall
            _el(76, "AXLink", "The fastest way to clear storage", y=120, h=1),
            _el(78, "AXStaticText", "Sponsored", y=120, h=1),
            # on-screen videos, right to left in the DOM on purpose
            _el(181, "AXHeading", "Robert Greene on focus", y=566, h=44, x=595),
            _el(182, "AXLink", "Robert Greene on focus 6 minutes, 37 seconds", y=566, h=44, x=595),
            _el(171, "AXHeading", "easily one of the best Anime of 2026", y=566, x=140),
            _el(172, "AXLink", "easily one of the best Anime of 2026 9 minutes, 50 seconds",
                y=566, x=140),
            # a visible ad: a link before a Sponsored label, no heading
            _el(240, "AXLink", "Get 2X usage per credit with AI Token Plan", y=566, h=44, x=1007),
            _el(243, "AXStaticText", "Sponsored", y=566, h=20, x=1007),
            # Shorts shelf below the fold: no frame at all
            _el(300, "AXHeading", "Shorts row title"),
            _el(301, "AXLink", "Shorts row title"),
        ],
    }


def test_only_on_screen_videos_in_reading_order_and_ads_flagged() -> None:
    items = scene.extract_items(_home_feed())
    assert [i.title for i in items] == [
        "easily one of the best Anime of 2026",
        "Robert Greene on focus",
        "Get 2X usage per credit with AI Token Plan",
    ]
    assert [i.is_ad for i in items] == [False, False, True]
    assert items[0].token == "s:1:172"


def test_description_is_short_numbered_and_names_the_ad() -> None:
    state = _home_feed()
    text = scene.describe(
        scene.extract_items(state), app="Google Chrome", title=scene.page_title(state)
    )
    assert text.startswith("I see in Google Chrome: 1, easily one")
    assert "3, an advertisement, Get 2X" in text
    assert len(text) < 400


def test_page_title_drops_unread_count_and_site_suffix() -> None:
    assert scene.page_title({"window_title": "(165) YouTube"}) == ""
    assert scene.page_title({"window_title": "(3) Cool video - YouTube - Google Chrome"}) == (
        "Cool video"
    )


def test_a_page_without_videos_falls_back_to_labelled_links() -> None:
    state = {"elements": [
        _el(1, "AXLink", "Read the full documentation here", y=100),
        _el(2, "AXLink", "Home", y=40),
    ]}
    assert [i.title for i in scene.extract_items(state)] == ["Read the full documentation here"]


def test_loose_dictation_still_routes_to_describe_screen() -> None:
    for text in (
        "Right now that skin That screen inside what video Are you see?",
        "Currently explained My screen Inside What videos are you see in That "
        "video's title kindly you say Then i command What video you play?",
        "tell me the titles on my screen",
    ):
        command = parse(text)
        assert command is not None and command.recipe == "describe_screen", text


def test_commands_that_mention_see_are_not_hijacked() -> None:
    assert parse("open chrome and tell me what you see") is None or (
        parse("open chrome and tell me what you see").recipe != "describe_screen"
    )
    command = parse("play the second video")
    assert command is not None and command.recipe == "press_item"


def _create_page() -> dict:
    return {
        "app_name": "Google Chrome",
        "window_title": "(165) YouTube",
        "snapshot_id": "s1",
        "elements": [
            _el(15, "AXComboBox", "Search", y=136, x=434),
            _el(16, "AXButton", "Search", y=128, x=953),
            _el(18, "AXButton", "Create", y=128, x=1241),
            _el(19, "AXButton", "Notifications", y=128, x=1352),
            _el(20, "AXButton", "Create a playlist", y=700, x=100),
            _el(90, "AXButton", "Create", y=1, h=1.0),  # off-screen twin must be ignored
        ],
    }


def test_click_by_name_resolves_the_visible_create_button() -> None:
    element, _ = scene.find_by_label(_create_page(), "the create button")
    assert element is not None and element["element_token"] == "s:1:18"


def test_click_by_name_reports_ambiguity_instead_of_guessing() -> None:
    state = {"elements": [_el(1, "AXButton", "Save draft", y=10), _el(2, "AXButton", "Save all", y=50)]}
    element, alternatives = scene.find_by_label(state, "save")
    assert element is None and alternatives == ["Save draft", "Save all"]


def test_click_by_name_says_so_when_nothing_matches() -> None:
    element, alternatives = scene.find_by_label(_create_page(), "gemini")
    assert element is None and alternatives == []


def test_model_observation_lists_visible_controls_with_tokens_only() -> None:
    text = scene.compact_observation(_create_page())
    assert text is not None
    assert '[s:1:18] AXButton "Create" @1241,128' in text
    assert "s:1:90" not in text and "off-screen" in text
    assert len(text) < 1200


def test_raw_observation_is_kept_when_the_page_reports_no_frames() -> None:
    assert scene.compact_observation({"elements": [{"role": "AXButton", "label": "OK"}]}) is None


def test_click_and_go_to_phrasings_parse_to_click_named() -> None:
    for text, label in (
        ("Click the create button", "create"),
        ("hit generate", "generate"),
        ("go to the upload section", "upload"),
    ):
        command = parse(text)
        assert command is not None and command.recipe == "click_named", text
        assert command.kwargs == {"label": label}
    assert parse("press enter") is None


def test_a_chain_of_local_steps_parses_only_when_every_clause_does() -> None:
    from assistant.velo.parse import parse_chain

    chain = parse_chain(
        "Go to create section then you're gonna click that gemini button then you're "
        "gonna write a girl is walking in the garden then hit generate"
    )
    assert chain is not None
    assert [c.recipe for c in chain] == ["click_named", "click_named", "type_text", "click_named"]
    assert chain[2].kwargs == {"text": "a girl is walking in the garden"}
    assert parse_chain("click create then generate the video and upload it") is None


def _chat_page() -> dict:
    return {"elements": [
        _el(1, "AXTextField", "Address and search bar", y=84),
        _el(2, "AXTextArea", "Message Codex", y=900, h=60),
        _el(3, "AXTextField", "Search chats", y=140),
    ]}


def test_the_input_box_is_the_chat_area_not_the_address_bar() -> None:
    assert scene.find_field(_chat_page())["element_token"] == "s:1:2"
    assert scene.find_field(_chat_page(), "search")["element_token"] == "s:1:3"
    assert scene.find_field({"elements": []}) is None


def test_type_in_the_box_and_submit_parse_to_fill_field() -> None:
    for text, want_text, submit in (
        ("Now you codex Input box inside you type Make a simple business site",
         "Make a simple business site", False),
        ("in the search box write cats and submit", "cats", True),
        ("type hello world in the input box", "hello world", False),
        ("type build me a landing page and press enter", "build me a landing page", True),
    ):
        command = parse(text)
        assert command is not None and command.recipe == "fill_field", text
        assert command.kwargs["text"] == want_text and command.kwargs["submit"] is submit, text
    plain = parse("type hello")
    assert plain is not None and plain.recipe == "type_text"


def test_a_short_answer_resolves_the_pending_question() -> None:
    from assistant.velo.controller import VeloEntry
    from assistant.velo.scene import PENDING

    PENDING.clear()
    PENDING.ask("c", "click_named", ("Save draft", "Save all"))
    command = VeloEntry._answer_to_pending("the second one", "c")
    assert command is not None and command.kwargs == {"label": "Save all"}
    assert VeloEntry._answer_to_pending("the second one", "c") is None  # consumed
    PENDING.ask("c", "click_named", ("Save draft", "Save all"))
    assert VeloEntry._answer_to_pending("open chrome and do a lot of other things please", "c") is None
    assert VeloEntry._answer_to_pending("draft", "c") is None  # cleared by the unrelated command
    PENDING.clear()


def test_an_electron_app_that_only_returned_its_menu_bar_is_not_hydrated_yet() -> None:
    menu_only = {"elements": [
        _el(1, "AXMenuBarItem", "File", y=0, h=33),
        _el(2, "AXMenuBarItem", "Edit", y=0, h=33),
        _el(3, "AXWindow", "ChatGPT", y=33, h=846),
    ]}
    assert scene.looks_unhydrated(menu_only)
    assert not scene.looks_unhydrated(_chat_page())
    assert not scene.looks_unhydrated({"elements": []})


def test_any_screen_is_described_with_boxes_text_and_deduplicated_controls() -> None:
    state = {"window_title": "", "elements": [
        _el(1, "AXMenuBarItem", "File", y=0, h=33),
        _el(2, "AXButton", "New chat", y=100),
        _el(3, "AXButton", "Pin chat", y=140), _el(4, "AXButton", "Pin chat", y=180),
        _el(5, "AXStaticText", "Your workspace is out of credits. Ask the owner.", y=655),
        {**_el(6, "AXTextArea", "Do anything", y=775, h=40), "value": ""},
        _el(7, "AXButton", "Add files and more", y=823),
    ]}
    text = scene.describe_general(state, app="ChatGPT", title="")
    assert text.startswith("This is ChatGPT.")
    assert "Input boxes: Do anything (empty)." in text
    assert "out of credits" in text
    assert "Pin chat (2 times)" in text and "Add files and more" in text
    assert "File" not in text  # menu bar is chrome, not content
    assert len(text) < 600


def test_prompt_dictation_routes_to_compose_and_enter() -> None:
    for text, submit in (
        ("Okay input box inside you prompt made that website making prompt made and paste that",
         False),
        ("write a prompt for a simple SaaS website and type it in the input box and submit",
         True),
    ):
        command = parse(text)
        assert command is not None and command.recipe == "compose_fill", text
        assert command.kwargs["submit"] is submit
    # a plain mention of "prompt" with no write verb stays untouched
    assert parse("open prompt") is None or parse("open prompt").recipe != "compose_fill"


def test_secrets_on_screen_are_never_read_out_or_sent() -> None:
    state = {"elements": [
        _el(1, "AXStaticText", "TWO_FACTOR_API_KEY=3e3703a3-658e-11f1-8f15-0200cd936042 and more", y=100),
        _el(2, "AXStaticText", "A perfectly ordinary sentence about the weather today.", y=140),
        {**_el(3, "AXTextArea", "Note body with password: hunter2 inside it " * 3, y=200),
         "value": "password: hunter2"},
        _el(4, "AXButton", "Save", y=300),
    ]}
    spoken = scene.describe_general(state, app="Notes", title="")
    assert "3e3703a3" not in spoken and "hunter2" not in spoken
    assert "a text area (has text)" in spoken and "weather today" in spoken
    model_view = scene.compact_observation(state)
    assert model_view is not None and "3e3703a3" not in model_view and "hunter2" not in model_view


def test_chatty_dictation_resolves_the_named_button_from_the_screen() -> None:
    state = {"elements": [
        _el(1, "AXButton", "Try Omni now", y=400),
        _el(2, "AXButton", "New project", y=520, x=600),
        _el(3, "AXButton", "Flow Music", y=100),
    ]}
    spoken = "Now you create new project there new project button here that button you click"
    element = scene.find_in_utterance(state, spoken)
    assert element is not None and element["element_token"] == "s:1:2"
    assert scene.find_in_utterance(state, "click the weather button") is None
    command = parse(spoken)
    assert command is not None and command.recipe == "click_in_utterance"


def test_next_previous_and_open_nth_mockup_parse() -> None:
    for text, recipe, kw in (
        ("Next", "step_item", {"direction": "next"}),
        ("okay now next one", "step_item", {"direction": "next"}),
        ("previous", "step_item", {"direction": "previous"}),
        ("open the first mockup", "click_named", {"label": "generated image 1"}),
        ("click the 3rd image", "click_named", {"label": "generated image 3"}),
    ):
        command = parse(text)
        assert command is not None and command.recipe == recipe, text
        assert command.kwargs == kw, text


def test_google_flow_is_a_known_site() -> None:
    command = parse("open google flow in chrome")
    assert command is not None and command.recipe == "navigate"
    assert command.kwargs["destination"] == "flow.google.com"
