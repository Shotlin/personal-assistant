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
