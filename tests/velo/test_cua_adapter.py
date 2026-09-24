"""CUA adapter tests against fake MCP tools (Velo spec sections 6, 13, 18)."""

import pytest

from assistant.agent.context import RunBudget
from assistant.runtime.session import DesktopRun, DesktopRunCancelled, DesktopSessionConfig
from assistant.tools.cua import CuaConnection
from assistant.tools.result_normalizer import ToolOutcome
from assistant.velo.cua_adapter import VeloCuaAdapter, allowed_apps_from_manifest
from assistant.velo.types import (
    VeloActionKind,
    VeloCuaError,
    VeloObjective,
)
from tests.helpers.fake_driver import FakeDriver
from tests.velo.fakes import act, observation, target

WINDOW_STATE = {
    "window": {"title": "WhatsApp"},
    "url": "https://web.whatsapp.com/",
    "focused_element": "message input",
    "modal": False,
    "elements": [
        {"element_token": "tok-1", "role": "button", "label": "Rahul", "value": "2 unread"},
        {"element_token": "tok-2", "role": "button", "label": "Sayan", "value": "1 unread"},
        {"element_token": "tok-3", "role": "textbox", "label": "message input"},
        {"no_label_at_all": True},
    ],
}

APPS_STATE = {
    "apps": [
        {
            "pid": 4242,
            "bundle_id": "com.google.Chrome",
            "name": "Google Chrome",
            "frontmost": True,
            "windows": [{"window_id": 7, "title": "WhatsApp"}],
        },
        {"pid": 100, "bundle_id": "com.apple.Terminal", "name": "Terminal", "windows": []},
    ]
}


class FakeTool:
    """Minimal StructuredTool double: records calls, returns one outcome."""

    def __init__(
        self,
        name: str,
        outcome: ToolOutcome,
        *,
        arg_names: tuple[str, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.args = {arg_name: None for arg_name in arg_names}
        self.outcome = outcome
        self.error = error
        self.calls: list[dict] = []

    async def ainvoke(self, kwargs: dict) -> ToolOutcome:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.outcome


def ok_outcome(structured: dict | None = None) -> ToolOutcome:
    return ToolOutcome(status="ok", effect="not_applicable", structured=structured or {})


def build_adapter(
    tools_by_name: dict[str, FakeTool], *, allowed_apps: dict[str, str] | None = None
) -> tuple[VeloCuaAdapter, DesktopRun]:
    connection = CuaConnection(
        tools=[],
        tool_names=sorted(tools_by_name),
        discovered_names=sorted(tools_by_name),
        skipped_names=[],
        tools_by_name=tools_by_name,  # type: ignore[arg-type]
    )
    run = DesktopRun("run-1", "assistant-run-1", FakeDriver(), DesktopSessionConfig())
    budget = RunBudget(max_actions=10)
    adapter = VeloCuaAdapter(
        connection, run, budget, allowed_apps=allowed_apps or {"com.google.Chrome": "Chrome"}
    )
    return adapter, run


@pytest.fixture
def cua_tools() -> dict[str, FakeTool]:
    return {
        "list_apps": FakeTool("list_apps", ok_outcome(APPS_STATE), arg_names=()),
        "get_window_state": FakeTool(
            "get_window_state",
            ok_outcome(WINDOW_STATE),
            arg_names=("pid", "window_id", "include_screenshot", "max_elements", "max_depth"),
        ),
        "get_accessibility_tree": FakeTool(
            "get_accessibility_tree", ok_outcome({"elements": []}), arg_names=("max_elements",)
        ),
        "click": FakeTool(
            "click", ok_outcome({"effect": "confirmed"}), arg_names=("element_token",)
        ),
        "type_text": FakeTool(
            "type_text", ok_outcome({"effect": "confirmed"}), arg_names=("element_token", "text")
        ),
        "launch_app": FakeTool(
            "launch_app", ok_outcome({"effect": "confirmed"}), arg_names=("bundle_id",)
        ),
        "scroll": FakeTool(
            "scroll", ok_outcome({"effect": "confirmed"}), arg_names=("direction", "amount")
        ),
    }


async def test_observe_prefers_semantic_state(cua_tools: dict[str, FakeTool]) -> None:
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
    finally:
        await run.aclose()

    assert obs.foreground_app == "Google Chrome"
    assert obs.window_title == "WhatsApp"
    assert obs.url == "https://web.whatsapp.com/"
    assert obs.focused_element == "message input"
    assert [t.id for t in obs.targets] == ["tok-1", "tok-2", "tok-3"]
    window_args = cua_tools["get_window_state"].calls[0]
    assert window_args["include_screenshot"] is False
    assert window_args["pid"] == 4242 and window_args["window_id"] == 7
    assert adapter.screenshot_count == 0
    assert cua_tools["get_accessibility_tree"].calls == []


async def test_observe_falls_back_to_accessibility_tree(
    cua_tools: dict[str, FakeTool],
) -> None:
    cua_tools["get_window_state"].outcome = ok_outcome({"window": {"title": "x"}})
    cua_tools["get_accessibility_tree"].outcome = ok_outcome(
        {
            "elements": [
                {"role": "button", "label": "Rahul"},
                {"role": "button", "label": "Sayan"},
            ]
        }
    )
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
    finally:
        await run.aclose()

    assert [t.id for t in obs.targets] == ["t1", "t2"]
    assert all(t.element_token == "" for t in obs.targets)


async def test_execute_click_resolves_the_element_token(
    cua_tools: dict[str, FakeTool],
) -> None:
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
        result = await adapter.execute(
            act(VeloActionKind.CLICK, target_id="tok-1"), obs, VeloObjective(text="click Rahul")
        )
    finally:
        await run.aclose()

    assert result.status == "ok"
    assert cua_tools["click"].calls == [{"element_token": "tok-1"}]


async def test_execute_types_the_exact_payload(cua_tools: dict[str, FakeTool]) -> None:
    payload = "I will call you later  "  # trailing spaces: bytes must survive
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
        objective = VeloObjective(text="send", user_text_payloads={"user_text_1": payload})
        result = await adapter.execute(
            act(VeloActionKind.TYPE_USER_TEXT, target_id="tok-3", payload_id="user_text_1"),
            obs,
            objective,
        )
    finally:
        await run.aclose()

    assert result.status == "ok"
    call = cua_tools["type_text"].calls[0]
    assert call["text"] == "I will call you later  "
    assert call["element_token"] == "tok-3"


async def test_execute_launch_app_maps_through_the_manifest(
    cua_tools: dict[str, FakeTool],
) -> None:
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
        result = await adapter.execute(
            act(VeloActionKind.LAUNCH_APP, app_name="Chrome"), obs, VeloObjective(text="open")
        )
    finally:
        await run.aclose()

    assert result.status == "ok"
    assert cua_tools["launch_app"].calls == [{"bundle_id": "com.google.Chrome"}]


async def test_execute_rejects_apps_outside_the_manifest(
    cua_tools: dict[str, FakeTool],
) -> None:
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
        with pytest.raises(VeloCuaError):
            await adapter.execute(
                act(VeloActionKind.LAUNCH_APP, app_name="Finder"), obs, VeloObjective(text="open")
            )
    finally:
        await run.aclose()
    assert cua_tools["launch_app"].calls == []


async def test_unknown_outcomes_fail_closed(cua_tools: dict[str, FakeTool]) -> None:
    cua_tools["click"].outcome = ToolOutcome(status="ok", effect="unverifiable")
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
        result = await adapter.execute(
            act(VeloActionKind.CLICK, target_id="tok-1"), obs, VeloObjective(text="click")
        )
    finally:
        await run.aclose()
    assert result.status == "unknown"

    cua_tools["click"].outcome = ToolOutcome(status="ok", effect="suspected_noop")
    adapter2, run2 = build_adapter(cua_tools)
    try:
        obs2 = await adapter2.observe()
        result2 = await adapter2.execute(
            act(VeloActionKind.CLICK, target_id="tok-1"), obs2, VeloObjective(text="click")
        )
    finally:
        await run2.aclose()
    assert result2.status == "unknown"


async def test_driver_errors_surface_as_failed_not_crashes(
    cua_tools: dict[str, FakeTool],
) -> None:
    cua_tools["click"].error = RuntimeError("driver exploded")
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
        result = await adapter.execute(
            act(VeloActionKind.CLICK, target_id="tok-1"), obs, VeloObjective(text="click")
        )
    finally:
        await run.aclose()
    assert result.status == "failed"
    assert "driver exploded" in result.detail


async def test_cancelled_run_starts_no_tool_call(cua_tools: dict[str, FakeTool]) -> None:
    adapter, run = build_adapter(cua_tools)
    try:
        run.cancel()
        with pytest.raises(DesktopRunCancelled):
            await adapter.observe()
        with pytest.raises(DesktopRunCancelled):
            await adapter.execute(
                act(VeloActionKind.CLICK, target_id="tok-1"),
                observation(*[target("tok-1", "Rahul")]),
                VeloObjective(text="click"),
            )
    finally:
        await run.aclose()
    assert all(tool.calls == [] for tool in cua_tools.values())


async def test_budget_ceiling_fails_closed(cua_tools: dict[str, FakeTool]) -> None:
    from assistant.agent.context import CuaBudgetExceeded

    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
        with pytest.raises(CuaBudgetExceeded):
            for _ in range(12):
                await adapter.execute(
                    act(VeloActionKind.CLICK, target_id="tok-1"), obs, VeloObjective(text="click")
                )
    finally:
        await run.aclose()


async def test_verify_reports_scene_change(cua_tools: dict[str, FakeTool]) -> None:
    adapter, run = build_adapter(cua_tools)
    try:
        before = await adapter.observe()
        await adapter.execute(
            act(VeloActionKind.CLICK, target_id="tok-1"), before, VeloObjective(text="click")
        )
        cua_tools["get_window_state"].outcome = ok_outcome(
            {**WINDOW_STATE, "focused_element": "tok-3"}
        )
        verification = await adapter.verify(act(VeloActionKind.CLICK, target_id="tok-1"), before)
    finally:
        await run.aclose()
    assert verification.changed
    assert verification.observation.focused_element == "tok-3"


def test_allowed_apps_parse_from_the_real_manifest() -> None:
    allowed = allowed_apps_from_manifest("config/cua-capabilities.yaml")
    assert allowed == {
        "com.apple.calculator": "calculator",
        "com.google.Chrome": "Chrome",
        "com.apple.Terminal": "Terminal",
        "com.apple.TextEdit": "TextEdit",
    }


def test_missing_manifest_fails_closed() -> None:
    with pytest.raises(VeloCuaError):
        allowed_apps_from_manifest("/nonexistent/manifest.yaml")


async def test_scrolling_bare_and_targeted(cua_tools: dict[str, FakeTool]) -> None:
    adapter, run = build_adapter(cua_tools)
    try:
        obs = await adapter.observe()
        bare = await adapter.execute(
            act(VeloActionKind.SCROLL, direction="down"), obs, VeloObjective(text="scroll")
        )
        targeted = await adapter.execute(
            act(VeloActionKind.SCROLL, direction="down", target_id="tok-1"),
            obs,
            VeloObjective(text="scroll"),
        )
    finally:
        await run.aclose()

    assert bare.status == "ok"
    assert targeted.status == "ok"
    first, second = cua_tools["scroll"].calls
    assert first == {"direction": "down", "amount": 5}
    assert second == {"direction": "down", "amount": 5}  # element_token schema-filtered


async def test_sani_is_never_the_observed_desktop_target(cua_tools) -> None:
    """Sani's pill and panel are always-on-top, so Sani is usually frontmost.

    Observing itself returns no actionable elements, so JEV kept deciding
    OBSERVE and the cursor never moved.
    """
    cua_tools["list_apps"].outcome = ok_outcome(
        {
            "apps": [
                {
                    "pid": 7,
                    "bundle_id": "app.sani.local",
                    "name": "Sani",
                    "frontmost": True,
                    "windows": [{"window_id": 8, "title": "Sani"}],
                },
                *APPS_STATE["apps"],
            ]
        }
    )
    adapter, _ = build_adapter(cua_tools)

    result = await adapter.observe()

    assert cua_tools["get_window_state"].calls[-1]["pid"] == 4242
    assert result.foreground_app == "Google Chrome"


#: What the packaged driver actually returns from `list_apps`: an `active`
#: flag rather than `frontmost`, and an empty `windows` list for every
#: application. The earlier fixture invented both fields, which is why the
#: unit tests passed while observation could never succeed on a real machine.
REAL_APPS_STATE = {
    "apps": [
        {
            "pid": 4242,
            "bundle_id": "com.google.Chrome",
            "name": "Google Chrome",
            "kind": "desktop",
            "active": True,
            "running": True,
            "windows": [],
        },
        {
            "pid": 100,
            "bundle_id": "com.apple.Terminal",
            "name": "Terminal",
            "kind": "desktop",
            "active": False,
            "running": True,
            "windows": [],
        },
    ]
}

REAL_WINDOWS_STATE = {
    "windows": [
        {
            "window_id": 1971,
            "app_name": "Google Chrome",
            "is_on_screen": False,
            "bounds": {"x": 99.0, "y": 58.0, "width": 1254.0, "height": 138.0},
        },
        {
            "window_id": 2002,
            "app_name": "Google Chrome",
            "is_on_screen": True,
            "bounds": {"x": 0.0, "y": 25.0, "width": 1470.0, "height": 874.0},
        },
    ]
}


async def test_window_id_comes_from_list_windows(cua_tools) -> None:
    """`get_window_state` refuses a snapshot without a window id.

    Observation used to read the id off `list_apps`, which never reports one,
    so every snapshot failed and the loop could only ever decide OBSERVE.
    """
    cua_tools["list_apps"].outcome = ok_outcome(REAL_APPS_STATE)
    cua_tools["list_windows"] = FakeTool(
        "list_windows", ok_outcome(REAL_WINDOWS_STATE), arg_names=("pid",)
    )
    adapter, _ = build_adapter(cua_tools)

    result = await adapter.observe()

    assert cua_tools["list_windows"].calls[-1]["pid"] == 4242
    assert cua_tools["get_window_state"].calls[-1]["window_id"] == 2002
    assert result.foreground_app == "Google Chrome"


async def test_observation_survives_an_app_with_no_windows(cua_tools) -> None:
    """No window id must degrade to the tree fallback, not raise."""
    cua_tools["list_apps"].outcome = ok_outcome(REAL_APPS_STATE)
    cua_tools["list_windows"] = FakeTool("list_windows", ok_outcome({"windows": []}))
    cua_tools["get_window_state"].error = VeloCuaError("window_id required")
    adapter, _ = build_adapter(cua_tools)

    result = await adapter.observe()

    assert "window_id" not in cua_tools["get_window_state"].calls[-1]
    assert result.targets == ()


#: Trimmed from a real `list_windows` response for a running Chrome: ten
#: windows, one visible, the rest off-screen or 1x1 placeholders. Recorded
#: verbatim from the packaged driver so the selection logic is tested against
#: the payload it actually has to survive.
CAPTURED_WINDOWS = [
    {"window_id": 1971, "is_on_screen": False, "title": "",
     "bounds": {"x": 99.0, "y": 58.0, "width": 1254.0, "height": 138.0}},
    {"window_id": 1970, "is_on_screen": False, "title": "",
     "bounds": {"x": 99.0, "y": 58.0, "width": 1263.0, "height": 138.0}},
    {"window_id": 1969, "is_on_screen": True, "title": "",
     "bounds": {"x": 0.0, "y": 25.0, "width": 1470.0, "height": 849.0}},
    {"window_id": 1590, "is_on_screen": False, "title": "",
     "bounds": {"x": 0.0, "y": 25.0, "width": 495.0, "height": 98.0}},
    {"window_id": 1973, "is_on_screen": False, "title": "",
     "bounds": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}},
    {"window_id": 1598, "is_on_screen": False, "title": "",
     "bounds": {"x": 0.0, "y": 0.0, "width": 64.0, "height": 64.0}},
    "not-a-dict",
]


def test_visible_window_wins_over_larger_offscreen_placeholders() -> None:
    from assistant.velo.cua_adapter import _pick_window_id

    assert _pick_window_id(CAPTURED_WINDOWS) == 1969


def test_window_selection_falls_back_and_survives_junk() -> None:
    from assistant.velo.cua_adapter import _pick_window_id

    assert _pick_window_id([]) is None
    assert _pick_window_id(["nope", 7]) is None
    # Nothing on screen: the biggest window is still the best available guess.
    offscreen = [w for w in CAPTURED_WINDOWS if isinstance(w, dict) and not w["is_on_screen"]]
    assert _pick_window_id(offscreen) == 1970
    # A window record with no usable id must not raise.
    assert _pick_window_id([{"bounds": {"width": 10, "height": 10}}]) is None


def test_fallback_target_is_allowlisted_and_recent_not_just_first(cua_tools) -> None:
    """Nothing is frontmost for long stretches, so the fallback is the norm.

    Driver order puts a disallowed browser first; the allowlist and then
    recency have to decide.
    """
    from assistant.velo.cua_adapter import _fallback_app

    apps = [
        {"pid": 532, "bundle_id": "com.apple.Safari", "name": "Safari",
         "running": True, "active": False, "last_used": "2026-09-23T20:00:00Z"},
        {"pid": 4242, "bundle_id": "com.google.Chrome", "name": "Google Chrome",
         "running": True, "active": False, "last_used": "2026-09-23T19:00:00Z"},
        {"pid": 100, "bundle_id": "com.apple.Terminal", "name": "Terminal",
         "running": True, "active": False, "last_used": "2026-09-23T21:00:00Z"},
    ]
    allowed = {"com.google.Chrome": "Chrome", "com.apple.Terminal": "Terminal"}
    chosen = _fallback_app(apps, allowed)
    assert chosen is not None and chosen["bundle_id"] == "com.apple.Terminal"
    # With no allowlist configured, a running app is still returned.
    assert _fallback_app(apps, {})["bundle_id"] == "com.apple.Safari"
    assert _fallback_app([], allowed) is None


#: Argument names and required fields as reported by `cua-driver describe
#: <tool>` against the pinned 0.28.2 binary. `_filter_kwargs` drops any key the
#: schema does not declare, so a name that is merely wrong disappears and the
#: call goes out missing its required fields -- which is exactly how the
#: `combo` hotkey argument failed for every HOTKEY decision.
DRIVER_SCHEMAS = {
    "launch_app": {"bundle_id", "name", "urls", "additional_arguments"},
    "click": {"pid", "window_id", "element_token", "element_index", "snapshot_id",
              "x", "y", "button", "count", "action", "delivery_mode", "scope",
              "session", "target", "from_zoom", "modifier", "debug_image_out"},
    "type_text": {"pid", "window_id", "text", "element_token", "element_index",
                  "snapshot_id", "x", "y", "delay_ms", "delivery_mode", "scope",
                  "session", "target"},
    "press_key": {"pid", "window_id", "key", "modifiers", "element_token",
                  "element_index", "snapshot_id", "x", "y", "delivery_mode",
                  "scope", "session", "target"},
    "hotkey": {"pid", "window_id", "keys", "element_token", "element_index",
               "snapshot_id", "x", "y", "delivery_mode", "scope", "session", "target"},
    "scroll": {"pid", "window_id", "direction", "amount", "by", "element_token",
               "element_index", "snapshot_id", "x", "y", "delivery_mode", "scope",
               "session", "target"},
}
DRIVER_REQUIRED = {"type_text": {"text"}, "press_key": {"key"}, "hotkey": {"keys"},
                   "scroll": {"direction"}, "click": set(), "launch_app": set()}


async def test_emitted_arguments_exist_in_the_driver_schema(cua_tools) -> None:
    adapter, _ = build_adapter(cua_tools)
    obs = observation(target("tok-1", "Rahul", token="tok-1"))
    cases = [
        (act(VeloActionKind.LAUNCH_APP, app_name="Chrome"), set()),
        (act(VeloActionKind.CLICK, target_id="tok-1"), {"element_token"}),
        (act(VeloActionKind.PRESS_KEY, key="Return"), {"key"}),
        (act(VeloActionKind.HOTKEY, combo="cmd+t"), {"keys"}),
        (act(VeloActionKind.SCROLL, direction="down"), {"direction"}),
    ]
    for decision, expected in cases:
        tool_name, kwargs = adapter._build_tool_call(decision, obs, None)
        assert kwargs, f"{tool_name} emitted no arguments"
        assert set(kwargs) <= DRIVER_SCHEMAS[tool_name], (
            f"{tool_name} sent undeclared keys {set(kwargs) - DRIVER_SCHEMAS[tool_name]}"
        )
        assert set(kwargs) >= expected | DRIVER_REQUIRED[tool_name], (
            f"{tool_name} missing required keys {expected - set(kwargs)}"
        )


async def test_hotkey_combo_is_split_into_key_list(cua_tools) -> None:
    adapter, _ = build_adapter(cua_tools)
    obs = observation(target("tok-1", "Rahul", token="tok-1"))
    tool_name, kwargs = adapter._build_tool_call(
        act(VeloActionKind.HOTKEY, combo="cmd+shift+4"), obs, None
    )
    assert tool_name == "hotkey"
    assert kwargs == {"keys": ["cmd", "shift", "4"]}


async def test_click_falls_back_to_pixels_when_axpress_is_unsupported(cua_tools) -> None:
    """TextEdit's document view does not implement AXPress (-25206).

    An accessibility click there reports failure and nothing happens, which is
    indistinguishable from "permissions are broken" unless the adapter retries
    on the pixel rung.
    """
    from assistant.velo.types import VeloTarget

    cua_tools["list_apps"].outcome = ok_outcome(REAL_APPS_STATE)
    cua_tools["list_windows"] = FakeTool(
        "list_windows", ok_outcome(REAL_WINDOWS_STATE), arg_names=("pid",)
    )
    click_tool = cua_tools["click"]
    click_tool.args = {k: None for k in
                       ("element_token", "pid", "window_id", "x", "y", "delivery_mode")}
    click_tool.outcome = ToolOutcome(
        status="failed",
        effect="unverifiable",
        text="AX action failed: AXUIElementPerformAction(AXPress) returned -25206",
    )
    adapter, _ = build_adapter(cua_tools)
    await adapter.observe()  # records the focused pid/window

    framed = observation(
        VeloTarget(id="tok-1", role="textbox", label="body",
                   element_token="tok-1", frame=(246.0, 171.0, 100.0, 40.0))
    )
    objective = VeloObjective(text="type into the document")
    result = await adapter.execute(
        act(VeloActionKind.CLICK, target_id="tok-1"), framed, objective
    )

    assert len(click_tool.calls) == 2, "no pixel retry happened"
    retry = click_tool.calls[1]
    # On-screen window 2002 starts at (0, 25); the element centre is global
    # (296, 191), so the window-local point is (296, 166).
    assert (retry["x"], retry["y"]) == (296, 166)
    assert retry["delivery_mode"] == "foreground"
    assert retry["pid"] == 4242 and retry["window_id"] == 2002
    assert result.status == "failed"  # the driver still refused; only the aim changed
