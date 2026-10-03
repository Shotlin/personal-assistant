"""The claude_code tool: folder safety, sign-in gating, step events, sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from assistant.claude_code import context
from assistant.claude_code.locate import ClaudeStatus
from assistant.claude_code.runner import ClaudeRunner
from assistant.claude_code.toolkit import ClaudeCodeToolkit
from assistant.claude_code.watchdog import WatchdogLimits
from assistant.core.protocol import AGENT_PROGRESS
from assistant.settings import Settings
from tests.unit.test_claude_code import fake_claude, seen  # noqa: F401  (fixture re-export)

SIGNED_IN = ClaudeStatus(installed=True, path="/x/claude", version="2.1.286", signed_in=True)


def make(
    tmp_path: Path,
    fake: Path | None,
    *,
    status: ClaudeStatus = SIGNED_IN,
    **overrides: Any,
) -> tuple[ClaudeCodeToolkit, Path]:
    project = tmp_path / "work" / "app"
    project.mkdir(parents=True, exist_ok=True)
    values: dict[str, Any] = {
        "app_env": "development",
        "model_provider": "generic_openai_compatible",
        "model_base_url": "http://127.0.0.1:1",
        "model_api_key": "k",
        "model_name": "m",
        "memory_backend": "sqlite",
        "sani_data_dir": str(tmp_path / "data"),
        "claude_code_enabled": True,
        "claude_code_dirs": str(tmp_path / "work"),
    }
    values.update(overrides)
    (tmp_path / "data").mkdir(exist_ok=True)

    async def reader(_binary: Path | None) -> ClaudeStatus:
        return status

    toolkit = ClaudeCodeToolkit(
        Settings(**values),
        locator=lambda **_: fake,
        status_reader=reader,
        runner_factory=lambda binary, version: ClaudeRunner(
            binary, version=version, tick_seconds=0.05, grace_seconds=2.0
        ),
        limits=WatchdogLimits(),
    )
    return toolkit, project


class Sink:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, data: dict[str, Any]) -> None:
        self.frames.append((kind, data))

    def steps(self) -> list[dict[str, Any]]:
        return [d["step"] for k, d in self.frames if k == AGENT_PROGRESS and "step" in d]


async def test_refuses_without_folders_or_outside_them(fake_claude: Path, tmp_path: Path) -> None:  # noqa: F811
    toolkit, project = make(tmp_path, fake_claude, claude_code_dirs="")
    assert "No project folders" in await toolkit.run("do it", str(project))
    toolkit, project = make(tmp_path, fake_claude)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    reply = await toolkit.run("do it", str(outside))
    assert reply.startswith("Not started.") and "isn't one of the folders" in reply


async def test_symlink_out_of_an_allowed_folder_does_not_count(
    fake_claude: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    toolkit, project = make(tmp_path, fake_claude)
    secret = tmp_path / "private"
    secret.mkdir()
    (project / "escape").symlink_to(secret)
    reply = await toolkit.run("do it", str(project / "escape"))
    assert reply.startswith("Not started.")


async def test_home_and_root_are_never_allowed(fake_claude: Path, tmp_path: Path) -> None:  # noqa: F811
    toolkit, _ = make(tmp_path, fake_claude, claude_code_dirs=f"/:{Path.home()}")
    assert toolkit.allowed_dirs() == []


async def test_not_installed_and_not_signed_in_never_start_a_process(
    fake_claude: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    toolkit, project = make(
        tmp_path, fake_claude, status=ClaudeStatus(installed=False, detail="Install it.")
    )
    assert await toolkit.run("go", str(project)) == "Not started. Install it."
    toolkit, project = make(
        tmp_path, fake_claude, status=ClaudeStatus(installed=True, signed_in=False)
    )
    reply = await toolkit.run("go", str(project))
    assert "claude auth login" in reply
    assert not (fake_claude.parent / "seen.json").exists()


async def test_a_run_streams_steps_and_returns_a_short_summary(
    fake_claude: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    toolkit, project = make(tmp_path, fake_claude)
    sink = Sink()
    token = context.event_sink.set(sink)
    conv = context.conversation_id.set("chat-1")
    try:
        reply = await toolkit.run("SCENARIO:ok fix the bug", str(project), mode="run")
    finally:
        context.event_sink.reset(token)
        context.conversation_id.reset(conv)
    assert reply.startswith("Claude Code finished.")
    assert "Files changed: src/a.py" in reply and "Session: sess-1" in reply
    steps = sink.steps()
    first = [s for s in steps if s["id"] == "cc:t1"]
    assert [s["status"] for s in first] == ["running", "complete"]
    assert first[-1]["duration_ms"] >= 0 and first[-1]["tool"] == "Bash"
    assert any(s["label"] == "Edited src/a.py" for s in steps)


async def test_the_next_call_in_a_chat_resumes_its_session(
    fake_claude: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    toolkit, project = make(tmp_path, fake_claude)
    conv = context.conversation_id.set("chat-1")
    try:
        await toolkit.run("SCENARIO:ok first", str(project))
        assert "--resume" not in seen(fake_claude)["argv"]
        await toolkit.run("SCENARIO:ok second", str(project))
        argv = seen(fake_claude)["argv"]
        assert argv[argv.index("--resume") + 1] == "sess-1"
        await toolkit.run("SCENARIO:ok third", str(project), new_session=True)
        assert "--resume" not in seen(fake_claude)["argv"]
    finally:
        context.conversation_id.reset(conv)


async def test_the_users_ceiling_limits_what_the_agent_may_ask_for(
    fake_claude: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    toolkit, project = make(tmp_path, fake_claude, claude_code_permission="read")
    reply = await toolkit.run("SCENARIO:ok look around", str(project), mode="run")
    argv = seen(fake_claude)["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "limited to “read”" in reply


async def test_a_runaway_run_is_reported_as_stopped_with_advice(
    fake_claude: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    toolkit, project = make(tmp_path, fake_claude)
    reply = await toolkit.run("SCENARIO:loop", str(project))
    assert "stopped early because it repeated the same step" in reply
    assert "Do not simply retry" in reply


async def test_one_folder_is_never_worked_on_twice_at_once(
    fake_claude: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    toolkit, project = make(tmp_path, fake_claude)
    first = asyncio.create_task(toolkit.run("SCENARIO:hang", str(project)))
    await asyncio.sleep(0.5)
    reply = await toolkit.run("SCENARIO:ok", str(project))
    assert "already working" in reply
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first


async def test_status_report_tells_the_ui_what_is_true(fake_claude: Path, tmp_path: Path) -> None:  # noqa: F811
    toolkit, project = make(tmp_path, fake_claude)
    report = await toolkit.status_report()
    assert report["enabled"] is True
    assert report["folders"] == [str((tmp_path / "work").resolve())]
    assert report["claude"]["signed_in"] is True
    json.dumps(report)  # must be JSON-serialisable for the wire
