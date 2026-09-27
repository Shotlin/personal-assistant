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
