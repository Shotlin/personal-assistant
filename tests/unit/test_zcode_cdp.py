"""ZCode through its own window: client, launcher, page reading, contract, snapshot.

Nothing here talks to a real ZCode. The page text fixtures were captured from a real
ZCode 3.14.4 on 2026-10-04 (tests/fixtures/zcode/provider-panels.json); everything else is a
scripted fake of the debug port, so these tests prove Sani's handling, not ZCode's behaviour.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from assistant.coding_agents.zcode_cdp import pages
from assistant.coding_agents.zcode_cdp.client import CdpClient, CdpError, find_page_url
from assistant.coding_agents.zcode_cdp.contract import (
    ContractResult,
    check_contract,
    wait_for_contract,
)
from assistant.coding_agents.zcode_cdp.launcher import ZCodeLauncher
from assistant.coding_agents.zcode_cdp.pages import (
    PageAdapter,
    parse_balances,
    parse_model_testid,
    parse_models,
    summarize_sessions,
)
from assistant.coding_agents.zcode_cdp.reader import (
    read_snapshot,
    save_snapshot,
    saved_overview,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "zcode"
PANELS: dict[str, str] = json.loads((FIXTURES / "provider-panels.json").read_text())
START_PLAN = PANELS["model-provider-nav-item-coding-plan:account:zai-start-plan"]
CODING_PLAN = PANELS["model-provider-nav-item-preset:account:zai-start-plan"]


async def no_sleep(_: float) -> None:
    return None


# -- parsing real captured text ---------------------------------------------------------


def test_balances_are_read_exactly_as_displayed() -> None:
    items = parse_balances(START_PLAN)
    assert [(i["plan"], i["model"], i["remaining"], i["total"]) for i in items] == [
        ("ZCode Trust Build", "GLM-5.3-Flash", 100_000_000, 100_000_000),
        ("ZCode Start Plan", "GLM-5.3", 3_000_000, 3_000_000),
        ("ZCode Start Plan", "GLM-5.3-Flash", 5_000_000, 5_000_000),
    ]
    assert items[0]["expires"] == "Expires Oct 4, 21:30" and items[0]["reset"] == "21:30"
    assert items[1]["percent"] == 100


def test_a_provider_without_a_plan_yields_no_balances() -> None:
    assert parse_balances(CODING_PLAN) == []


def test_incomplete_or_impossible_figures_are_dropped_not_completed() -> None:
    text = (
        "Plan A\nExpires soon\nToday's balance\n"
        "M1\n50%\n10:00\n9 / 5\n"  # more left than the total: impossible
        "M2\n50%\n10:00\n"  # no figures at all
        "M3\n40%\n11:00\n2 / 5\n"
    )
    items = parse_balances(text)
    assert [(i["model"], i["remaining"], i["total"], i["percent"]) for i in items] == [
        ("M3", 2, 5, 40)
    ]


def test_model_test_ids_carry_the_plan() -> None:
    parts = parse_model_testid(
        "chat-model-select-item-custom:account%3Azai-start-plan:GLM-5.3-Flash"
    )
    assert parts == {
        "provider": "custom",
        "plan_id": "account:zai-start-plan",
        "model": "GLM-5.3-Flash",
    }
    assert parse_model_testid("chat-model-select-item-broken") is None
    assert parse_model_testid("something-else:a:b") is None


def test_models_keep_current_flag_and_plan_label() -> None:
    models = parse_models(
        [
            {
                "testid": "chat-model-select-item-custom:account%3Azai-start-plan:GLM-5.3",
                "checked": True,
                "group": "Start Plan\nFree",
            },
            {"testid": "junk", "checked": False, "group": ""},
        ]
    )
    assert models == [
        {
            "provider": "custom",
            "plan_id": "account:zai-start-plan",
            "model": "GLM-5.3",
            "plan": "Start Plan",
            "current": True,
        }
    ]


def test_sessions_are_incomplete_when_a_project_is_collapsed() -> None:
    raw: list[dict[str, Any]] = [
        {"path": "/a", "name": "a", "expanded": "true", "tasks": [{"id": "s1", "title": "t"}]},
        {"path": "/b", "name": "b", "expanded": "false", "tasks": []},
    ]
    summary = summarize_sessions(raw)
    assert summary["count"] == 1 and summary["complete"] is False
    raw[1]["expanded"] = "true"
    assert summarize_sessions(raw)["complete"] is True


def test_secret_shaped_session_titles_are_screened() -> None:
    raw = [
        {
            "path": "/a",
            "name": "a",
            "expanded": "true",
            "tasks": [{"id": "s", "title": "use ghp_" + "a" * 30}],
        }
    ]
    assert "ghp_" not in summarize_sessions(raw)["projects"][0]["tasks"][0]["title"]


def test_required_selectors_are_all_in_the_discovery_fixture() -> None:
    """The contract's controls are the ones mapped by hand in S1 (fixture contract test)."""
    mapped = (
        Path(__file__).parents[2] / "docs/verification/zcode-cdp/fixtures/composer-controls.json"
    ).read_text()
    skeleton = mapped + "sidebar login-trigger task-settings-button workspace-list"
    for name, selector in pages.REQUIRED.items():
        testid = selector.split("=", 1)[1].rstrip("]")
        assert testid in skeleton, f"{name}: {testid} was never seen in discovery"


# -- CDP client over a fake socket -------------------------------------------------------


class FakeTransport:
    """Answers each request with the next scripted reply; can inject an event first."""

    def __init__(self, replies: list[dict[str, Any]] | None = None) -> None:
        self.replies = list(replies or [])
        self.sent: list[dict[str, Any]] = []
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False

    async def send(self, data: str) -> None:
        message = json.loads(data)
        self.sent.append(message)
        if self.replies:
            reply = self.replies.pop(0)
            if reply.get("_event"):
                await self._inbox.put(json.dumps({"method": "Page.loadEventFired"}))
                reply = {k: v for k, v in reply.items() if k != "_event"}
            await self._inbox.put(json.dumps({"id": message["id"], **reply}))

    async def recv(self) -> str:
        return await self._inbox.get()

    async def close(self) -> None:
        self.closed = True


async def test_evaluate_returns_the_value_and_ignores_events() -> None:
    transport = FakeTransport([{"_event": True, "result": {"result": {"value": 42}}}])
    client = CdpClient(transport)
    assert await client.evaluate("1+41") == 42
    assert transport.sent[0]["method"] == "Runtime.evaluate"
    assert transport.sent[0]["params"]["returnByValue"] is True
    assert client.events and client.events[0]["method"] == "Page.loadEventFired"
    await client.close()
    assert transport.closed


async def test_page_exceptions_and_protocol_errors_are_raised() -> None:
    transport = FakeTransport(
        [
            {"result": {"exceptionDetails": {"exception": {"description": "TypeError: x"}}}},
            {"error": {"message": "no such method"}},
        ]
    )
    client = CdpClient(transport)
    with pytest.raises(CdpError, match="TypeError"):
        await client.evaluate("boom")
    with pytest.raises(CdpError, match="no such method"):
        await client.call("Nope.nothing")
    await client.close()


async def test_a_silent_page_times_out() -> None:
    client = CdpClient(FakeTransport())
    with pytest.raises(CdpError, match="timed out"):
        await client.call("Runtime.evaluate", timeout=0.05)
    await client.close()


async def test_click_sends_real_mouse_events_at_the_centre() -> None:
    transport = FakeTransport([{"result": {"result": {"value": [10, 20]}}}] + [{"result": {}}] * 3)
    client = CdpClient(transport)
    await client.click("[data-testid=x]")
    assert "scrollIntoView" in transport.sent[0]["params"]["expression"]
    mouse = [m for m in transport.sent if m["method"] == "Input.dispatchMouseEvent"]
    assert [m["params"]["type"] for m in mouse] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert all(m["params"]["x"] == 10 and m["params"]["y"] == 20 for m in mouse)
    await client.close()


async def test_click_on_a_missing_element_raises() -> None:
    client = CdpClient(FakeTransport([{"result": {"result": {"value": None}}}]))
    with pytest.raises(CdpError, match="nothing to click"):
        await client.click("[data-testid=gone]")
    await client.close()


async def test_a_dropped_connection_fails_waiting_calls() -> None:
    transport = FakeTransport()
    client = CdpClient(transport)
    task = asyncio.create_task(client.call("Runtime.evaluate", timeout=2))
    await asyncio.sleep(0)
    await transport._inbox.put("not json")  # reader loop dies on garbage
    with pytest.raises(CdpError, match="closed"):
        await task
    await client.close()


async def test_only_a_local_debug_address_is_ever_used() -> None:
    with pytest.raises(CdpError, match="not on this Mac"):
        await CdpClient.open("ws://10.0.0.5:9222/devtools/page/1")


async def test_the_app_page_is_found_among_workers_and_waited_for() -> None:
    calls: list[int] = []

    async def get(_: str) -> Any:
        calls.append(1)
        if len(calls) < 3:
            return [{"type": "worker", "url": "x", "webSocketDebuggerUrl": "ws://w"}]
        return [
            {"type": "worker", "url": "x", "webSocketDebuggerUrl": "ws://w"},
            {
                "type": "page",
                "url": "file:///A/ZCode.app/out/renderer/index.html?restore",
                "webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/page/9",
            },
        ]

    assert await find_page_url(1, get, sleep=no_sleep) == "ws://127.0.0.1:1/devtools/page/9"

    async def never(_: str) -> Any:
        return []

    with pytest.raises(CdpError, match="not found"):
        await find_page_url(1, never, seconds=1, sleep=no_sleep)


# -- launcher ----------------------------------------------------------------------------


class FakeSystem:
    """A pretend macOS: which processes run, what lsof says, which commands were run."""

    def __init__(self, *, running: bool = False, lsof: str | None = None) -> None:
        self.running = running
        self.lsof = lsof
        self.commands: list[list[str]] = []
        self.version_ready = True

    async def run(self, command: list[str]) -> tuple[int, str]:
        self.commands.append(command)
        if command[0] == "pgrep":
            return (0, "111\n") if self.running else (1, "")
        if command[0] == "osascript":
            self.running = False
            return 0, ""
        if command[0] == "open":
            self.running = True
            return 0, ""
        if command[0] == "lsof":
            port = next(a for a in command if a.startswith("-iTCP:")).split(":")[1]
            return (0, self.lsof.replace("PORT", port)) if self.lsof else (1, "")
        return 1, ""

    async def get(self, url: str) -> Any:
        if not self.version_ready:
            raise OSError("not yet")
        return {"User-Agent": "Mozilla/5.0 ZCode/3.14.4 Chrome/146 Electron/41"}


def launcher_for(system: FakeSystem, tmp_path: Path) -> ZCodeLauncher:
    app = tmp_path / "ZCode.app"
    app.mkdir()
    return ZCodeLauncher(
        run=system.run,
        get=system.get,
        pick_port=lambda: 23456,
        sleep=no_sleep,
        app_path=app,
    )


GOOD_LSOF = "p111\nn127.0.0.1:PORT\n"


async def test_starts_with_a_private_port_and_verifies_the_owner(tmp_path: Path) -> None:
    system = FakeSystem(lsof=GOOD_LSOF)
    launcher = launcher_for(system, tmp_path)
    system.running = False
    port = await launcher.start_with_port()
    assert port == 23456
    opened = [c for c in system.commands if c[0] == "open"]
    assert opened == [["open", "-a", "ZCode", "--args", "--remote-debugging-port=23456"]]
    assert await launcher.version() == "3.14.4"


async def test_refuses_to_start_while_zcode_is_running(tmp_path: Path) -> None:
    system = FakeSystem(running=True, lsof=GOOD_LSOF)
    with pytest.raises(CdpError, match="already running"):
        await launcher_for(system, tmp_path).start_with_port()
    assert not [c for c in system.commands if c[0] == "open"]


@pytest.mark.parametrize(
    ("lsof", "message"),
    [
        ("p999\nn127.0.0.1:PORT\n", "not owned by ZCode"),
        ("p111\nn*:PORT\n", "beyond this Mac"),
        (None, "could not confirm"),
    ],
)
async def test_a_port_that_is_not_ours_is_refused(
    tmp_path: Path, lsof: str | None, message: str
) -> None:
    system = FakeSystem(lsof=lsof)
    launcher = launcher_for(system, tmp_path)
    system.running = False
    original = system.run

    async def run(command: list[str]) -> tuple[int, str]:
        code, out = await original(command)
        if command[0] == "open":  # the app is "running" as pid 111 afterwards
            system.running = True
        return code, out

    launcher.run = run
    with pytest.raises(CdpError, match=message):
        await launcher.start_with_port()


async def test_restore_quits_then_opens_normally(tmp_path: Path) -> None:
    system = FakeSystem(running=True)
    launcher = launcher_for(system, tmp_path)
    launcher.port = 23456
    assert await launcher.restore_normal() is True
    assert system.commands[-1] == ["open", "-a", "ZCode"]
    assert launcher.port == 0


async def test_a_zcode_that_will_not_quit_is_not_force_killed(tmp_path: Path) -> None:
    system = FakeSystem(running=True)

    async def stubborn(command: list[str]) -> tuple[int, str]:
        if command[0] == "osascript":
            system.commands.append(command)
            return 0, ""  # still running afterwards
        return await FakeSystem.run(system, command)

    launcher = launcher_for(system, tmp_path)
    launcher.run = stubborn
    assert await launcher.restore_normal() is False
    assert not [c for c in system.commands if c[0] in {"kill", "pkill"}]


# -- page adapter over a scripted page ----------------------------------------------------


class FakePage:
    """Answers scripts by their identity and records clicks; no browser."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.clicks: list[str] = []
        self.keys: list[str] = []
        self.back_visible = False

    async def evaluate(self, expression: str, *, timeout: float = 10.0) -> Any:
        if expression == pages.JS_BACK:
            return self.back_visible
        for script, value in self.answers.items():
            if expression == script:
                return value(self) if callable(value) else value
        raise AssertionError(f"unexpected script: {expression[:60]}")

    async def click(self, selector: str) -> None:
        self.clicks.append(selector)
        if selector == pages.SETTINGS_BUTTON:
            self.back_visible = True
        if selector == pages.BACK_SELECTOR:
            self.back_visible = False

    async def press(self, key: str) -> None:
        self.keys.append(key)


async def test_models_modes_and_levels_are_read_and_menus_closed() -> None:
    model_items = [
        {
            "testid": "chat-model-select-item-custom:account%3Azai-start-plan:GLM-5.3-Flash",
            "checked": True,
            "group": "Start Plan",
        }
    ]
    page = FakePage(
        {
            pages.JS_STATUS: {"model": "GLM-5.3-Flash", "account": "A"},
            pages.JS_MODEL_ITEMS: model_items,
            pages.JS_MODE_ITEMS: [
                {"testid": "chat-mode-select-item-edit", "checked": True, "text": "Edit\nx"}
            ],
            pages.JS_REASONING_ITEMS: [
                {"testid": "chat-thought-level-select-item-max", "checked": True, "text": "Max"}
            ],
        }
    )
    result = await PageAdapter(page, sleep=no_sleep).models()
    assert result["current_model"] == "GLM-5.3-Flash"
    assert result["models"][0]["plan_id"] == "account:zai-start-plan"
    assert result["modes"] == [{"id": "edit", "label": "Edit", "current": True}]
    assert result["reasoning"] == [{"id": "max", "label": "Max", "current": True}]
    assert page.keys == ["Escape"] * 3  # every menu was closed again


async def test_balances_walk_every_plan_page_and_always_leave_settings() -> None:
    nav = [
        {"testid": "model-provider-nav-item-preset:account:zai-start-plan", "text": "Z.ai"},
        {
            "testid": "model-provider-nav-item-coding-plan:account:zai-start-plan",
            "text": "Start Plan",
        },
        {"testid": "model-provider-nav-item-custom:new-provider", "text": "New provider"},
    ]
    shown: list[str] = []

    def panel(page: FakePage) -> str:
        last = page.clicks[-1]
        shown.append(last)
        return CODING_PLAN if "preset:" in last else START_PLAN

    page = FakePage({pages.JS_PROVIDER_NAV: nav, pages.JS_PROVIDER_PANEL: panel})
    result = await PageAdapter(page, sleep=no_sleep).balances()
    assert len(result["items"]) == 3
    assert result["items"][0]["provider"] == "account:zai-start-plan"
    assert result["not_read"] == ["Z.ai: Not subscribed, enabled after subscription"]
    assert len(shown) == 2  # the custom provider is not opened
    assert page.clicks[-1] == pages.BACK_SELECTOR and page.back_visible is False


async def test_a_failed_balance_read_still_leaves_settings() -> None:
    def boom(_: FakePage) -> Any:
        raise CdpError("page went away")

    page = FakePage({pages.JS_PROVIDER_NAV: boom})
    with pytest.raises(CdpError):
        await PageAdapter(page, sleep=no_sleep).balances()
    assert page.clicks[-1] == pages.BACK_SELECTOR


async def test_sessions_open_collapsed_projects_then_close_them_again() -> None:
    state = {"b_open": False, "more": True}

    def sessions(_: FakePage) -> list[dict[str, Any]]:
        return [
            {
                "path": "/a",
                "name": "a",
                "expanded": "true",
                "tasks": [{"id": "s1", "title": "t1", "age": "1h"}],
            },
            {
                "path": "/b",
                "name": "b",
                "expanded": "true" if state["b_open"] else "false",
                "tasks": [{"id": "s2", "title": "t2", "age": "2h"}] if state["b_open"] else [],
            },
        ]

    page = FakePage({pages.JS_SESSIONS: sessions})
    original_click = page.click

    async def click(selector: str) -> None:
        await original_click(selector)
        if "workspace-item-/b" in selector:
            state["b_open"] = not state["b_open"]
        if selector == pages.MORE_SELECTOR:
            state["more"] = False

    page.click = click  # type: ignore[method-assign]
    page.answers[pages.JS_MARK_SHOW_MORE] = lambda _p: state["more"]
    result = await PageAdapter(page, sleep=no_sleep).sessions()
    assert result["count"] == 2 and result["complete"] is True
    assert state["b_open"] is False  # put back the way it was
    assert pages.MORE_SELECTOR in page.clicks


async def test_signed_out_account_is_not_a_name() -> None:
    page = FakePage({pages.JS_STATUS: {"account": "Sign in"}})
    assert await PageAdapter(page, sleep=no_sleep).account() == {"name": "", "signed_in": False}
    page = FakePage({pages.JS_STATUS: {"account": "Agnix MStudio"}})
    assert (await PageAdapter(page, sleep=no_sleep).account())["signed_in"] is True


# -- contract ----------------------------------------------------------------------------


def present_script() -> str:
    return pages.JS_PRESENT.replace("__SELECTORS__", json.dumps(pages.REQUIRED))


async def test_contract_names_what_is_missing_and_refuses() -> None:
    found = {name: True for name in pages.REQUIRED} | {"send": False}
    adapter = PageAdapter(FakePage({present_script(): found}), sleep=no_sleep)
    result = await check_contract(adapter, "3.14.4")
    assert not result.ok and result.missing == ["send"]
    assert "send" in result.refusal() and "does not look" in result.refusal()


async def test_an_unverified_version_is_flagged_but_not_silently_trusted() -> None:
    found = {name: True for name in pages.REQUIRED}
    adapter = PageAdapter(FakePage({present_script(): found}), sleep=no_sleep)
    result = await check_contract(adapter, "9.9.9")
    assert result.ok and result.version_verified is False
    assert result.as_dict()["version_verified"] is False


async def test_contract_waits_for_the_app_to_draw_itself() -> None:
    answers = [{}, {}, {name: True for name in pages.REQUIRED}]

    class Draws(FakePage):
        async def evaluate(self, expression: str, *, timeout: float = 10.0) -> Any:
            return answers.pop(0) if len(answers) > 1 else answers[0]

    result = await wait_for_contract(
        PageAdapter(Draws({}), sleep=no_sleep), "3.14.4", sleep=no_sleep
    )
    assert result.ok


# -- reader ------------------------------------------------------------------------------


class FakeStore:
    def __init__(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}

    async def put_state(self, key: str, value: dict[str, Any]) -> None:
        self.state[key] = value

    async def get_state(self, key: str) -> dict[str, Any]:
        return self.state.get(key, {})

    async def delete_state(self, key: str) -> None:
        self.state.pop(key, None)

    async def forget_prefix(self, prefix: str) -> int:
        return 0


class FakeLauncher:
    def __init__(self, *, running: bool = False, quits: bool = True) -> None:
        self._running = running
        self._quits = quits
        self.port = 0
        self.calls: list[str] = []

    def installed(self) -> bool:
        return True

    async def running(self) -> bool:
        return self._running

    async def quit(self) -> bool:
        self.calls.append("quit")
        return self._quits

    async def start_with_port(self) -> int:
        self.calls.append("start")
        self.port = 4242
        return 4242

    async def version(self) -> str:
        return "3.14.4"

    async def restore_normal(self) -> bool:
        self.calls.append("restore")
        self.port = 0
        return True


class FakeClient:
    """Stands in for CdpClient; the page answers come from a FakePage-like dict."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    async def evaluate(self, expression: str, *, timeout: float = 10.0) -> Any:
        return await self.page.evaluate(expression)

    async def click(self, selector: str) -> None:
        await self.page.click(selector)

    async def press(self, key: str) -> None:
        await self.page.press(key)

    async def close(self) -> None:
        self.closed = True


def good_page(account: str = "Agnix MStudio", present: dict[str, bool] | None = None) -> FakePage:
    nav = [
        {
            "testid": "model-provider-nav-item-coding-plan:account:zai-start-plan",
            "text": "Start Plan",
        }
    ]
    return FakePage(
        {
            present_script(): present or {name: True for name in pages.REQUIRED},
            pages.JS_STATUS: {"account": account, "model": "GLM-5.3-Flash"},
            pages.JS_MODEL_ITEMS: [
                {
                    "testid": "chat-model-select-item-custom:account%3Azai-start-plan:GLM-5.3",
                    "checked": False,
                    "group": "Start Plan",
                }
            ],
            pages.JS_MODE_ITEMS: [],
            pages.JS_REASONING_ITEMS: [],
            pages.JS_PROVIDER_NAV: nav,
            pages.JS_PROVIDER_PANEL: START_PLAN,
            pages.JS_SESSIONS: [
                {
                    "path": "/a",
                    "name": "a",
                    "expanded": "true",
                    "tasks": [{"id": "s1", "title": "t", "age": "1h"}],
                }
            ],
            pages.JS_MARK_SHOW_MORE: False,
        }
    )


def opener_for(page: FakePage) -> Any:
    async def opener(port: int) -> Any:
        return FakeClient(page)

    return opener


async def test_without_the_users_go_ahead_nothing_is_touched() -> None:
    launcher = FakeLauncher(running=True)
    snap = await read_snapshot(launcher, consent=False, opener=opener_for(good_page()))  # type: ignore[arg-type]
    assert "no go-ahead" in snap.refusal and launcher.calls == []


async def test_a_full_read_closes_the_port_and_keeps_every_part() -> None:
    launcher = FakeLauncher(running=True)
    snap = await read_snapshot(
        launcher,  # type: ignore[arg-type]
        consent=True,
        opener=opener_for(good_page()),
        clock=lambda: 1000.0,
    )
    assert snap.refusal == "" and snap.port_closed is True
    assert launcher.calls == ["quit", "start", "restore"]
    assert snap.account == {"name": "Agnix MStudio", "signed_in": True}
    assert len(snap.balances["items"]) == 3 and snap.sessions["count"] == 1
    store = FakeStore()
    await save_snapshot(store, snap)
    assert set(store.state) == {
        "zcode_contract",
        "zcode_account",
        "zcode_balances",
        "zcode_models",
        "zcode_sessions",
    }
    assert store.state["zcode_account"] == {"name": "Agnix MStudio", "email": "", "as_of": 1000.0}
    overview = await saved_overview(store)
    assert overview is not None and overview["contract"]["ok"] is True
    assert overview["sessions"]["count"] == 1


async def test_a_failing_contract_reads_nothing_and_still_restores_zcode() -> None:
    launcher = FakeLauncher()
    page = good_page(present={name: False for name in pages.REQUIRED})
    snap = await read_snapshot(launcher, consent=True, opener=opener_for(page))  # type: ignore[arg-type]
    assert "does not look" in snap.refusal and snap.account == {} and snap.balances == {}
    assert launcher.calls[-1] == "restore" and snap.port_closed
    store = FakeStore()
    await save_snapshot(store, snap)
    assert "zcode_balances" not in store.state and "zcode_account" not in store.state


async def test_signed_out_is_refused_in_plain_words() -> None:
    launcher = FakeLauncher()
    snap = await read_snapshot(
        launcher,  # type: ignore[arg-type]
        consent=True,
        opener=opener_for(good_page(account="Sign in")),
    )
    assert "signed out" in snap.refusal and snap.balances == {}
    assert launcher.calls[-1] == "restore"


async def test_zcode_that_will_not_close_is_left_alone() -> None:
    launcher = FakeLauncher(running=True, quits=False)
    snap = await read_snapshot(launcher, consent=True, opener=opener_for(good_page()))  # type: ignore[arg-type]
    assert "did not close" in snap.refusal and launcher.calls == ["quit"]


async def test_one_unreadable_part_does_not_hide_the_others() -> None:
    page = good_page()

    def boom(_: FakePage) -> Any:
        raise CdpError("page went away")

    page.answers[pages.JS_PROVIDER_NAV] = boom
    snap = await read_snapshot(FakeLauncher(), consent=True, opener=opener_for(page))  # type: ignore[arg-type]
    assert "balances" in snap.not_read and snap.not_read["balances"].startswith("not read")
    assert snap.models["models"] and snap.sessions["count"] == 1
    store = FakeStore()
    await save_snapshot(store, snap)
    assert "zcode_balances" not in store.state  # never overwritten with nothing


async def test_a_launch_failure_is_reported_and_zcode_is_reopened() -> None:
    class Fails(FakeLauncher):
        async def start_with_port(self) -> int:
            self.calls.append("start")
            self.port = 4242
            raise CdpError("ZCode did not open its debug port in time")

    launcher = Fails()
    snap = await read_snapshot(launcher, consent=True, opener=opener_for(good_page()))  # type: ignore[arg-type]
    assert "debug port" in snap.refusal and launcher.calls[-1] == "restore"


def test_contract_result_without_checks_is_never_ok() -> None:
    assert ContractResult(version="3.14.4").ok is False


# -- background read job and the settings action ------------------------------------------


def snapshot(refusal: str = "", not_read: dict[str, str] | None = None) -> Any:
    from assistant.coding_agents.zcode_cdp.reader import Snapshot

    return Snapshot(
        as_of=5.0,
        refusal=refusal,
        contract={"ok": True, "version": "3.14.4", "version_verified": True},
        account={"name": "Agnix MStudio", "signed_in": True},
        sessions={"count": 2, "projects": [], "complete": True},
        port_closed=True,
        not_read=not_read or {},
    )


async def test_the_read_job_needs_a_yes_and_runs_one_at_a_time() -> None:
    from assistant.coding_agents.zcode_cdp.job import ReadJob

    release = asyncio.Event()
    started: list[int] = []

    async def reader() -> Any:
        started.append(1)
        await release.wait()
        return snapshot()

    job = ReadJob(FakeStore(), reader)
    assert "Confirm" in job.start(confirmed=False) and started == []
    assert job.start(confirmed=True) == "" and job.view()["state"] == "reading"
    assert job.start(confirmed=True) == ""  # a second press joins the first
    await asyncio.sleep(0)
    assert started == [1]
    release.set()
    await job._task  # type: ignore[misc]
    assert job.view()["state"] == "done" and job.view()["port_closed"] is True


async def test_a_refused_or_crashing_read_is_reported_not_raised() -> None:
    from assistant.coding_agents.zcode_cdp.job import ReadJob

    async def refused() -> Any:
        return snapshot(refusal="ZCode is signed out.")

    store = FakeStore()
    job = ReadJob(store, refused)
    job.start(confirmed=True)
    await job._task  # type: ignore[misc]
    assert job.view() == {**job.view(), "state": "refused", "message": "ZCode is signed out."}
    assert store.state == {}  # a refusal keeps nothing

    async def crashes() -> Any:
        raise RuntimeError("secret detail that must not leak")

    job = ReadJob(store, crashes)
    job.start(confirmed=True)
    await job._task  # type: ignore[misc]
    assert job.view()["state"] == "refused" and "secret" not in job.view()["message"]


async def test_the_settings_action_starts_a_read_and_status_shows_it(tmp_path: Path) -> None:
    from assistant.coding_agents.zcode_cdp.job import ReadJob
    from tests.unit.test_zcode_backend import make_toolkit

    toolkit, _project, store = make_toolkit(tmp_path, tmp_path / "zcode")

    async def reader() -> Any:
        return snapshot()

    toolkit._cdp_job = ReadJob(store, reader)
    refused = await toolkit.handle_action("zcode.read", {})
    assert "Confirm" in refused["error"]
    started = await toolkit.handle_action("zcode.read", {"confirm": True})
    assert started["cdp"]["job"]["state"] in {"reading", "done"}
    await toolkit._cdp_job._task
    report = await toolkit.status_report()
    assert report["cdp"]["job"]["state"] == "done"
    assert report["cdp"]["contract"]["ok"] is True
    assert report["cdp"]["sessions"]["count"] == 2
    assert report["account"]["name"] == "Agnix MStudio"


def test_every_zcode_method_the_host_can_send_is_one_the_core_accepts() -> None:
    """The installed app said "unknown method": the core's allowlist missed ``zcode.read``.

    The Rust host maps window actions to core methods; the core only dispatches an allowlist.
    """
    import re

    from assistant.core.app import _ZCODE_ACTIONS

    source = (Path(__file__).parents[2] / "sani/src-tauri/src/sani_core.rs").read_text()
    body = source[source.index("fn zcode_auth_method") :]
    body = body[: body.index("\n}\n")]
    sent = set(re.findall(r'"(zcode\.[a-z_]+)"', body))
    assert "zcode.read" in sent
    assert sent <= _ZCODE_ACTIONS


def test_the_core_dispatches_zcode_read_instead_of_rejecting_it() -> None:
    from assistant.core.app import SaniCoreApp

    app = SaniCoreApp.__new__(SaniCoreApp)  # routing needs no state
    assert app._handler_for("zcode.read") is not None
    assert app._handler_for("zcode.nonsense") is None
