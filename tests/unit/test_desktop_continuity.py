"""Conversation continuity for computer control (Phase 6).

Four messages, one task: "Open Chrome", "go to youtube.com", "play a hip-hop
song", "scroll down three times". Each is its own agent run, so without carried
desktop state the third turn has to rediscover what the first one opened -- and
the fourth has no idea what it is scrolling.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from assistant.runtime.session import DesktopSessionManager
from assistant.tools.policy import (
    cua_run_scope,
    cua_target_state,
    desktop_contexts,
)
from assistant.tools.result_normalizer import ToolOutcome
from tests.helpers.fake_driver import FakeDriver

#: A real `get_window_state` answer names the application it read, which is how
#: continuity can say what it is continuing *in*.
SNAPSHOT = {
    "pid": 4242,
    "window_id": 7,
    "app_name": "Google Chrome",
    "snapshot_id": "s00000001",
    "elements": [],
}
APPS = {
    "apps": [
        {"pid": 4242, "bundle_id": "com.google.Chrome", "name": "Google Chrome",
         "active": True, "running": True, "windows": []}
    ]
}


def _tool(name: str, outcome: ToolOutcome, seen: list[dict]) -> StructuredTool:
    async def call(**kwargs: Any) -> ToolOutcome:
        seen.append(kwargs)
        return outcome

    return StructuredTool(
        name=name,
        description=name,
        args_schema={
            "type": "object",
            "properties": {
                key: {}
                for key in ("pid", "window_id", "element_token", "session", "include_screenshot",
                            "screenshot_out_file", "max_elements", "max_depth")
            },
        },
        coroutine=call,
    )


def _outcome(structured: dict) -> ToolOutcome:
    return ToolOutcome(status="ok", effect="not_applicable", text="tree", structured=structured)


async def _observe(conversation: str, seen: list[dict]) -> str:
    """One turn: bind the run scope for ``conversation`` and observe."""
    from assistant.tools.policy import apply_tool_policy

    wrapped, _ = apply_tool_policy([_tool("get_window_state", _outcome(SNAPSHOT), seen)])
    async with cua_run_scope(
        budget=None, run=None, artifact_dir="", conversation=conversation
    ):
        return str(await wrapped[0].ainvoke({"pid": 4242, "window_id": 7}))


async def test_a_later_turn_remembers_the_application_it_was_working_on() -> None:
    desktop_contexts.clear()
    seen: list[dict] = []

    first = await _observe("conversation-a", seen)
    assert "continuing this conversation" not in first, "the first turn has nothing to carry"

    second = await _observe("conversation-a", seen)
    assert "continuing this conversation in Google Chrome (pid 4242, window 7)" in second
    assert "re-snapshot before acting" in second

    third = await _observe("conversation-a", seen)
    assert third.count("continuing this conversation") == 1, "the note repeated in one turn"
    desktop_contexts.clear()


async def test_another_conversation_carries_nothing() -> None:
    desktop_contexts.clear()
    seen: list[dict] = []
    await _observe("conversation-b", seen)

    text = await _observe("conversation-c", seen)
    assert "continuing this conversation" not in text
    desktop_contexts.clear()


async def test_snapshots_never_cross_a_turn_boundary() -> None:
    """Identity carries over; a snapshot does not, or the loop acts on a dead screen."""
    desktop_contexts.clear()
    seen: list[dict] = []
    await _observe("conversation-d", seen)

    from assistant.tools.policy import apply_tool_policy

    clicks: list[dict] = []
    wrapped_click, _ = apply_tool_policy(
        [_tool("click", _outcome({"effect": "confirmed"}), clicks)]
    )
    async with cua_run_scope(budget=None, run=None, conversation="conversation-d"):
        state = cua_target_state.get()
        assert state["focus"] == (4242, 7)
        assert state["snapshots"] == {}, "an element cache from another turn leaked in"
        refused = await wrapped_click[0].ainvoke(
            {"pid": 4242, "window_id": 7, "element_token": "s00000001:0"}
        )
    assert clicks == [], "a stale token was dispatched in the new turn"
    assert "get_window_state" in str(refused)
    desktop_contexts.clear()


async def test_the_driver_session_is_named_after_the_conversation() -> None:
    """One session per task, not one per message: the cursor stays the same."""
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    async with manager.open("run-1", session_id="assistant-conv-9") as run:
        await run.ensure_started()
    starts = [call for call in driver.calls if call.name == "start_session"]
    assert starts[0].args["session"] == "assistant-conv-9"

    async with manager.open("run-2") as run2:
        assert run2.session_id == "assistant-run-2"


async def test_a_run_scope_without_a_conversation_stays_per_run() -> None:
    desktop_contexts.clear()
    seen: list[dict] = []
    first = await _observe("", seen)
    second = await _observe("", seen)
    assert "continuing this conversation" not in first + second
    assert desktop_contexts._entries == {}
    desktop_contexts.clear()
