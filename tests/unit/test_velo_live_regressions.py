"""Regression tests from the 2026-09-26 03:00-03:16 live session."""

from __future__ import annotations

import asyncio

import pytest

from assistant.velo.adapter import CuaAdapter
from assistant.velo.contracts import (
    AppIdentity,
    OutcomeState,
    Postcondition,
    PostconditionKind,
    Target,
    TaskState,
)
from assistant.velo.parse import parse
from assistant.velo.recipes import execute

from tests.unit.velo_fakes import (
    EventLog,
    FakeTool,
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


def _safari_window(pid: int = 9, window_id: int = 2) -> None:
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["windows"][pid] = [
        {"window_id": window_id, "is_on_screen": True,
         "bounds": {"height": 600, "width": 800}}
    ]


# -- Fix 1: verification waits for the page, it does not check once ----------


async def test_navigate_waits_for_slow_pages_instead_of_reporting_failure() -> None:
    """Live 03:04: 'go to youtube.com' could not confirm because the tree
    needed a few seconds; the recipe now polls while the page loads."""
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Safari", pid=9, running=True)]},
    )
    _safari_window()
    loads_on_poll = {"n": 0}

    def get_state(kwargs):
        loads_on_poll["n"] += 1
        # First two reads see the pre-load tree; the page settles on the third.
        if loads_on_poll["n"] >= 3:
            return "t", window_state_payload(
                [
                    {"role": "AXTextField", "label": "address", "value":
                        "https://youtube.com", "element_token": "a1"},
                    {"role": "AXStaticText", "label": "YouTube"},
                ],
                pid=9, window_id=2,
            )
        return "t", window_state_payload([], pid=9, window_id=2)

    tools["get_window_state"]._respond = get_state
    task = _task()
    result = await execute("navigate", task, adapter, destination="youtube.com")
    assert result.state is OutcomeState.CONFIRMED
    assert "Opened https://youtube.com in Safari." == result.answer


async def test_navigate_still_reports_honestly_when_the_page_never_loads() -> None:
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Safari", pid=9, running=True)]},
    )
    _safari_window()
    tools["get_window_state"]._respond = lambda kwargs: (
        "t", window_state_payload([], pid=9, window_id=2),
    )
    task = _task()
    result = await execute("navigate", task, adapter, destination="youtube.com")
    assert result.state is OutcomeState.UNKNOWN
    assert "could not confirm" in result.answer


# -- Fix 2: carried targets answer with the app's name -----------------------


async def test_scroll_on_a_carried_target_names_the_application() -> None:
    """Live 03:08: 'Scrolled down in .' -- the carried seed had no name."""
    from tests.unit.velo_fakes import SHARED_APPS

    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Google Chrome", pid=7, running=True)]
    task = _task()
    task.resolved = Target(
        app=AppIdentity(name="", pid=7, running=True), window_id=1
    )
    _safari_window(pid=7, window_id=1)

    reads = {"n": 0}

    def get_state(kwargs):
        reads["n"] += 1
        label = "top" if reads["n"] <= 1 else "bottom"
        return "t", window_state_payload(
            [{"role": "AXStaticText", "label": label}], pid=7, window_id=1
        )

    tools["get_window_state"]._respond = get_state
    result = await execute("scroll", task, adapter, direction="down", amount=3)
    assert result.state is OutcomeState.CONFIRMED
    assert "Scrolled down in Google Chrome." == result.answer


# -- Fix 3: dictation word order for press commands --------------------------


@pytest.mark.parametrize(
    ("text", "index"),
    [
        ("First Link Click.", 1),
        ("first link click", 1),
        ("third link you press", 3),
        ("the second button press", 2),
        ("click first link", 1),
    ],
)
def test_dictation_word_order_presses(text: str, index: int) -> None:
    command = parse(text)
    assert command is not None, text
    assert command.recipe == "press_ordinal"
    assert command.kwargs["index"] == index


def test_the_verb_first_form_still_parses() -> None:
    command = parse("press the third link")
    assert command is not None
    assert command.kwargs == {"kind": "link", "index": 3}


# -- Fix 4: JEV disabled defers to the executor instead of dead-ending -------


async def test_route_b_without_jev_defers_to_the_reasoning_executor() -> None:
    """Live 03:16: an unparseable instruction got an unhelpful dead end."""
    from tests.unit.velo_fakes import FakeRuntime

    from assistant.velo.controller import VeloEntry

    class Deep:
        def __init__(self) -> None:
            self.runs: list[str] = []

        async def run(self, text, *, thread_id, on_event, cancel_check):
            self.runs.append(text)
            return {"status": "done", "thread_id": thread_id,
                    "response": "did it", "cua_actions_used": 0}

        async def cancel(self) -> None:
            pass

    from tests.unit.velo_fakes import SHARED_APPS, app_entry

    SHARED_APPS["list"] = [app_entry("ChatGPT", pid=3, running=True)]
    runtime = FakeRuntime(standard_tools())
    deep = Deep()
    entry = VeloEntry(
        StubSettings(velo_jev_enabled=False),
        get_runtime=runtime.runtime,
        deep_entry=deep,
        jev_factory=lambda: None,
    )
    result = await entry.run(
        "No chatgpt input box inside write hi",
        thread_id="conv-1",
        on_event=EventLog(),
        cancel_check=lambda: False,
    )
    assert deep.runs == ["No chatgpt input box inside write hi"]
    assert result["response"] == "did it"
    assert result["route"] == "plan"
    assert "deferred" in result["route_note"]


class StubSettings:
    def __init__(self, *, velo_jev_enabled: bool = False) -> None:
        self.velo_jev_enabled = velo_jev_enabled
        self.velo_command_deadline_seconds = 90


# -- Fix 5: window discovery waits for the window to exist -------------------


async def test_open_app_waits_through_window_creation_lag() -> None:
    """Live 03:03: 'Open safari' reported no window that was about to exist."""
    adapter, tools, _log = _adapter()
    tools["list_apps"]._respond = lambda kwargs: (
        "t", {"apps": [app_entry("Safari", pid=9, running=True)]},
    )
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["windows"][9] = []  # no window yet

    def bring(kwargs):
        SHARED_APPS["windows"][9] = [
            {"window_id": 2, "is_on_screen": True,
             "bounds": {"height": 600, "width": 800}}
        ]
        return "ok", {}

    tools["bring_to_front"]._respond = bring
    tools["get_window_state"]._respond = lambda kwargs: (
        "t", window_state_payload(
            [{"role": "AXStaticText", "label": "start page"}], pid=9, window_id=2,
        ),
    )
    task = _task()
    result = await execute("open_app", task, adapter, app_name="Safari")
    assert result.state is OutcomeState.CONFIRMED
    assert "Brought Safari." == result.answer


# -- Fix 5b: a carried window id that died is re-resolved --------------------


async def test_navigate_re_resolves_a_closed_carried_window() -> None:
    from tests.unit.velo_fakes import SHARED_APPS

    adapter, tools, _log = _adapter()
    SHARED_APPS["list"] = [app_entry("Safari", pid=9, running=True)]
    # Carried window 2 is gone; the browser has window 5 now.
    SHARED_APPS["windows"][9] = [
        {"window_id": 5, "is_on_screen": True, "bounds": {"height": 600, "width": 800}},
    ]
    task = _task()
    task.resolved = Target(
        app=AppIdentity(name="Safari", pid=9, running=True), window_id=2
    )

    def get_state(kwargs):
        assert kwargs["window_id"] == 5, "must act on the live window"
        if tools["press_key"].calls:
            return "t", window_state_payload(
                [{"role": "AXTextField", "label": "address", "value":
                    "https://example.com", "element_token": "a1"}],
                pid=9, window_id=5,
            )
        return "t", window_state_payload(
            [{"role": "AXTextField", "label": "address", "value": "",
              "element_token": "a1", "focused": True}],
            pid=9, window_id=5,
        )

    tools["get_window_state"]._respond = get_state
    result = await execute("navigate", task, adapter, destination="example.com")
    assert result.state is OutcomeState.CONFIRMED


# -- Fix 6: the third identical mutation is refused --------------------------


async def test_policy_refuses_the_third_identical_mutation() -> None:
    """Live 03:13: the same link was clicked seven times in a row."""
    from assistant.agent.context import RunBudget
    from assistant.tools.policy import apply_tool_policy, cua_run_scope

    class FakeDriverTool:
        def __init__(self, name: str) -> None:
            self.name = name
            self.calls = 0

        @property
        def args(self) -> dict:
            base = {
                "pid": {"type": "integer"},
                "window_id": {"type": "integer"},
            }
            if self.name == "click":
                base["element_token"] = {"type": "string"}
            else:
                base.update({
                    "include_screenshot": {"type": "boolean"},
                    "max_elements": {"type": "integer"},
                    "max_depth": {"type": "integer"},
                    "session": {"type": "string"},
                    "screenshot_out_file": {"type": "string"},
                })
            return base

        async def coroutine(self, **kwargs):
            from assistant.tools.result_normalizer import ToolOutcome

            self.calls += 1
            if self.name == "click":
                return f"clicked {kwargs.get('element_token')}"
            return ToolOutcome(
                status="ok",
                effect="confirmed",
                text="window read",
                structured={
                    "pid": kwargs.get("pid"),
                    "window_id": kwargs.get("window_id"),
                    "snapshot_id": "snap-1",
                    "elements": [],
                },
            )

    observer = FakeDriverTool("get_window_state")
    inner = FakeDriverTool("click")
    wrapped, _names = apply_tool_policy([observer, inner])
    click = next(t for t in wrapped if t.name == "click")
    observe = next(t for t in wrapped if t.name == "get_window_state")
    budget = RunBudget()

    def click_args(token: str) -> dict:
        return {"element_token": token, "pid": 9, "window_id": 2}

    async with cua_run_scope(budget=budget, run=None, conversation="") :
        # The targeting invariant requires a current snapshot of the surface
        # before an element-token action, exactly as real callers do.
        await observe.ainvoke({"pid": 9, "window_id": 2, "include_screenshot": False})
        first = await click.ainvoke(click_args("tok-1"))
        second = await click.ainvoke(click_args("tok-1"))
        third = await click.ainvoke(click_args("tok-1"))
        fourth = await click.ainvoke(click_args("tok-1"))

    assert "clicked tok-1" == first == second
    assert inner.calls == 2, "the third and fourth identical clicks never dispatch"
    assert "[loop]" in third and "3 times" in third
    assert "[loop]" in fourth
    # A different action resets the counter: it may proceed (after its own
    # snapshot, as the targeting invariant requires in every fresh scope).
    async with cua_run_scope(budget=budget, run=None, conversation=""):
        await observe.ainvoke({"pid": 9, "window_id": 2, "include_screenshot": False})
        result = await click.ainvoke(click_args("tok-2"))
    assert result == "clicked tok-2"
    assert inner.calls == 3
