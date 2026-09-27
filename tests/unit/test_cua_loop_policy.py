"""The computer-control loop's contract (Phase 5, architecture D2).

One executor: the LLM tool loop. What is verified here is the machinery around
it -- the deterministic action-class gate that replaced the manifest ceiling, the
targeting invariant that stops an action being aimed at nothing, the recovery
ladder the model is handed instead of a dead end, and vision as an on-demand rung.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from langchain_core.tools import StructuredTool

from assistant.tools.policy import (
    ActionClass,
    _window_enumeration,
    apply_tool_policy,
    classify_cua_call,
    cua_artifact_dir,
    cua_target_state,
    fault_guidance,
    wrap_tool_errors,
)
from assistant.tools.result_normalizer import ImageRef, ToolOutcome


def make_tool(name: str, outcome: ToolOutcome, calls: list[dict]) -> StructuredTool:
    async def run(**kwargs: Any) -> ToolOutcome:
        calls.append(kwargs)
        if outcome is RAISE:
            raise anyio.ClosedResourceError()
        return outcome

    return StructuredTool(
        name=name,
        description=f"{name} description",
        args_schema={
            "type": "object",
            "properties": {
                key: {}
                for key in (
                    "pid", "window_id", "element_token", "text", "direction", "amount",
                    "key", "keys", "bundle_id", "name", "include_screenshot",
                    "screenshot_out_file", "delivery_mode", "session", "max_elements",
                    "max_depth", "query",
                )
            },
        },
        coroutine=run,
    )


RAISE = ToolOutcome(status="failed", effect="unverifiable", text="never returned")


def ok(structured: dict | None = None, effect: str = "confirmed") -> ToolOutcome:
    return ToolOutcome(status="ok", effect=effect, text="done", structured=structured or {})


@pytest.fixture
def scope(tmp_path):
    """A run scope: fresh targeting state, artifacts on, no budget or ledger."""
    token_state = cua_target_state.set({"snapshots": {}, "focus": None, "apps": {}})
    token_dir = cua_artifact_dir.set(str(tmp_path))
    yield tmp_path
    cua_target_state.reset(token_state)
    cua_artifact_dir.reset(token_dir)


# ------------------------------------------------------------- action classes


def test_the_gate_is_decided_by_the_target_not_the_verb() -> None:
    apps = {77: "com.apple.Terminal", 9: "com.apple.Notes", 12: "com.apple.systempreferences"}
    assert classify_cua_call("click", {"pid": 9, "element_token": "t"}, apps) == ActionClass.ACT
    assert (
        classify_cua_call("click", {"pid": 77, "element_token": "t"}, apps)
        is ActionClass.SENSITIVE
    )
    assert classify_cua_call("type_text", {"pid": 77, "text": "ls"}, apps) == ActionClass.SENSITIVE
    # Reading a credential surface is not acting on it.
    assert classify_cua_call("get_window_state", {"pid": 77}, apps) == ActionClass.READ
    assert classify_cua_call("launch_app", {"name": "Notes"}, apps) == ActionClass.ACT
    assert classify_cua_call("kill_app", {"pid": 9}, apps) == ActionClass.SENSITIVE


def test_the_gate_catches_the_shapes_a_model_actually_writes() -> None:
    """The Phase 7 battery drove a shell on 2026-09-25 with this gate in place.

    It compared whole reverse-DNS ids, and the loop asks for applications by
    name: ``launch_app(name="Terminal")`` was classified as ordinary work. The
    gate now has to answer for the arguments a model writes, not the arguments a
    test author would write.
    """
    for kwargs in (
        {"name": "Terminal"},
        {"name": "terminal"},
        {"bundle_id": "com.apple.Terminal"},
        {"name": "Terminal.app"},
    ):
        assert classify_cua_call("launch_app", kwargs) is ActionClass.SENSITIVE
    assert classify_cua_call("launch_app", {"name": "System Settings"}) is ActionClass.SENSITIVE

    # A pid is only as safe as the identity remembered for it, and that depends
    # on which of the two fields a payload happened to carry.
    for apps in ({77: "Terminal"}, {77: "com.apple.Terminal"}):
        assert classify_cua_call("bring_to_front", {"pid": 77}, apps) is ActionClass.SENSITIVE
        aimed = classify_cua_call("type_text", {"pid": 77, "text": "ls"}, apps)
        assert aimed is ActionClass.SENSITIVE

    # Benign desktop work stays ungated however it is spelled.
    for kwargs in (
        {"name": "TextEdit"},
        {"bundle_id": "com.apple.TextEdit"},
        {"name": "Google Chrome"},
        {"name": "Stickies"},
    ):
        assert classify_cua_call("launch_app", kwargs) is ActionClass.ACT


#: Captured from a live ``list_windows`` of TextEdit and Stickies, 2026-09-25.
WINDOWS = {
    "current_space_id": 1,
    "windows": [
        {"window_id": 5140, "title": "Open", "is_on_screen": True,
         "bounds": {"x": 295.0, "y": 134.0, "width": 880.0, "height": 448.0}},
        {"window_id": 5142, "title": "Save Panel Accessory View", "is_on_screen": False,
         "bounds": {"x": 462.0, "y": 518.0, "width": 713.0, "height": 64.0}},
        {"window_id": 99, "title": "Menubar", "is_on_screen": True,
         "bounds": {"x": 0.0, "y": 0.0, "width": 1512.0, "height": 33.0}},
    ],
}


def test_an_observation_of_windows_gives_ids_the_loop_can_use() -> None:
    """The driver answers ``list_windows`` with a count; the model needs the ids.

    Told only "Found 5 window(s).", the Phase 7 loop guessed ``window_id 1`` and
    then 2, 3, 4 and 5 -- every one refused -- because nothing it could see named
    a real window.
    """
    text = _window_enumeration(WINDOWS)
    assert "- window_id 5140" in text
    assert "off screen" in text
    assert "too small to aim" in text, "a 33pt menu bar is not a target"
    assert _window_enumeration({"windows": []}) == ""


async def test_the_window_list_the_model_reads_carries_those_ids(scope) -> None:
    calls: list[dict] = []
    tool = wrap_tool_errors(make_tool("list_windows", ok(WINDOWS), calls))
    text = await tool.ainvoke({"pid": 42})
    assert "window_id 5140" in text
    assert "Found" in text or "- window_id" in text


async def test_a_sensitive_target_is_refused_without_being_dispatched(scope) -> None:
    calls: list[dict] = []
    state = cua_target_state.get()
    state["apps"] = {77: "com.apple.Terminal"}
    wrapped = wrap_tool_errors(make_tool("click", ok(), calls))

    text = await wrapped.ainvoke({"pid": 77, "window_id": 3, "element_token": "s1:0"})

    assert calls == [], "a refused action still reached the driver"
    assert text.startswith("Refused:")
    assert "credentials" in text and "Ask the user" in text


async def test_an_ordinary_action_runs_without_any_confirmation(scope) -> None:
    calls: list[dict] = []
    state = cua_target_state.get()
    state["apps"] = {9: "com.apple.Notes"}
    state["snapshots"]["9:3"] = "s00000001"
    wrapped = wrap_tool_errors(make_tool("click", ok(), calls))

    result = await wrapped.ainvoke(
        {"pid": 9, "window_id": 3, "element_token": "s00000001:0"}
    )

    assert calls == [
        {"pid": 9, "window_id": 3, "element_token": "s00000001:0", "session": None}
    ] or len(calls) == 1
    assert "done" in str(result)


# ------------------------------------------------------- targeting invariant


async def test_an_action_without_a_resolved_target_is_refused_locally(scope) -> None:
    calls: list[dict] = []
    wrapped = wrap_tool_errors(make_tool("click", ok(), calls))

    text = await wrapped.ainvoke({"element_token": "s00000001:0"})

    assert calls == []
    assert "[target]" in text
    assert "list_windows" in text
    assert "never ask the user for a pid" in text


async def test_an_element_token_needs_a_snapshot_of_that_surface(scope) -> None:
    calls: list[dict] = []
    wrapped = wrap_tool_errors(make_tool("click", ok(), calls))

    text = await wrapped.ainvoke({"pid": 42, "window_id": 7, "element_token": "s00000001:0"})

    assert calls == [], "a token was dispatched with no snapshot behind it"
    assert "get_window_state" in text


async def test_a_snapshot_unlocks_the_token_and_a_raise_invalidates_it(scope) -> None:
    seen: list[dict] = []
    snapshot = wrap_tool_errors(
        make_tool(
            "get_window_state",
            ok({"pid": 42, "window_id": 7, "snapshot_id": "s00000001", "elements": []}),
            seen,
        )
    )
    click = wrap_tool_errors(make_tool("click", ok(), seen))
    raise_front = wrap_tool_errors(make_tool("bring_to_front", ok(), seen))

    await snapshot.ainvoke({"pid": 42, "window_id": 7})
    assert cua_target_state.get()["snapshots"] == {"42:7": "s00000001"}
    assert cua_target_state.get()["apps"] == {}  # no bundle id in this payload

    await click.ainvoke({"pid": 42, "window_id": 7, "element_token": "s00000001:0"})
    assert sum(1 for call in seen if "element_token" in call) == 1

    # Raising a window cascades it, so every frame read before it is stale.
    await raise_front.ainvoke({"pid": 42, "window_id": 7})
    assert cua_target_state.get()["snapshots"] == {}
    before = len(seen)
    refused = await click.ainvoke({"pid": 42, "window_id": 7, "element_token": "s00000001:0"})
    assert len(seen) == before, "a stale snapshot still authorised a click"
    assert "get_window_state" in refused


async def test_a_launch_teaches_the_gate_which_pid_is_which_app(scope) -> None:
    seen: list[dict] = []
    launcher = wrap_tool_errors(
        make_tool("launch_app", ok({"pid": 77, "bundle_id": "com.apple.Notes"}), seen)
    )
    await launcher.ainvoke({"name": "Notes"})
    assert cua_target_state.get()["apps"] == {77: "com.apple.Notes"}


async def test_an_observation_teaches_the_gate_which_pid_is_a_shell(scope) -> None:
    """How the loop really learns it: ``list_apps``, because that is where the
    pid of an already-running Terminal comes from."""
    seen: list[dict] = []
    lister = wrap_tool_errors(
        make_tool(
            "list_apps",
            ok({"apps": [{"pid": 77, "bundle_id": "com.apple.Terminal", "name": "Terminal"}]}),
            seen,
        )
    )
    await lister.ainvoke({})
    assert cua_target_state.get()["apps"] == {77: "com.apple.Terminal"}

    typer = wrap_tool_errors(make_tool("type_text", ok(), seen))
    refused = await typer.ainvoke({"pid": 77, "window_id": 1, "text": "rm -rf /"})
    assert refused.startswith("Refused:")


async def test_naming_a_shell_is_refused_before_anything_is_launched(scope) -> None:
    """Phase 7 live battery, 2026-09-25: the loop typed into a Terminal it should
    never have opened, because the gate only recognised whole reverse-DNS ids.
    """
    seen: list[dict] = []
    launcher = wrap_tool_errors(make_tool("launch_app", ok(), seen))
    refused = await launcher.ainvoke({"name": "Terminal"})
    assert refused.startswith("Refused:")
    assert seen == [], "the launch reached the driver after being refused"
    assert cua_target_state.get()["apps"] == {}


# ------------------------------------------------------------ recovery ladder


def test_a_dead_lease_becomes_a_transport_fault_with_a_next_move() -> None:
    text = fault_guidance("", anyio.ClosedResourceError())
    assert "[transport]" in text
    assert "reconnects the driver itself" in text


def test_a_stale_window_names_the_call_that_fixes_it() -> None:
    text = fault_guidance(
        '{"code": "window_not_found", "reason": "WindowServer has no record of window 9"}'
    )
    assert "[window]" in text
    assert "list_windows again" in text


def test_a_missing_pid_is_a_target_fault_not_a_mystery() -> None:
    assert "[target]" in fault_guidance("Missing required integer field: pid")


async def test_a_refused_outcome_still_carries_its_next_move(scope) -> None:
    calls: list[dict] = []
    refused = ToolOutcome(
        status="ok",
        effect="refused",
        text='{"code": "window_not_found"}',
        structured={"code": "window_not_found", "effect": "refused"},
    )
    wrapped = wrap_tool_errors(make_tool("scroll", refused, calls))
    state = cua_target_state.get()
    state["snapshots"]["42:7"] = "s00000001"

    text = await wrapped.ainvoke({"pid": 42, "window_id": 7, "direction": "down"})

    assert "[window]" in text and "list_windows again" in text


async def test_an_unverifiable_effect_escalates_to_the_vision_rung(scope) -> None:
    calls: list[dict] = []
    wrapped = wrap_tool_errors(
        make_tool("click", ToolOutcome(status="ok", effect="unverifiable", text="ok"), calls)
    )
    state = cua_target_state.get()
    state["snapshots"]["42:7"] = "s00000001"

    text = await wrapped.ainvoke({"pid": 42, "window_id": 7, "element_token": "s00000001:0"})

    assert "include_screenshot: true" in text
    assert "rather than repeating the action" in text


# ------------------------------------------------------------------- vision


async def test_an_explicit_screenshot_reaches_the_model(scope) -> None:
    calls: list[dict] = []
    outcome = ToolOutcome(
        status="ok",
        effect="not_applicable",
        text="tree",
        images=[ImageRef(data_base64="aW1n", mime_type="image/png")],
    )
    wrapped = wrap_tool_errors(make_tool("get_window_state", outcome, calls))

    blocks = await wrapped.ainvoke({"pid": 42, "window_id": 7, "include_screenshot": True})

    assert calls[0].get("screenshot_out_file") is None, "the capture was diverted to a file"
    kinds = [block.get("type") for block in blocks] if isinstance(blocks, list) else []
    assert "image" in kinds


async def test_an_ordinary_observation_stays_text_only(scope) -> None:
    """The text-first baseline: no capture is asked for, and nothing inlines.

    If the driver still hands back an image, it is reported by count -- the model
    pays for a picture only when it asked for one.
    """
    calls: list[dict] = []
    outcome = ToolOutcome(
        status="ok",
        effect="not_applicable",
        text="tree",
        images=[ImageRef(data_base64="aW1n", mime_type="image/png")],
    )
    wrapped = wrap_tool_errors(make_tool("get_window_state", outcome, calls))

    text = await wrapped.ainvoke({"pid": 42, "window_id": 7})

    assert calls[0]["include_screenshot"] is False
    assert "screenshot_out_file" not in calls[0]
    assert isinstance(text, str)
    assert "screenshot(s) retained locally" in text


async def test_apply_tool_policy_keeps_every_tool_callable(scope) -> None:
    calls: list[dict] = []
    tools = [make_tool(name, ok(), calls) for name in ("list_apps", "click", "get_screen_size")]
    wrapped, names = apply_tool_policy(tools)
    assert names == ["list_apps", "click", "get_screen_size"]
    assert all(getattr(tool, "description", "") for tool in wrapped)
