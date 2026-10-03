"""Recipes: verified execution with explicit target ownership."""

from __future__ import annotations

import pytest

from assistant.velo.adapter import CuaAdapter
from assistant.velo.contracts import (
    AppIdentity,
    OutcomeState,
    PostconditionKind,
    Target,
    TaskState,
)
from assistant.velo.recipes import execute

from tests.unit.velo_fakes import (
    EventLog,
    app_entry,
    reset_world,
    standard_tools,
    window_state_payload,
)


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    yield
    reset_world()


def _task(**overrides) -> TaskState:
    task = TaskState(instruction="test")
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


def _adapter() -> tuple[CuaAdapter, dict, EventLog]:
    tools = standard_tools()
    log = EventLog()

    async def on_event(kind, data):
        await log(kind, data)

    return CuaAdapter(tools, on_event=on_event), tools, log


def _safari_state(pid: int = 9, window_id: int = 2, **elements) -> dict:
    base = [
        {"role": "AXTextField", "label": "address", "value": "", "element_token": "addr-1",
         "focused": False},
    ]
    base.extend(elements.pop("extra", []))
    return window_state_payload(base, pid=pid, window_id=window_id)


async def test_open_app_launches_the_requested_app_and_confirms() -> None:
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t",
        {"apps": [app_entry("Google Chrome", pid=7, running=True),
                  app_entry("Safari", pid=None, running=False,
                            bundle_id="com.apple.Safari")]},
    )

    def launch(kwargs):
        # After launch, the driver sees Safari running.
        tools["list_apps"]._respond = lambda k: (
            "t",
            {"apps": [app_entry("Safari", pid=12, running=True)]},
        )
        tools["list_windows"]._respond = lambda k: (
            "t",
            {"windows": [{"window_id": 2, "is_on_screen": True,
                          "bounds": {"height": 600, "width": 800}}]},
        )
        tools["get_window_state"]._respond = lambda k: ("t", _safari_state(pid=12))
        return "Launched.", {"pid": 12, "bundle_id": "com.apple.Safari"}

    tools["launch_app"]._respond = launch
    task = _task()
    result = await execute("open_app", task, adapter, app_name="Safari")
    assert result.state is OutcomeState.CONFIRMED
    assert "Safari" in result.answer
    launch_calls = tools["launch_app"].calls
    assert launch_calls and launch_calls[0]["bundle_id"] == "com.apple.Safari"


async def test_open_safari_never_fronts_running_chrome() -> None:
    """The deterministic defect, regression-tested at the recipe level."""
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t",
        {"apps": [app_entry("Google Chrome", pid=7, running=True),
                  app_entry("Safari", pid=9, running=False,
                            bundle_id="com.apple.Safari")]},
    )

    def launch(kwargs):
        assert kwargs.get("bundle_id") == "com.apple.Safari", (
            "Safari must be launched, not substituted with Chrome"
        )
        tools["list_apps"]._respond = lambda k: (
            "t",
            {"apps": [app_entry("Google Chrome", pid=7, running=True),
                      app_entry("Safari", pid=12, running=True)]},
        )
        tools["list_windows"]._respond = lambda k: (
            "t",
            {"windows": [{"window_id": 2, "is_on_screen": True,
                          "bounds": {"height": 600, "width": 800}}]},
        )
        tools["get_window_state"]._respond = lambda k: ("t", _safari_state(pid=12))
        return "Launched.", {"pid": 12, "bundle_id": "com.apple.Safari"}

    tools["launch_app"]._respond = launch
    task = _task()
    result = await execute("open_app", task, adapter, app_name="Safari")
    assert result.state is OutcomeState.CONFIRMED
    # Chrome's pid was never the object of any action.
    assert all(call.get("pid") != 7 for call in tools["bring_to_front"].calls)


async def test_a_running_requested_app_is_brought_to_front() -> None:
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Safari", pid=9, running=True)]},
    )
    tools["list_windows"]._respond = lambda kwargs: (
        "t",
        {"windows": [{"window_id": 2, "is_on_screen": True,
                      "bounds": {"height": 600, "width": 800}}]},
    )
    tools["get_window_state"]._respond = lambda kwargs: ("t", _safari_state())
    task = _task()
    result = await execute("open_app", task, adapter, app_name="Safari")
    assert result.state is OutcomeState.CONFIRMED
    assert result.answer.startswith("Brought")
    assert tools["launch_app"].calls == []
    assert tools["bring_to_front"].calls == [{"pid": 9}]


async def test_an_unknown_app_is_answered_not_guessed() -> None:
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Finder", pid=1)]},
    )
    task = _task()
    result = await execute("open_app", task, adapter, app_name="Nonexistent")
    assert result.state is OutcomeState.NO_EFFECT
    assert "couldn't find" in result.answer
    assert tools["launch_app"].calls == []


async def test_navigate_sets_the_value_of_the_focused_address_field() -> None:
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Safari", pid=9, running=True)]},
    )
    tools["list_windows"]._respond = lambda kwargs: (
        "t",
        {"windows": [{"window_id": 2, "is_on_screen": True,
                      "bounds": {"height": 600, "width": 800}}]},
    )
    state = _safari_state(
        extra=[{"role": "AXTextField", "label": "address", "value": "",
                "element_token": "addr-1", "focused": True}],
    )

    def get_state(kwargs):
        # After Return was pressed, the page reports the destination.
        if tools["press_key"].calls:
            state["elements"][0]["value"] = "https://example.com"
            state["elements"].append(
                {"role": "AXStaticText", "label": "Example Domain"}
            )
        return "t", dict(state)

    tools["get_window_state"]._respond = get_state
    task = _task()
    result = await execute("navigate", task, adapter, destination="example.com")
    assert result.state is OutcomeState.CONFIRMED
    set_calls = tools["set_value"].calls
    assert set_calls and set_calls[0]["value"] == "https://example.com"
    assert set_calls[0]["element_token"] == "addr-1"
    assert set_calls[0]["pid"] == 9 and set_calls[0]["window_id"] == 2


async def test_navigate_falls_back_to_typing_when_no_field_is_found() -> None:
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Safari", pid=9, running=True)]},
    )
    tools["list_windows"]._respond = lambda kwargs: (
        "t",
        {"windows": [{"window_id": 2, "is_on_screen": True,
                      "bounds": {"height": 600, "width": 800}}]},
    )

    def get_state(kwargs):
        if tools["type_text"].calls:
            return "t", window_state_payload(
                [{"role": "AXStaticText", "label": "https://example.com"}],
                pid=9, window_id=2,
            )
        return "t", window_state_payload([], pid=9, window_id=2)

    tools["get_window_state"]._respond = get_state
    task = _task()
    result = await execute("navigate", task, adapter, destination="example.com")
    assert result.state is OutcomeState.CONFIRMED
    typed = tools["type_text"].calls[0]
    assert typed["text"] == "https://example.com"
    assert typed["pid"] == 9


async def test_navigate_reports_honestly_when_navigation_cannot_be_confirmed() -> None:
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Safari", pid=9, running=True)]},
    )
    tools["list_windows"]._respond = lambda kwargs: (
        "t",
        {"windows": [{"window_id": 2, "is_on_screen": True,
                      "bounds": {"height": 600, "width": 800}}]},
    )
    tools["get_window_state"]._respond = lambda kwargs: (
        "t", window_state_payload([], pid=9, window_id=2),
    )
    task = _task()
    result = await execute("navigate", task, adapter, destination="example.com")
    assert result.state is OutcomeState.UNKNOWN
    assert "could not confirm" in result.answer


async def test_search_youtube_navigates_to_the_sites_own_search() -> None:
    """Site-scoped search is deterministic; the query is never web-scattered."""
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Google Chrome", pid=7, running=True)]},
    )
    tools["list_windows"]._respond = lambda kwargs: (
        "t",
        {"windows": [{"window_id": 1, "is_on_screen": True,
                      "bounds": {"height": 600, "width": 800}}]},
    )

    def get_state(kwargs):
        if tools["press_key"].calls:
            return "t", window_state_payload(
                [
                    {"role": "AXTextField", "label": "search", "value":
                        "https://www.youtube.com/results?search_query=jazz",
                     "element_token": "s1"},
                    {"role": "AXStaticText", "label": "jazz - YouTube"},
                ],
                pid=7, window_id=1,
            )
        return "t", window_state_payload([], pid=7, window_id=1)

    tools["get_window_state"]._respond = get_state
    task = _task()
    result = await execute("search_browser", task, adapter, query="jazz", site="youtube")
    assert result.state is OutcomeState.CONFIRMED
    # No field was observable before typing, so the exact search URL went
    # through the type fallback and Return submitted it.
    assert tools["set_value"].calls == []
    typed = tools["type_text"].calls[0]
    assert "youtube.com/results?search_query=jazz" in typed["text"]
    assert tools["press_key"].calls and tools["press_key"].calls[0]["key"] == "Return"


async def test_scroll_reports_no_effect_when_the_view_did_not_change() -> None:
    adapter, tools, _log = _adapter()
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["list"] = [app_entry("Preview", pid=5, running=True)]
    task = _task()
    task.resolved = Target(
        app=AppIdentity(name="Preview", pid=5, running=True), window_id=3
    )
    state = window_state_payload(
        [{"role": "AXStaticText", "label": "page text"}], pid=5, window_id=3
    )
    tools["get_window_state"]._respond = lambda kwargs: ("t", dict(state))
    result = await execute("scroll", task, adapter, direction="down", amount=3)
    assert result.state is OutcomeState.NO_EFFECT
    assert "did not change" in result.answer


async def test_scroll_confirms_when_the_viewport_changed() -> None:
    adapter, tools, _log = _adapter()
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["list"] = [app_entry("Preview", pid=5, running=True)]
    task = _task()
    task.resolved = Target(
        app=AppIdentity(name="Preview", pid=5, running=True), window_id=3
    )
    calls = {"n": 0}

    def get_state(kwargs):
        calls["n"] += 1
        label = "top text" if calls["n"] == 1 else "bottom text"
        return "t", window_state_payload(
            [{"role": "AXStaticText", "label": label}], pid=5, window_id=3
        )

    tools["get_window_state"]._respond = get_state
    result = await execute("scroll", task, adapter, direction="down", amount=3)
    assert result.state is OutcomeState.CONFIRMED


async def test_type_text_sets_the_value_of_a_focused_field() -> None:
    adapter, tools, _log = _adapter()
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["list"] = [app_entry("Notes", pid=4, running=True)]
    task = _task()
    task.resolved = Target(
        app=AppIdentity(name="Notes", pid=4, running=True), window_id=1
    )
    state = {
        "elements": [
            {"role": "AXTextArea", "label": "composer", "value": "",
             "element_token": "field-1", "focused": True},
        ]
    }

    def get_state(kwargs):
        if tools["set_value"].calls:
            state["elements"][0]["value"] = "hello there"
        return "t", dict(state)

    tools["get_window_state"]._respond = get_state
    result = await execute("type_text", task, adapter, text="hello there")
    assert result.state is OutcomeState.CONFIRMED
    assert tools["set_value"].calls[0]["value"] == "hello there"


async def test_press_ordinal_counts_honestly() -> None:
    adapter, tools, _log = _adapter()
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["list"] = [app_entry("Safari", pid=9, running=True)]
    task = _task()
    task.resolved = Target(
        app=AppIdentity(name="Safari", pid=9, running=True), window_id=2
    )
    tools["get_window_state"]._respond = lambda kwargs: (
        "t",
        window_state_payload(
            [{"role": "AXLink", "label": "only link", "element_token": "l-1"}],
            pid=9, window_id=2,
        ),
    )
    result = await execute("press_ordinal", task, adapter, kind="link", index=3)
    assert result.state is OutcomeState.NO_EFFECT
    assert "no third one" in result.answer


async def test_press_ordinal_clicks_the_chosen_token_and_confirms_change() -> None:
    adapter, tools, _log = _adapter()
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["list"] = [app_entry("Safari", pid=9, running=True)]
    task = _task()
    task.resolved = Target(
        app=AppIdentity(name="Safari", pid=9, running=True), window_id=2
    )
    calls = {"n": 0}

    def get_state(kwargs):
        calls["n"] += 1
        label = "before" if calls["n"] == 1 else "after"
        return "t", window_state_payload(
            [
                {"role": "AXLink", "label": "first", "element_token": "l-1"},
                {"role": "AXStaticText", "label": label},
            ],
            pid=9, window_id=2,
        )

    tools["get_window_state"]._respond = get_state
    result = await execute("press_ordinal", task, adapter, kind="link", index=1)
    assert result.state is OutcomeState.CONFIRMED
    assert tools["click"].calls[0]["element_token"] == "l-1"


async def test_an_action_that_blows_the_task_budget_stops_the_recipe() -> None:
    """The budget is enforced before the first dispatch; the controller reports it."""
    from assistant.velo.contracts import TaskCancelled

    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Safari", pid=9, running=False,
                                 bundle_id="com.apple.Safari")]},
    )
    task = _task(max_actions=1)
    task.used_actions = 1
    with pytest.raises(TaskCancelled, match="budget"):
        await execute("open_app", task, adapter, app_name="Safari")


def _yt_results(order: list[str], extra: list[dict] | None = None) -> dict:
    labels = {
        "vegeta": "Vegeta is jealous of Hit by DB Clips 1.2M views 2 days ago 59 seconds",
        "addiction": "Addiction is very real by Mind Talks 165 views 1 hour ago 4 minutes",
        "ad": "Sponsored Learn Python fast by CodeCo 3 minutes",
        "agentic": "Agentic AI Class 4 by AI School 9K views 3 days ago 1 hour, 2 minutes",
    }
    elements = [{"role": "AXLink", "label": "Home", "element_token": "nav-1"}]
    for key in order:
        # YouTube exposes a thumbnail link AND a title link per video.
        elements.append({"role": "AXLink", "label": labels[key], "element_token": f"t-{key}"})
        elements.append({"role": "AXLink", "label": labels[key], "element_token": f"x-{key}"})
    elements.extend(extra or [])
    return window_state_payload(elements, pid=9, window_id=2)


def _chrome_task() -> TaskState:
    from assistant.velo.scene import SCENES

    SCENES.clear()
    task = _task(conversation="conv-1")
    task.resolved = Target(app=AppIdentity(name="Google Chrome", pid=9, running=True), window_id=2)
    return task


async def test_describe_screen_numbers_items_and_flags_the_ad() -> None:
    from tests.unit.velo_fakes import SHARED_APPS

    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Google Chrome", pid=9, running=True)]
    tools["get_window_state"]._respond = lambda k: ("t", _yt_results(["addiction", "vegeta", "ad"]))
    task = _chrome_task()
    result = await execute("describe_screen", task, adapter)
    assert result.state is OutcomeState.CONFIRMED
    assert "1, Addiction is very real, by Mind Talks" in result.answer
    assert "2, Vegeta is jealous of Hit" in result.answer
    assert "3, an advertisement" in result.answer


async def test_play_second_clicks_the_second_video_not_the_first() -> None:
    from tests.unit.velo_fakes import SHARED_APPS

    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Google Chrome", pid=9, running=True)]
    tools["get_window_state"]._respond = lambda k: ("t", _yt_results(["addiction", "vegeta", "ad"]))
    task = _chrome_task()
    await execute("describe_screen", task, adapter)

    # The page reshuffles before the user speaks; "second" is still what they heard.
    shown = {"order": ["vegeta", "addiction", "ad"]}

    def state(k):
        if tools["click"].calls:
            return "t", window_state_payload(
                [{"role": "AXStaticText", "label": "Vegeta is jealous of Hit - YouTube"}],
                pid=9, window_id=2,
            )
        return "t", _yt_results(shown["order"])

    tools["get_window_state"]._respond = state
    result = await execute("press_item", task, adapter, index=2)
    assert result.state is OutcomeState.CONFIRMED
    assert tools["click"].calls[0]["element_token"] == "t-vegeta"


async def test_play_item_refuses_an_advertisement() -> None:
    from tests.unit.velo_fakes import SHARED_APPS

    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Google Chrome", pid=9, running=True)]
    tools["get_window_state"]._respond = lambda k: ("t", _yt_results(["addiction", "vegeta", "ad"]))
    task = _chrome_task()
    await execute("describe_screen", task, adapter)
    result = await execute("press_item", task, adapter, index=3)
    assert result.state is OutcomeState.NO_EFFECT
    assert "advertisement" in result.answer
    assert not tools["click"].calls


async def test_fill_field_clicks_the_chat_box_types_and_submits() -> None:
    from tests.unit.velo_fakes import SHARED_APPS

    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Codex", pid=9, running=True)]
    task = _chrome_task()

    def state(k):
        submitted = bool(tools["press_key"].calls)
        elements = [
            {"role": "AXTextField", "label": "Address and search bar", "element_token": "addr",
             "frame": {"x": 1, "y": 80, "w": 400, "h": 30}},
            {"role": "AXTextArea", "label": "Message Codex", "element_token": "box",
             "value": "make a site" if tools["set_value"].calls else "",
             "frame": {"x": 1, "y": 900, "w": 800, "h": 60}},
        ]
        if submitted:
            elements.append({"role": "AXStaticText", "label": "Working on it",
                             "frame": {"x": 1, "y": 500, "w": 100, "h": 20}})
        return "t", window_state_payload(elements, pid=9, window_id=2)

    tools["get_window_state"]._respond = state
    result = await execute("fill_field", task, adapter, text="make a site", submit=True)
    assert result.state is OutcomeState.CONFIRMED
    assert tools["click"].calls[0]["element_token"] == "box"
    assert tools["set_value"].calls[0]["value"] == "make a site"
    assert tools["press_key"].calls[0]["key"] == "Return"


async def test_fill_field_without_a_box_asks_instead_of_apologising() -> None:
    from tests.unit.velo_fakes import SHARED_APPS

    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Codex", pid=9, running=True)]
    task = _chrome_task()
    tools["get_window_state"]._respond = lambda k: (
        "t", window_state_payload([{"role": "AXButton", "label": "Go", "element_token": "g",
                                    "frame": {"x": 1, "y": 1, "w": 20, "h": 20}}],
                                   pid=9, window_id=2))
    result = await execute("fill_field", task, adapter, text="hi")
    assert result.state is OutcomeState.NO_EFFECT
    assert "Which box do you mean" in result.answer


async def test_download_images_opens_viewer_takes_the_series_item_and_sees_files_land(
    tmp_path, monkeypatch
) -> None:
    from assistant.velo import recipes
    from tests.unit.velo_fakes import SHARED_APPS

    monkeypatch.setattr(recipes, "_downloads_dir", lambda: tmp_path)
    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Google Chrome", pid=9, running=True)]
    task = _chrome_task()
    ui = {"stage": "page"}

    def frame(y=200):
        return {"x": 100, "y": y, "w": 200, "h": 40}

    def state(k):
        els = [{"role": "AXStaticText", "label": "Worked for 1m 46s here", "frame": frame(50)}]
        if ui["stage"] == "page":
            els.append({"role": "AXButton", "label": "Generated image 1",
                        "element_token": "img1", "frame": frame()})
        else:
            els.append({"role": "AXPopUpButton", "label": "Download",
                        "element_token": "dl", "frame": frame(100)})
            els.append({"role": "AXButton", "label": "Close viewer",
                        "element_token": "close", "frame": frame(100)})
        if ui["stage"] == "menu":
            els.append({"role": "AXMenuItem", "label": "Download image",
                        "element_token": "one", "frame": frame(300)})
            els.append({"role": "AXMenuItem", "label": "Download 5 images in this series",
                        "element_token": "all", "frame": frame(340)})
        return "t", window_state_payload(els, pid=9, window_id=2)

    def click(k):
        token = k.get("element_token")
        if token == "img1":
            ui["stage"] = "viewer"
        elif token == "dl":
            ui["stage"] = "menu"
        elif token == "all":
            ui["stage"] = "viewer"
            for n in range(5):
                (tmp_path / f"ChatGPT Image {n}.png").write_bytes(b"x")
        elif token == "close":
            ui["stage"] = "page"
        return "ok", {}

    tools["get_window_state"]._respond = state
    tools["click"]._respond = click
    monkeypatch.setattr(recipes.asyncio, "sleep", lambda *_a, **_k: _instant())
    result = await execute("download_images", task, adapter)
    assert result.state is OutcomeState.CONFIRMED
    assert "Downloaded 5 images" in result.answer
    assert [c["element_token"] for c in tools["click"].calls][:3] == ["img1", "dl", "all"]


async def _instant() -> None:
    return None


def test_download_phrasings_route_to_download_images() -> None:
    from assistant.velo.parse import parse

    for text in (
        "Now you download all image that ChatGPT generate",
        "save all the images",
        "download the mockups",
    ):
        command = parse(text)
        assert command is not None and command.recipe == "download_images", text


async def test_next_clicks_the_following_numbered_image_and_stops_at_the_last() -> None:
    from assistant.velo import recipes
    from tests.unit.velo_fakes import SHARED_APPS

    recipes._IMAGE_CURSOR.clear()
    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Google Chrome", pid=9, running=True)]
    task = _chrome_task()
    shown = {"n": 0}

    def state(k):
        els = [{"role": "AXCheckBox", "label": f"Show generated image {i}",
                "element_token": f"t{i}", "frame": {"x": 1, "y": 100 * i, "w": 50, "h": 40}}
               for i in (1, 2, 3)]
        els.append({"role": "AXStaticText", "label": f"viewing {shown['n']}",
                    "frame": {"x": 1, "y": 5, "w": 50, "h": 40}})
        return "t", window_state_payload(els, pid=9, window_id=2)

    def click(k):
        shown["n"] = int(k["element_token"][1:])
        return "ok", {}

    tools["get_window_state"]._respond = state
    tools["click"]._respond = click
    for expect in ("t1", "t2", "t3"):
        result = await execute("step_item", task, adapter, direction="next")
        assert result.state is OutcomeState.CONFIRMED
        assert tools["click"].calls[-1]["element_token"] == expect
    last = await execute("step_item", task, adapter, direction="next")
    assert last.state is OutcomeState.NO_EFFECT and "last image" in last.answer
    recipes._IMAGE_CURSOR.clear()
