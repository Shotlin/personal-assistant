"""Account change and sync: sign-out, a different account, a plan that ends, and recovery.

A real account switch inside ZCode is something only the user can do; here it is simulated
with scripted reads. Nothing touches ZCode's credential values: the tests plant a secret in a
fake credentials file and assert it never reaches anything Sani stores.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from assistant.claude_code.locate import ClaudeStatus
from assistant.claude_code.store import SessionStore
from assistant.claude_code.toolkit import ClaudeCodeToolkit
from assistant.coding_agents.zcode_cdp import sync as zsync
from assistant.coding_agents.zcode_cdp.control import ControlSession
from assistant.coding_agents.zcode_cdp.job import ReadJob
from assistant.coding_agents.zcode_cdp.reader import Snapshot
from assistant.coding_agents.zcode_cdp.sync import AccountSync, credential_signal
from assistant.coding_agents.zcode_window import ZCodeWindowBackend
from assistant.settings import Settings

SECRET = "sk-live-THIS-MUST-NEVER-BE-KEPT-0123456789"


def store_at(tmp_path: Path) -> SessionStore:
    return SessionStore(str(tmp_path / "sani.db"))


def snap(name: str, plans: list[str], *, signed_in: bool = True, at: float = 100.0) -> Snapshot:
    items = [
        {
            "provider": "account:zai-start-plan",
            "plan": plan,
            "model": "GLM-5.3-Flash",
            "remaining": 5,
            "total": 5,
            "percent": 100,
            "reset": "21:29",
            "expires": "Expires soon",
        }
        for plan in plans
    ]
    result = Snapshot(
        as_of=at,
        contract={"ok": True, "version": "3.14.4", "version_verified": True},
        account={"name": name if signed_in else "", "signed_in": signed_in},
        models={"models": [{"model": "GLM-5.3-Flash", "current": True}], "current_model": "x"},
        balances={"items": items} if signed_in else {},
        sessions={"count": 1, "projects": [], "complete": True},
        port_closed=True,
    )
    if not signed_in:
        result.refusal = (
            "ZCode is signed out. Open ZCode and sign in; Sani never types credentials."
        )
        result.models, result.sessions = {}, {}
    return result


async def run_job(store: SessionStore, snapshot: Snapshot) -> ReadJob:
    async def reader() -> Snapshot:
        return snapshot

    job = ReadJob(store, reader)
    job.start(confirmed=True)
    await job._task  # type: ignore[misc]
    return job


@pytest.fixture(autouse=True)
def fake_zcode_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "zhome"
    (home / "v2").mkdir(parents=True)
    (home / "v2" / "credentials.json").write_text(
        json.dumps({"oauth:zai:access_token:u1": SECRET, "account-provider:zai": SECRET})
    )
    monkeypatch.setenv("ZCODE_HOME", str(home))
    return home


# -- the credential signal never keeps values ---------------------------------------------


def test_the_credential_signal_uses_names_and_time_only() -> None:
    fingerprint, modified = credential_signal()
    assert fingerprint and modified > 0
    assert SECRET not in fingerprint
    again, _ = credential_signal()
    assert again == fingerprint


def test_a_missing_or_broken_credentials_file_is_just_no_signal(tmp_path: Path) -> None:
    assert credential_signal(tmp_path / "nope.json") == ("", 0.0)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert credential_signal(broken) == ("", 0.0)


# -- observing changes --------------------------------------------------------------------


async def test_the_first_read_and_an_unchanged_read_are_not_changes(tmp_path: Path) -> None:
    sync = AccountSync(store_at(tmp_path))
    assert await sync.observe(signed_in=True, name="A", plans=["P1"]) is None
    assert await sync.observe(signed_in=True, name="A", plans=["P1"]) is None
    assert (await sync.view())["change"] is None


async def test_a_different_account_drops_the_old_one_and_says_so(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    sync = AccountSync(store, clock=lambda: 50.0)
    await sync.observe(signed_in=True, name="A", plans=["P1"])
    await store.put_state("zcode_account", {"name": "A", "email": "", "as_of": 1})
    await store.put_state("zcode_balances", {"items": [{"plan": "P1"}]})
    await store.put_state("zcode_models", {"models": [1]})
    await store.put_state("zcode_sessions", {"count": 3})
    await store.set_session("zcode:chat-1", "/proj", "sess_of_A")
    await store.set_session("chat-1", "/proj", "claude_session_untouched")
    change = await sync.observe(signed_in=True, name="B", plans=["P2"], source="window")
    assert change is not None and change["kind"] == "switched"
    assert (change["from"], change["to"]) == ("A", "B")
    assert change["plans_added"] == ["P2"] and change["plans_removed"] == ["P1"]
    for key in ("zcode_account", "zcode_balances", "zcode_models", "zcode_sessions"):
        assert await store.get_state(key) == {}, key
    assert await store.get_session("zcode:chat-1", "/proj") == ""  # A's task mapping is gone
    assert await store.get_session("chat-1", "/proj") == "claude_session_untouched"
    audit = (await store.get_state(zsync.AUDIT_KEY))["lines"]
    assert audit[-1]["line"] == "Account changed: A -> B"
    view = await sync.view()
    assert view["change"]["to"] == "B"
    await sync.acknowledge()
    assert (await sync.view())["change"] is None  # dismissed, but the record stays in the audit


async def test_sign_out_then_sign_in_is_reported_both_ways(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    sync = AccountSync(store)
    await sync.observe(signed_in=True, name="A", plans=["P1"])
    await store.put_state("zcode_balances", {"items": [{"plan": "P1"}]})
    out = await sync.observe(signed_in=False, name="", plans=None)
    assert out is not None and out["kind"] == "signed_out" and out["from"] == "A"
    assert await store.get_state("zcode_balances") == {}
    back = await sync.observe(signed_in=True, name="A", plans=["P1"])
    assert back is not None and back["kind"] == "signed_in" and back["to"] == "A"


async def test_a_plan_that_ends_or_is_added_is_a_change_but_keeps_the_account(
    tmp_path: Path,
) -> None:
    store = store_at(tmp_path)
    sync = AccountSync(store)
    await sync.observe(signed_in=True, name="A", plans=["Trust Build", "Start Plan"])
    await store.put_state("zcode_account", {"name": "A", "email": "", "as_of": 1})
    await store.put_state("zcode_balances", {"items": [{"plan": "Trust Build"}]})
    change = await sync.observe(signed_in=True, name="A", plans=["Start Plan"])
    assert change is not None and change["kind"] == "plans_changed"
    assert change["plans_removed"] == ["Trust Build"]
    assert (await store.get_state("zcode_account"))["name"] == "A"
    assert await store.get_state("zcode_balances") == {}


async def test_plans_not_read_this_time_cannot_count_as_changed(tmp_path: Path) -> None:
    sync = AccountSync(store_at(tmp_path))
    await sync.observe(signed_in=True, name="A", plans=["P1"])
    assert await sync.observe(signed_in=True, name="A", plans=None, source="window") is None


async def test_the_plans_of_a_noticed_switch_fold_into_the_same_banner(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    sync = AccountSync(store)
    await sync.observe(signed_in=True, name="A", plans=["P1"])
    await sync.observe(signed_in=True, name="B", plans=None, source="window")  # label noticed
    await sync.observe(signed_in=True, name="B", plans=["P2"])  # the read that follows
    change = (await sync.view())["change"]
    assert change["kind"] == "switched" and (change["from"], change["to"]) == ("A", "B")
    assert change["plans_added"] == ["P2"]


async def test_the_cheap_signal_marks_data_stale_once(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    sync = AccountSync(store)
    await sync.observe(signed_in=True, name="A", plans=["P1"], fingerprint="fp1:100")
    assert await sync.signal("fp1:100", 100.0) is False  # unchanged
    assert await sync.signal("fp2:200", 200.0) is True
    assert await sync.signal("fp2:200", 200.0) is False  # already noted
    assert (await sync.view())["stale"] is True
    await sync.observe(signed_in=True, name="A", plans=["P1"], fingerprint="fp2:200")
    assert (await sync.view())["stale"] is False  # a fresh read clears it


# -- through the read job ---------------------------------------------------------------


async def test_a_simulated_account_switch_is_picked_up_by_the_next_read(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    await run_job(store, snap("Alice", ["Start Plan"]))
    await store.set_session("zcode:chat-1", "/proj", "sess_alice")
    assert (await store.get_state("zcode_account"))["name"] == "Alice"
    job = await run_job(store, snap("Bob", ["Pro Plan"], at=200.0))
    assert job.view()["state"] == "done"
    assert (await store.get_state("zcode_account"))["name"] == "Bob"
    items = (await store.get_state("zcode_balances"))["items"]
    assert {i["plan"] for i in items} == {"Pro Plan"}  # nothing of Alice's mixed in
    assert await store.get_session("zcode:chat-1", "/proj") == ""
    view = await AccountSync(store).view()
    assert (view["change"]["from"], view["change"]["to"]) == ("Alice", "Bob")


async def test_sign_out_is_refused_in_words_and_recovery_is_picked_up(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    await run_job(store, snap("Alice", ["Start Plan"]))
    out = await run_job(store, snap("", [], signed_in=False, at=200.0))
    assert out.view()["state"] == "refused" and "signed out" in out.view()["message"]
    assert (await AccountSync(store).view())["change"]["kind"] == "signed_out"
    assert await store.get_state("zcode_balances") == {}  # nothing stale left to show
    back = await run_job(store, snap("Alice", ["Start Plan"], at=300.0))
    assert back.view()["state"] == "done"
    assert (await AccountSync(store).view())["change"]["kind"] == "signed_in"
    assert (await store.get_state("zcode_account"))["name"] == "Alice"


async def test_no_credential_value_reaches_anything_sani_stores(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    sync = AccountSync(store)
    fingerprint, modified = credential_signal()
    await sync.observe(signed_in=True, name="A", plans=["P1"], fingerprint=fingerprint)
    await sync.signal("other:1", modified)
    await run_job(store, snap("B", ["P2"]))
    raw = (tmp_path / "sani.db").read_bytes()
    assert SECRET.encode() not in raw


# -- auto re-read in the toolkit ---------------------------------------------------------


def toolkit_for(tmp_path: Path, started: list[int]) -> tuple[ClaudeCodeToolkit, ZCodeWindowBackend]:
    (tmp_path / "data").mkdir(exist_ok=True)
    settings = Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path / "data"),
        claude_code_dirs=str(tmp_path / "work"),
        zcode_cli_enabled=True,
        zcode_mode="window",
    )
    backend = ZCodeWindowBackend(str(tmp_path / "data"), control=ControlSession(sleep=_nosleep))

    async def reader(_binary: Path | None) -> ClaudeStatus:
        return ClaudeStatus(installed=True, path="/Applications/ZCode.app", signed_in=True)

    toolkit = ClaudeCodeToolkit(
        settings,
        backend=backend,
        locator=lambda **_: Path("/Applications/ZCode.app"),
        status_reader=reader,
        store=SessionStore(settings.sani_db_path),
    )

    class Job:
        reading = False

        def start(self, *, confirmed: bool) -> str:
            started.append(1 if confirmed else 0)
            return ""

        def view(self) -> dict[str, Any]:
            return {"state": "idle"}

    toolkit._cdp_job = Job()
    return toolkit, backend


async def _nosleep(_: float) -> None:
    await asyncio.sleep(0)


async def test_a_changed_sign_in_file_starts_a_read_on_its_own_when_control_is_on(
    tmp_path: Path, fake_zcode_home: Path
) -> None:
    started: list[int] = []
    toolkit, backend = toolkit_for(tmp_path, started)
    sync = AccountSync(toolkit._store)  # type: ignore[arg-type]
    fingerprint, _ = credential_signal()
    await sync.observe(signed_in=True, name="A", plans=["P1"], fingerprint=fingerprint)
    await backend.control.set_enabled(True)
    await toolkit.status_report()
    assert started == []  # nothing changed yet
    # the user signs in as someone else inside ZCode: the credentials file changes
    path = fake_zcode_home / "v2" / "credentials.json"
    path.write_text(json.dumps({"oauth:zai:access_token:u2": SECRET}))
    old = time.time() - 60
    os.utime(path, (old, old))
    report = await toolkit.status_report()
    assert started == [1] and report["account_change"]["stale"] is True
    await toolkit.status_report()
    assert started == [1]  # not again within five minutes


async def test_without_the_standing_yes_nothing_is_relaunched_but_the_data_is_marked_stale(
    tmp_path: Path, fake_zcode_home: Path
) -> None:
    started: list[int] = []
    toolkit, backend = toolkit_for(tmp_path, started)
    fingerprint, _ = credential_signal()
    await AccountSync(toolkit._store).observe(  # type: ignore[arg-type]
        signed_in=True, name="A", plans=["P1"], fingerprint=fingerprint
    )
    path = fake_zcode_home / "v2" / "credentials.json"
    path.write_text(json.dumps({"oauth:zai:access_token:u2": SECRET}))
    old = time.time() - 60
    os.utime(path, (old, old))
    report = await toolkit.status_report()
    assert started == [] and report["account_change"]["stale"] is True
    assert report["control"]["enabled"] is False


async def test_a_sign_in_still_in_progress_is_given_time_to_finish(
    tmp_path: Path, fake_zcode_home: Path
) -> None:
    started: list[int] = []
    toolkit, backend = toolkit_for(tmp_path, started)
    fingerprint, _ = credential_signal()
    await AccountSync(toolkit._store).observe(  # type: ignore[arg-type]
        signed_in=True, name="A", plans=["P1"], fingerprint=fingerprint
    )
    await backend.control.set_enabled(True)
    (fake_zcode_home / "v2" / "credentials.json").write_text(json.dumps({"oauth:zai:x": SECRET}))
    await toolkit.status_report()  # file was written just now
    assert started == []


# -- the open window is watched too ------------------------------------------------------


async def test_a_new_label_in_the_open_window_is_noticed_and_asks_for_a_read(
    tmp_path: Path,
) -> None:
    started: list[int] = []
    toolkit, backend = toolkit_for(tmp_path, started)
    store = toolkit._store
    assert store is not None
    await AccountSync(store).observe(signed_in=True, name="Alice", plans=["P1"])
    backend.bind_store(store)  # as the toolkit does at construction
    await backend._on_account({"name": "Bob", "signed_in": True})
    view = await AccountSync(store).view()
    assert view["change"]["kind"] == "switched" and view["stale"] is True
    await backend._on_account({"name": "Bob", "signed_in": True})  # same label again: no new change
    assert (await AccountSync(store).view())["change"]["to"] == "Bob"


async def test_the_window_watcher_calls_back_while_open_and_stops_when_closed() -> None:
    from tests.unit.test_zcode_window import FakeClient, FakeLauncher, frame

    seen: list[dict[str, Any]] = []

    async def on_account(account: dict[str, Any]) -> None:
        seen.append(account)

    async def opener(_: int) -> Any:
        return FakeClient([frame([])])

    class Store:
        async def get_state(self, key: str) -> dict[str, Any]:
            return {"enabled": True}

        async def put_state(self, key: str, value: dict[str, Any]) -> None:
            return None

    control = ControlSession(
        launcher_factory=FakeLauncher,  # type: ignore[arg-type]
        opener=opener,
        sleep=_nosleep,
        watch_seconds=0,
        on_account=on_account,
    )
    control.bind_store(Store())
    async with control.session():
        pass
    for _ in range(20):
        await asyncio.sleep(0)
    assert seen and seen[0]["signed_in"] is True
    await control.close()
    count = len(seen)
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(seen) == count  # no polling after the port is closed
