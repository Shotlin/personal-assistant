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
    velo_jev_enabled = False
    velo_command_deadline_seconds = 90
    velo_provider = "typesafe"
    typesafe_api_key = "k"
    velo_jev_model = "jev-latest"
    velo_typesafe_base_url = ""
    velo_max_decision_seconds = 5
    openrouter_api_key = ""


def _entry(tools: dict, *, deep: Any = None, jev: Any = None) -> VeloEntry:
    runtime = FakeRuntime(tools)
    if deep is None:
        deep = _RecordingDeep()
    return VeloEntry(
        StubSettings(),
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
    for text in ("What is 2 + 2?", "Open Safari, then search YouTube"):
        await entry.run(text, thread_id="c", on_event=EventLog(), cancel_check=lambda: False)
    assert deep.runs == ["What is 2 + 2?", "Open Safari, then search YouTube"]
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
