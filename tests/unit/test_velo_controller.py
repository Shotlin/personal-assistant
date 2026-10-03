"""The Velo controller: routes, carried targets, JEV, honest reporting."""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from assistant.agent.context import RunBudget
from assistant.velo.controller import VeloEntry
from assistant.velo.contracts import Candidate, CandidateKind, DecisionStatus, JevDecision

from assistant.tools.policy import desktop_contexts

from tests.unit.velo_fakes import (
    EventLog,
    FakeRuntime,
    app_entry,
    reset_world,
    standard_tools,
    window_state_payload,
)


@pytest.fixture(autouse=True)
def _world():
    reset_world()
    desktop_contexts.clear()
    yield
    reset_world()
    desktop_contexts.clear()


class StubSettings:
    velo_planner_enabled = False
    velo_jev_enabled = False
    velo_command_deadline_seconds = 90
    velo_provider = "typesafe"
    typesafe_api_key = "k"
    velo_jev_model = "jev-latest"
    velo_typesafe_base_url = ""
    velo_max_decision_seconds = 5
    openrouter_api_key = ""


def _entry(
    tools: dict, *, deep: Any = None, jev: Any = None, planner: bool = False
) -> VeloEntry:
    runtime = FakeRuntime(tools)
    if deep is None:
        deep = _RecordingDeep()
    settings = StubSettings()
    if planner:
        settings.velo_planner_enabled = True
        settings.velo_jev_enabled = jev is not None
    return VeloEntry(
        settings,
        get_runtime=runtime.runtime,
        deep_entry=deep,
        jev_factory=lambda: jev,
    ), runtime, deep


class _RecordingDeep:
    def __init__(self) -> None:
        self.runs: list[str] = []

    async def run(self, text, *, thread_id, on_event, cancel_check):
        self.runs.append(text)
        return {
            "status": "done",
            "thread_id": thread_id,
            "response": "planned answer",
            "cua_actions_used": 0,
        }

    async def cancel(self) -> None:  # pragma: no cover
        pass


def _safari_world(*, pid: int = 9) -> None:
    """The driver sees Safari running with one window; chrome is elsewhere."""
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["list"] = [
        app_entry("Google Chrome", pid=7, running=True),
        app_entry("Safari", pid=pid, running=True),
    ]
    SHARED_APPS["windows"][pid] = [
        {"window_id": 2, "is_on_screen": True, "bounds": {"height": 600, "width": 800}}
    ]
    SHARED_APPS["state"][(pid, 2)] = window_state_payload(
        [
            {"role": "AXTextField", "label": "address", "value": "",
             "element_token": "addr-1", "focused": True},
        ],
        pid=pid, window_id=2,
    )


async def test_open_safari_is_route_local_and_confirmed() -> None:
    _safari_world()
    entry, _runtime, deep = _entry(standard_tools())
    log = EventLog()
    result = await entry.run(
        "Open Safari", thread_id="conv-1", on_event=log, cancel_check=lambda: False
    )
    assert result["route"] == "local"
    assert result["verified"] is True
    assert result["status"] == "done"
    assert "Safari" in result["response"]
    assert deep.runs == [], "an explicit command must not wake the planner"
    assert result["timing"]["route"] == "local"
    assert result["engine"] is not None


async def test_open_safari_never_fronts_running_chrome() -> None:
    """End-to-end: the controller's answer to the deterministic defect."""
    from tests.unit.velo_fakes import SHARED_APPS

    _safari_world(pid=9)
    SHARED_APPS["list"].insert(0, app_entry("Google Chrome", pid=7, running=True))
    entry, runtime, _deep = _entry(standard_tools())
    result = await entry.run(
        "Open Safari", thread_id="c", on_event=EventLog(), cancel_check=lambda: False
    )
    assert result["verified"] is True
    tools = runtime.cua_tools
    assert all(call.get("pid") != 7 for call in tools["bring_to_front"].calls)
    assert tools["launch_app"].calls == []


async def test_search_youtube_keeps_the_query_and_does_not_invent_playback() -> None:
    """The acceptance sequence: open Safari, then 'search YouTube for jazz'.

    The search retains the bound browser and the exact query, and it invents
    no playback request from conversation history.
    """
    _safari_world(pid=9)
    entry, runtime, _deep = _entry(standard_tools())
    await entry.run(
        "Open Safari", thread_id="c", on_event=EventLog(), cancel_check=lambda: False
    )
    result = await entry.run(
        "search YouTube for jazz",
        thread_id="c",
        on_event=EventLog(),
        cancel_check=lambda: False,
    )
    assert result["route"] == "local"
    assert result["recipe"] in {"search_browser", "navigate"}
    tools = runtime.cua_tools
    if tools["set_value"].calls:
        delivered = tools["set_value"].calls[0]["value"]
    else:
        delivered = tools["type_text"].calls[0]["text"]
    assert "youtube.com/results?search_query=jazz" in delivered
    # Nothing was played: the only mutations are navigation-shaped.
    assert tools["click"].calls == []


async def test_a_followup_command_acts_on_the_carried_target() -> None:
    """'open Safari' then 'scroll down' -- the scroll lands in Safari."""
    _safari_world(pid=9)
    entry, runtime, _deep = _entry(standard_tools())
    log = EventLog()
    await entry.run("Open Safari", thread_id="conv-7", on_event=log, cancel_check=lambda: False)

    calls: list[dict] = []
    real_scroll = runtime.cua_tools["scroll"]

    async def scroll_spy(kwargs):
        calls.append(kwargs)
        return await real_scroll.ainvoke(kwargs)

    runtime.cua_tools["scroll"] = _Spy(real_scroll, scroll_spy)
    result = await entry.run(
        "scroll down", thread_id="conv-7", on_event=log, cancel_check=lambda: False
    )
    assert result["route"] == "local"
    assert calls and calls[0]["pid"] == 9 and calls[0]["window_id"] == 2


class _Spy:
    def __init__(self, inner, fn) -> None:
        self._inner = inner
        self._fn = fn
        self.args = inner.args

    async def ainvoke(self, kwargs):
        return await self._fn(kwargs)


async def test_an_unresolved_command_goes_to_route_b_and_jev_decides() -> None:
    _safari_world(pid=9)
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["list"] = [app_entry("Safari", pid=9, running=True)]

    class Jev:
        async def decide(self, request):
            assert request.candidates, "the request carries bounded candidates"
            chosen = request.candidates[0]
            return JevDecision(
                status=DecisionStatus.ACT,
                selected_id=chosen.id,
                confidence=0.9,
                applies_to_version=request.task_version,
            )

    entry, runtime, deep = _entry(standard_tools(), jev=Jev())
    entry._settings = type("S", (), {**vars(StubSettings()), "velo_jev_enabled": True})()
    result = await entry.run(
        "safari please", thread_id="c", on_event=EventLog(), cancel_check=lambda: False
    )
    assert result["route"] == "jev"
    assert deep.runs == []


async def test_a_failing_jev_request_is_reported_and_never_substituted() -> None:
    """An ENABLED decision service that fails stops honestly: no model swap."""
    from assistant.velo.contracts import JevServiceError

    _safari_world(pid=9)

    class Failing:
        async def decide(self, request):
            raise JevServiceError("endpoint unreachable")

    entry, _runtime, deep = _entry(standard_tools(), jev=Failing())
    entry._settings = type("S", (), {**vars(StubSettings()), "velo_jev_enabled": True})()
    result = await entry.run(
        "safari please", thread_id="c", on_event=EventLog(), cancel_check=lambda: False
    )
    assert result["route"] == "jev"
    assert "decision service failed" in result["response"]
    assert deep.runs == [], "a failed JEV request must not silently become a model call"


async def test_jev_disabled_by_config_defers_disclosed_to_the_executor() -> None:
    """Live 03:16: with JEV off, natural phrasing must still work -- disclosed."""
    _safari_world(pid=9)
    entry, _runtime, deep = _entry(standard_tools())
    result = await entry.run(
        "safari please", thread_id="c", on_event=EventLog(), cancel_check=lambda: False
    )
    assert deep.runs == ["safari please"], "natural phrasing must still work"
    assert result["route"] == "plan"
    assert "deferred" in result.get("route_note", "")


async def test_a_late_jev_decision_after_stop_produces_no_action() -> None:
    _safari_world(pid=9)

    class LateJev:
        async def decide(self, request):
            from assistant.velo.contracts import JevServiceError

            raise JevServiceError("the answer would arrive after the stop")

    entry, runtime, _deep = _entry(standard_tools())
    entry._settings = type("S", (), {**vars(StubSettings()), "velo_jev_enabled": True})()
    result = await entry.run(
        "safari please",
        thread_id="c",
        on_event=EventLog(),
        cancel_check=lambda: True,  # the stop landed while the request was out
    )
    assert result["response"] == "Stopped." or "Stopped" in result.get("response", "")
    assert result.get("route") == "jev"


async def test_an_unfamiliar_goal_is_delegated_to_the_reasoning_executor() -> None:
    entry, _runtime, deep = _entry(standard_tools())
    result = await entry.run(
        "Write a poem about latency", thread_id="conv-2", on_event=EventLog(),
        cancel_check=lambda: False,
    )
    assert result["route"] == "plan"
    assert result["response"] == "planned answer"
    assert deep.runs == ["Write a poem about latency"]


async def test_questions_and_multi_step_requests_never_execute_locally() -> None:
    entry, runtime, deep = _entry(standard_tools())
    # A multi-step request runs locally only if EVERY clause is a known action;
    # one unfamiliar clause hands the whole sentence to the reasoning route.
    for text in ("What is 2 + 2?", "Open Safari, then compose a haiku about it"):
        await entry.run(text, thread_id="c", on_event=EventLog(), cancel_check=lambda: False)
    assert deep.runs == ["What is 2 + 2?", "Open Safari, then compose a haiku about it"]
    assert runtime.cua_tools["launch_app"].calls == []


async def test_cancellation_before_a_local_command_changes_nothing() -> None:
    _safari_world(pid=9)
    entry, runtime, _deep = _entry(standard_tools())
    result = await entry.run(
        "Open Safari", thread_id="c", on_event=EventLog(), cancel_check=lambda: True
    )
    assert "Stopped" in result["response"]
    assert runtime.cua_tools["launch_app"].calls == []
    assert runtime.cua_tools["bring_to_front"].calls == []


def test_title_command_only_matches_a_just_described_item() -> None:
    from assistant.velo.controller import VeloEntry
    from assistant.velo.scene import SCENES, SceneItem

    SCENES.clear()
    assert VeloEntry._title_command("play vegeta is jealous", "c") is None
    SCENES.remember("c", [SceneItem(1, "Vegeta is jealous of Hit", "DB", False, "t1"),
                          SceneItem(2, "Addiction is very real", "MT", False, "t2")])
    command = VeloEntry._title_command("play vegeta is jealous", "c")
    assert command is not None and command.recipe == "press_item"
    assert VeloEntry._title_command("play some jazz", "c") is None
    SCENES.clear()


async def test_missing_macos_permission_is_one_clear_instruction_not_a_retry() -> None:
    from tests.unit.velo_fakes import FakeTool

    tools = standard_tools()
    tools["check_permissions"] = FakeTool(
        "check_permissions",
        respond=lambda kwargs: ("perms", {"accessibility": False, "screen_recording": False}),
    )
    entry, runtime, _deep = _entry(tools)
    result = await entry.run(
        "Open Safari", thread_id="c", on_event=EventLog(), cancel_check=lambda: False
    )
    assert "Accessibility and Screen Recording are off" in result["response"]
    assert result["status"] == "blocked"
    assert runtime.cua_tools["launch_app"].calls == []


async def test_granted_permissions_do_not_block_commands() -> None:
    from tests.unit.velo_fakes import FakeTool

    _safari_world(pid=9)
    tools = standard_tools()
    tools["check_permissions"] = FakeTool(
        "check_permissions",
        respond=lambda kwargs: ("perms", {"accessibility": True, "screen_recording": True}),
    )
    entry, runtime, _deep = _entry(tools)
    await entry.run("Open Safari", thread_id="c", on_event=EventLog(), cancel_check=lambda: False)
    assert runtime.cua_tools["check_permissions"].calls


async def test_compose_writes_with_one_model_call_then_types_it(monkeypatch) -> None:
    from tests.unit.velo_fakes import SHARED_APPS, app_entry, window_state_payload

    SHARED_APPS["list"] = [app_entry("Codex", pid=9, running=True)]
    tools = standard_tools()
    calls = {"typed": [], "briefs": []}

    def state(k):
        return "t", window_state_payload([
            {"role": "AXTextArea", "label": "Do anything", "element_token": "box",
             "value": tools["set_value"].calls[-1]["value"] if tools["set_value"].calls else "",
             "frame": {"x": 1, "y": 700, "w": 800, "h": 60}},
        ], pid=9, window_id=2)

    tools["get_window_state"]._respond = state
    entry, runtime, _deep = _entry(tools)

    async def fake_compose(self, brief):
        calls["briefs"].append(brief)
        return "Build a minimalist SaaS landing page with pricing and a contact form."

    monkeypatch.setattr(VeloEntry, "_compose", fake_compose)
    from assistant.velo.contracts import AppIdentity, Target

    def bound(task):
        task.resolved = Target(app=AppIdentity(name="Codex", pid=9, running=True), window_id=2)

    monkeypatch.setattr(VeloEntry, "_seed_carried_target", staticmethod(bound))

    result = await entry.run(
        "write a prompt for a simple saas website and type it in the input box",
        thread_id="c9", on_event=EventLog(), cancel_check=lambda: False,
    )
    assert len(calls["briefs"]) == 1
    assert runtime.cua_tools["set_value"].calls[0]["value"].startswith("Build a minimalist")
    assert "wrote the prompt" in result["response"].lower()
    assert result["status"] == "done"



# --------------------------------------------------------------- planner route


def _plan_of(*steps: tuple[str, dict]):
    from assistant.velo.parse import ParsedCommand
    from assistant.velo.planner import Plan

    return Plan("plan", steps=[ParsedCommand(recipe=r, kwargs=k, utterance=r) for r, k in steps])


def _script_planner(monkeypatch, plans: list):
    calls: list = []

    async def fake_plan(self, text, *, replan=None):
        calls.append(replan)
        return plans.pop(0) if plans else None

    monkeypatch.setattr(VeloEntry, "_plan", fake_plan)
    return calls


async def test_a_long_request_is_planned_once_and_run_step_by_step(monkeypatch) -> None:
    _safari_world()
    calls = _script_planner(monkeypatch, [_plan_of(("open_app", {"app_name": "Safari"}))])
    entry, _runtime, deep = _entry(standard_tools(), planner=True)
    result = await entry.run(
        "first get safari going and afterwards scroll down a bit for me", thread_id="c",
        on_event=EventLog(), cancel_check=lambda: False,
    )
    assert calls == [None]  # exactly one planning call, no re-plan
    assert result["route"] == "plan" and result["recipe"] == "plan"
    assert result["planned_steps"] >= 1 and deep.runs == []


async def test_a_failed_step_replans_only_the_rest_from_the_real_screen(monkeypatch) -> None:
    _safari_world()
    calls = _script_planner(monkeypatch, [
        _plan_of(("click_named", {"label": "nonexistent control"})),
        _plan_of(("open_app", {"app_name": "Safari"})),
    ])
    entry, _runtime, _deep = _entry(standard_tools(), planner=True)
    result = await entry.run(
        "first get the ghost thing going and afterwards open safari for me", thread_id="c",
        on_event=EventLog(), cancel_check=lambda: False,
    )
    assert len(calls) == 2 and calls[1] is not None
    assert calls[1]["failed"].startswith("click_named")
    assert result["route"] == "plan" and result["status"] == "done"


async def test_replans_are_bounded(monkeypatch) -> None:
    _safari_world()
    bad = lambda: _plan_of(("click_named", {"label": "ghost button"}))  # noqa: E731
    calls = _script_planner(monkeypatch, [bad(), bad(), bad(), bad(), bad()])
    entry, _runtime, _deep = _entry(standard_tools(), planner=True)
    result = await entry.run(
        "first get the ghost thing going and afterwards open the page for me", thread_id="c",
        on_event=EventLog(), cancel_check=lambda: False,
    )
    assert len(calls) == 3  # the first plan + two re-plans, never more
    assert result["status"] in ("blocked", "failed", "done")
    assert result["response"]  # it says what stopped it


async def test_a_planner_question_is_asked_not_acted_on(monkeypatch) -> None:
    from assistant.velo.planner import Plan

    _script_planner(monkeypatch, [Plan("ask", question="Which channel do you mean?")])
    entry, runtime, _deep = _entry(standard_tools(), planner=True)
    result = await entry.run(
        "could you maybe get youtube going and play that channel video", thread_id="c",
        on_event=EventLog(), cancel_check=lambda: False,
    )
    assert result["status"] == "ASK_USER" and "Which channel" in result["response"]
    assert runtime.cua_tools["launch_app"].calls == []


async def test_chat_skips_the_planner_and_unplannable_requests_fall_back_to_deep(
    monkeypatch,
) -> None:
    from assistant.velo.planner import Plan

    calls = _script_planner(monkeypatch, [Plan("chat")])
    entry, _runtime, deep = _entry(standard_tools(), planner=True)
    await entry.run("hello", thread_id="c", on_event=EventLog(), cancel_check=lambda: False)
    assert calls == [] and deep.runs == ["hello"]  # "hello" never reaches the planner
    await entry.run(
        "explain how to write a good poem about the sea", thread_id="c",
        on_event=EventLog(), cancel_check=lambda: False,
    )
    assert len(calls) == 1 and deep.runs[-1].startswith("explain how")


async def test_jev_can_confirm_a_step_that_could_not_confirm_itself(monkeypatch) -> None:
    from assistant.velo.contracts import JevDecision

    _safari_world()
    _script_planner(monkeypatch, [_plan_of(
        ("open_app", {"app_name": "Safari"}), ("scroll", {"direction": "down"}),
    )])
    asked: list = []

    class Judge:
        async def decide(self, request):
            asked.append(request)
            return JevDecision(status=DecisionStatus.ACT, selected_id="step_ok", confidence=0.9)

    entry, _runtime, _deep = _entry(standard_tools(), planner=True, jev=Judge())

    async def unknown_first(self, command, conversation, on_event, cancel_check, run_key=""):
        if command.recipe == "open_app":
            return {"status": "blocked", "outcome": "unknown", "response": "not sure",
                    "route": "local"}
        return {"status": "done", "outcome": "confirmed", "response": "Scrolled.",
                "route": "local"}

    monkeypatch.setattr(VeloEntry, "_run_local", unknown_first)
    result = await entry.run(
        "first get safari going and afterwards scroll down please now", thread_id="c",
        on_event=EventLog(), cancel_check=lambda: False,
    )
    assert len(asked) == 1 and "step_ok" in [c.id for c in asked[0].candidates]
    assert result["status"] == "done" and result["planned_steps"] == 2
