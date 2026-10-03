"""Sign in / out from inside Sani, against a fake `claude auth` program."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from assistant.claude_code.auth import CANCELLED, FAILED, IDLE, SUCCEEDED, WAITING, LoginSession
from assistant.claude_code.locate import read_status
from assistant.claude_code.toolkit import ClaudeCodeToolkit
from assistant.settings import Settings

FAKE = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys, time
    here = os.path.dirname(os.path.abspath(__file__))
    state = os.path.join(here, "signed_in")
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        on = os.path.exists(state)
        print(json.dumps({{"loggedIn": on, "authMethod": "claude.ai" if on else "none"}}))
        sys.exit(0 if on else 1)
    if args[:2] == ["--version"] or args == ["--version"]:
        print("2.1.286 (Claude Code)"); sys.exit(0)
    if args[:2] == ["auth", "logout"]:
        if os.path.exists(state): os.remove(state)
        print("Logged out"); sys.exit(0)
    if args[:2] == ["auth", "login"]:
        sys.stdout.write("Opening browser to sign in\\u2026\\n")
        sys.stdout.write("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=abc\\n")
        sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
        if os.path.exists(os.path.join(here, "auto")):
            time.sleep(0.4); open(state, "w").write("1"); print("Login successful"); sys.exit(0)
        line = sys.stdin.readline().strip()
        if line == "GOODCODE":
            open(state, "w").write("1"); print("Login successful"); sys.exit(0)
        print("Invalid code"); sys.exit(1)
    sys.exit(2)
    """
)


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(FAKE.format(python=sys.executable))
    path.chmod(stat.S_IRWXU)
    return path


async def until(session: LoginSession, state: str, seconds: float = 8) -> None:
    for _ in range(int(seconds / 0.05)):
        if session.view().state == state:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached {state}; at {session.view()}")


async def test_the_browser_finishes_the_sign_in_by_itself(fake: Path) -> None:
    (fake.parent / "auto").write_text("")
    session = LoginSession()
    first = await session.start(fake)
    assert first.state == WAITING
    await until(session, SUCCEEDED)
    assert (await read_status(fake)).signed_in is True


async def test_the_link_is_found_and_a_pasted_code_completes_it(fake: Path) -> None:
    session = LoginSession()
    await session.start(fake)
    for _ in range(100):
        if session.view().url:
            break
        await asyncio.sleep(0.05)
    assert session.view().url.startswith("https://claude.com/cai/oauth/authorize")
    await session.submit_code("GOODCODE")
    await until(session, SUCCEEDED)
    assert (await read_status(fake)).signed_in is True


async def test_a_wrong_code_fails_cleanly_and_a_malformed_one_is_not_sent(fake: Path) -> None:
    session = LoginSession()
    await session.start(fake)
    view = await session.submit_code("bad code; rm -rf /")
    assert view.state == WAITING and "doesn't look like" in view.message
    await session.submit_code("WRONGCODE")
    await until(session, FAILED)
    assert (await read_status(fake)).signed_in is False


async def test_cancel_stops_the_command_and_a_second_start_is_allowed(fake: Path) -> None:
    session = LoginSession()
    await session.start(fake)
    assert (await session.cancel()).state == CANCELLED
    again = await session.start(fake)
    assert again.state == WAITING
    await session.cancel()


async def test_cancel_with_nothing_running_does_nothing(fake: Path) -> None:
    assert (await LoginSession().cancel()).state == IDLE


def make_toolkit(tmp_path: Path, fake: Path) -> ClaudeCodeToolkit:
    (tmp_path / "data").mkdir(exist_ok=True)
    settings = Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path / "data"),
        claude_code_enabled=True,
    )
    return ClaudeCodeToolkit(settings, locator=lambda **_: fake)


async def test_the_actions_drive_status_end_to_end_and_sign_out(tmp_path: Path, fake: Path) -> None:
    (fake.parent / "auto").write_text("")
    toolkit = make_toolkit(tmp_path, fake)
    report: Any = await toolkit.status_report()
    assert report["claude"]["signed_in"] is False and report["login"]["state"] == IDLE

    started = await toolkit.handle_action("claude_code.login_start", {})
    assert started["login"]["state"] == WAITING
    for _ in range(100):
        report = await toolkit.status_report()
        if report["claude"]["signed_in"]:
            break
        await asyncio.sleep(0.1)
    assert report["claude"]["signed_in"] is True and report["login"]["state"] == SUCCEEDED

    out = await toolkit.handle_action("claude_code.logout", {})
    assert out["claude"]["signed_in"] is False
    json.dumps(out)  # must survive the wire


async def test_unknown_actions_and_a_missing_install_are_refused(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    settings = Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path / "data"),
    )
    toolkit = ClaudeCodeToolkit(settings, locator=lambda **_: None)
    assert (
        "isn't installed" in (await toolkit.handle_action("claude_code.login_start", {}))["error"]
    )
    assert "isn't installed" in (await toolkit.handle_action("claude_code.logout", {}))["error"]
    assert (await toolkit.handle_action("claude_code.nope", {}))["error"] == "unknown action"
