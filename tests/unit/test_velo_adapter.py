"""The CUA adapter: typed readback, contract failures, exact target resolution."""

from __future__ import annotations

import pytest

from assistant.tools.policy import STRUCTURED_CALLS
from assistant.velo.adapter import CuaAdapter
from assistant.velo.contracts import AdapterContractError, AppIdentity, TaskState

from tests.unit.velo_fakes import FakeTool, app_entry


@pytest.fixture(autouse=True)
def _clean_call_log():
    STRUCTURED_CALLS._entries.clear()
    STRUCTURED_CALLS._next = 0
    yield
    STRUCTURED_CALLS._entries.clear()
    STRUCTURED_CALLS._next = 0


def _adapter(**tools: FakeTool) -> CuaAdapter:
    async def on_event(kind, data):
        pass

    return CuaAdapter(dict(tools), on_event=on_event)


async def test_a_call_returns_the_typed_payload_of_its_own_dispatch() -> None:
    apps = FakeTool(
        "list_apps",
        respond=lambda kwargs: ("Listed 1 app.", {"apps": [app_entry("Safari", pid=42)]}),
    )
    adapter = _adapter(list_apps=apps)
    reply = await adapter.call("list_apps")
    assert reply.ok
    assert reply.tool == "list_apps"
    assert reply.structured["apps"][0]["name"] == "Safari"
    assert "Listed 1 app." in reply.text


async def test_an_unknown_argument_is_a_contract_failure_not_a_silent_filter() -> None:
    launch = FakeTool("launch_app", args={"bundle_id": {"type": "string"}})
    adapter = _adapter(launch_app=launch)
    with pytest.raises(AdapterContractError, match="does not declare"):
        await adapter.call("launch_app", bundle_id="x", invented_arg="y")
    assert launch.calls == [], "the call must never reach the driver"


async def test_an_interleaved_readback_is_refused_rather_than_masquerading() -> None:
    """The failure mode of the name-keyed dictionary, made impossible.

    A second dispatch records its payload inside the first call's window;
    the adapter must refuse the read instead of using a stranger's payload.
    """
    from assistant.tools.policy import STRUCTURED_CALLS

    def interleaving_respond(kwargs):
        STRUCTURED_CALLS.record("get_window_state", {"pid": 9})
        return "one", {"apps": [1]}

    first = FakeTool("list_apps", respond=interleaving_respond)
    adapter = _adapter(list_apps=first)
    with pytest.raises(AdapterContractError, match="interleaved"):
        await adapter.call("list_apps")


async def test_a_missing_tool_is_a_contract_failure() -> None:
    adapter = _adapter()
    with pytest.raises(AdapterContractError, match="does not expose"):
        await adapter.call("launch_app")


async def test_list_apps_maps_driver_entries_to_identities() -> None:
    listing = FakeTool(
        "list_apps",
        respond=lambda kwargs: (
            "t",
            {
                "apps": [
                    app_entry("Safari", pid=42, active=True),
                    app_entry("Google Chrome", pid=7, running=False),
                ]
            },
        ),
    )
    adapter = _adapter(list_apps=listing)
    task = TaskState(instruction="x")
    apps = await adapter.list_apps(task)
    assert apps[0].pid == 42 and apps[0].active and apps[0].running
    assert apps[1].running is False


async def test_front_window_picks_the_aimable_on_screen_window() -> None:
    windows = FakeTool(
        "list_windows",
        respond=lambda kwargs: (
            "t",
            {
                "windows": [
                    {"window_id": 3, "is_on_screen": False, "bounds": {"height": 500}},
                    {"window_id": 5, "is_on_screen": True, "bounds": {"height": 30}},
                    {"window_id": 8, "is_on_screen": True, "bounds": {"height": 600, "width": 800}},
                ]
            },
        ),
    )
    adapter = _adapter(list_windows=windows)
    task = TaskState(instruction="x")
    assert await adapter.front_window(task, pid=1) == 8


def test_safari_never_resolves_to_running_chrome() -> None:
    """The live defect: explicit browser names fell into the browser category."""
    apps = [
        AppIdentity(name="Google Chrome", bundle_id="com.google.Chrome", pid=7, running=True),
        AppIdentity(name="Safari", bundle_id="com.apple.Safari", pid=9),
    ]
    resolved, _ = CuaAdapter.resolve_app("Safari", apps)
    assert resolved is not None and resolved.pid == 9


def test_chrome_resolves_to_chrome_even_when_safari_is_running() -> None:
    apps = [
        AppIdentity(name="Google Chrome", bundle_id="com.google.Chrome", pid=7, running=True),
        AppIdentity(name="Safari", bundle_id="com.apple.Safari", pid=9, running=True),
    ]
    resolved, _ = CuaAdapter.resolve_app("chrome", apps)
    assert resolved is not None and resolved.pid == 7


def test_the_word_browser_prefers_the_one_browser_already_running() -> None:
    apps = [
        AppIdentity(name="Google Chrome", bundle_id="com.google.Chrome", pid=7, running=True),
        AppIdentity(name="Safari", bundle_id="com.apple.Safari", pid=9, running=False),
    ]
    resolved, _ = CuaAdapter.resolve_app("browser", apps)
    assert resolved is not None and resolved.pid == 7


def test_the_word_browser_with_two_running_browsers_defers() -> None:
    apps = [
        AppIdentity(name="Google Chrome", bundle_id="com.google.Chrome", pid=7, running=True),
        AppIdentity(name="Safari", bundle_id="com.apple.Safari", pid=9, running=True),
    ]
    resolved, _ = CuaAdapter.resolve_app("browser", apps)
    assert resolved is None, "two candidates means the user decides, not the router"


def test_ambiguous_category_with_no_running_browser_defers() -> None:
    apps = [
        AppIdentity(name="Google Chrome", bundle_id="com.google.Chrome"),
        AppIdentity(name="Safari", bundle_id="com.apple.Safari"),
    ]
    resolved, _ = CuaAdapter.resolve_app("browser", apps)
    assert resolved is None


def test_two_name_matches_are_the_users_decision_not_the_routers() -> None:
    apps = [
        AppIdentity(name="Code", bundle_id="com.todesktop.Code", pid=1),
        AppIdentity(name="VS Code", bundle_id="com.microsoft.VSCode", pid=2),
    ]
    resolved, _ = CuaAdapter.resolve_app("code", apps)
    assert resolved is None


def test_an_exact_name_outranks_a_word_inside_another_apps_name() -> None:
    apps = [
        AppIdentity(name="Mail", bundle_id="com.apple.Mail", pid=1),
        AppIdentity(name="Mailmate", bundle_id="com.freron.Mailmate", pid=2),
    ]
    resolved, _ = CuaAdapter.resolve_app("mail", apps)
    assert resolved is not None and resolved.pid == 1


def test_scene_digest_is_stable_and_discriminates() -> None:
    state = {"elements": [{"role": "AXButton", "label": "Play"}]}
    assert CuaAdapter.scene_digest(state) == CuaAdapter.scene_digest(state)
    moved = {"elements": [{"role": "AXButton", "label": "Pause"}]}
    assert CuaAdapter.scene_digest(state) != CuaAdapter.scene_digest(moved)
