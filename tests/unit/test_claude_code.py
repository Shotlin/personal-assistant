"""Claude Code companion: finder, parser, supervisor and runner.

The runner is exercised against a fake ``claude`` executable that speaks the
stream-json format, so nothing here needs a login, a network or a model.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from assistant.claude_code.events import (
    Final,
    Init,
    PermissionDenied,
    RateLimit,
    Retry,
    TextDelta,
    ToolEnd,
    ToolStart,
)
from assistant.claude_code.labels import describe_tool
from assistant.claude_code.locate import child_environment, find_claude_binary, parse_version
from assistant.claude_code.parser import StreamParser
from assistant.claude_code.redact import screen
from assistant.claude_code.runner import ClaudeRunner, RunRequest, build_args
from assistant.claude_code.watchdog import Watchdog, WatchdogLimits

DESKTOP = "Library/Application Support/Claude/claude-code"


def line(**message: object) -> str:
    return json.dumps(message)


# --------------------------------------------------------------------- locate


def test_environment_drops_api_billing_and_other_secrets() -> None:
    env = child_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/Users/x",
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "ANTHROPIC_AUTH_TOKEN": "t",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": "t",
            "OPENROUTER_API_KEY": "sk-or-secret",
            "RANDOM_APP_SECRET": "nope",
        }
    )
    assert env == {"PATH": "/usr/bin", "HOME": "/Users/x"}


def test_find_prefers_path_then_standard_then_desktop_bundle(tmp_path: Path) -> None:
    exe = stat.S_IRWXU
    bundled_old = tmp_path / f"{DESKTOP}/2.1.284/h/claude.app/Contents/MacOS/claude"
    bundled_new = tmp_path / f"{DESKTOP}/2.1.286/h/claude.app/Contents/MacOS/claude"
    for path in (bundled_old, bundled_new):
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(exe)
    # Only the desktop bundle exists: the newest version wins.
    found = find_claude_binary(home=tmp_path, which=lambda _: None)
    assert found == bundled_new
    # A real install in ~/.local/bin beats the bundle.
    local = tmp_path / ".local/bin/claude"
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n")
    local.chmod(exe)
    assert find_claude_binary(home=tmp_path, which=lambda _: None) == local
    # PATH beats both.
    assert find_claude_binary(home=tmp_path, which=lambda _: "/usr/bin/claude") == Path(
        "/usr/bin/claude"
    )
    # An explicit override that does not exist is not silently replaced.
    assert find_claude_binary(override=str(tmp_path / "missing"), home=tmp_path) is None


def test_parse_version() -> None:
    assert parse_version("2.1.286 (Claude Code)") == (2, 1, 286)
    assert parse_version("nothing") is None


# --------------------------------------------------------------------- labels


def test_labels_read_like_sentences_and_screen_secrets(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    label, _, path = describe_tool(
        "Edit", {"file_path": f"{cwd}/src/app.ts", "old_string": "a", "new_string": "b"}, cwd
    )
    assert label == "Edited src/app.ts"
    assert path == "src/app.ts"
    label, detail, _ = describe_tool(
        "Bash", {"command": "export API_KEY=sk-abcdef123456 && npm test"}, cwd
    )
    assert label.startswith("Ran `")
    assert "sk-abcdef123456" not in detail
    label, _, _ = describe_tool(
        "Bash", {"command": "npm test", "description": "Run the tests"}, cwd
    )
    assert label == "Run the tests"
    label, _, _ = describe_tool("mcp__github__create_issue", {"title": "x"}, cwd)
    assert label == "Used create issue (github)"


def test_screen_redacts_credential_shapes_and_caps_length() -> None:
    text = "token ghp_" + "a" * 30 + " and AKIA" + "B" * 16 + " end"
    cleaned = screen(text)
    assert "ghp_" not in cleaned and "AKIA" not in cleaned
    assert len(screen("x" * 5000, limit=100)) <= 101


# --------------------------------------------------------------------- parser


def test_parser_maps_a_whole_run() -> None:
    parser = StreamParser("/p")
    events = []
    for raw in (
        line(type="system", subtype="init", session_id="s1", model="m", cwd="/p"),
        line(
            type="assistant",
            message={
                "content": [
                    {"type": "text", "text": "Looking."},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                ],
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 90},
            },
        ),
        line(
            type="user",
            message={
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "boom",
                        "is_error": True,
                    }
                ]
            },
        ),
        line(
            type="stream_event",
            event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        ),
        "not json",
        line(type="mystery", whatever=1),
        line(
            type="result",
            subtype="success",
            is_error=False,
            result="All done",
            session_id="s1",
            num_turns=2,
            total_cost_usd=0.5,
            modelUsage={"m": {"contextWindow": 200000}},
            permission_denials=[{"tool_name": "Bash"}],
        ),
    ):
        events += parser.feed(raw)
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["Init", "AssistantText", "ToolStart", "ToolEnd", "TextDelta", "Final"]
    init = events[0]
    assert isinstance(init, Init) and init.session_id == "s1"
    end = events[3]
    assert isinstance(end, ToolEnd) and not end.ok and end.summary == "boom"
    final = events[-1]
    assert isinstance(final, Final)
    assert final.ok and final.turns == 2 and final.cost_usd == 0.5
    assert final.context_tokens == 100 and final.context_window == 200000
    assert final.denials == ("Bash",)


def test_parser_failure_subtypes_are_not_ok() -> None:
    parser = StreamParser()
    [final] = parser.feed(line(type="result", subtype="error_max_turns", is_error=False, result=""))
    assert isinstance(final, Final) and not final.ok


def test_parser_reads_retry_denial_and_rate_limit() -> None:
    parser = StreamParser()
    [retry] = parser.feed(
        line(type="system", subtype="api_retry", attempt=1, max_retries=3, error="rate_limit")
    )
    assert isinstance(retry, Retry) and retry.error == "rate_limit"
    [denied] = parser.feed(line(type="system", subtype="permission_denied", tool_name="Bash"))
    assert isinstance(denied, PermissionDenied) and denied.tool == "Bash"
    [limit] = parser.feed(
        line(
            type="rate_limit_event",
            rate_limit_info={
                "rateLimitType": "five_hour",
                "status": "allowed",
                "resetsAt": 1.0,
                "utilization": 0.42,
            },
        )
    )
    assert isinstance(limit, RateLimit) and limit.kind == "five_hour"
    assert limit.used_percent == pytest.approx(42.0)
    assert isinstance(
        parser.feed(
            line(
                type="stream_event",
                event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}},
            )
        )[0],
        TextDelta,
    )


# ------------------------------------------------------------------- watchdog


def _start(call_id: str, name: str = "Bash", command: str = "npm test") -> ToolStart:
    return ToolStart(
        id=call_id,
        name=name,
        label=f"Ran `{command}`",
        detail=command,
        fingerprint=f"{name}:{command}",
        mutating=True,
    )


def test_watchdog_trips_on_a_repeated_step() -> None:
    dog = Watchdog()
    assert dog.observe(_start("1")) is None
    assert dog.observe(_start("2")) is None
    reason = dog.observe(_start("3"))
    assert reason and "repeated the same step" in reason


def test_watchdog_allows_different_steps() -> None:
    dog = Watchdog()
    for index in range(8):
        assert dog.observe(_start(str(index), command=f"cmd {index}")) is None


def test_watchdog_trips_on_error_streak_but_resets_on_success() -> None:
    dog = Watchdog(WatchdogLimits(error_streak_limit=3))
    assert dog.observe(ToolEnd("1", ok=False)) is None
    assert dog.observe(ToolEnd("2", ok=True)) is None
    assert dog.observe(ToolEnd("3", ok=False)) is None
    assert dog.observe(ToolEnd("4", ok=False)) is None
    assert "in a row failed" in (dog.observe(ToolEnd("5", ok=False)) or "")


def test_watchdog_stops_on_denials_and_rate_limits() -> None:
    dog = Watchdog()
    assert dog.observe(PermissionDenied("Bash")) is None
    assert "needs permission" in (dog.observe(PermissionDenied("Bash")) or "")
    assert "rate limited" in (Watchdog().observe(Retry(1, 3, "rate_limit")) or "")


def test_watchdog_idle_and_ceiling_use_the_clock() -> None:
    now = [0.0]
    dog = Watchdog(WatchdogLimits(idle_seconds=10, max_seconds=100), clock=lambda: now[0])
    now[0] = 5
    assert dog.check_time() is None
    now[0] = 11
    assert dog.check_time() == "it stopped responding"
    dog.touch()
    now[0] = 15
    assert dog.check_time() is None
    now[0] = 101
    dog.touch()
    assert dog.check_time() == "it ran past its time limit"


# --------------------------------------------------------------------- runner

FAKE_CLAUDE = textwrap.dedent(
    """\
    #!{python}
    import json, os, signal, sys, time
    here = os.path.dirname(os.path.abspath(__file__))
    prompt = sys.stdin.read()
    with open(os.path.join(here, "seen.json"), "w") as fh:
        json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt,
                   "env": sorted(os.environ)}}, fh)

    def out(**m):
        sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()

    def result(subtype="success", text="done", is_error=False):
        out(type="result", subtype=subtype, is_error=is_error, result=text,
            session_id="sess-1", num_turns=1, total_cost_usd=0.01, duration_ms=5)

    def tool(i, name="Bash", **inp):
        out(type="assistant", message={{"content": [
            {{"type": "tool_use", "id": f"t{{i}}", "name": name, "input": inp}}]}})

    def done(i, ok=True):
        out(type="user", message={{"content": [
            {{"type": "tool_result", "tool_use_id": f"t{{i}}", "content": "err",
              "is_error": not ok}}]}})

    def on_int(*_):
        result("error_during_execution", "interrupted", True); sys.exit(130)
    signal.signal(signal.SIGINT, on_int)

    out(type="system", subtype="init", session_id="sess-1", model="m", cwd=os.getcwd())
    if "SCENARIO:ok" in prompt:
        tool(1, command="npm test"); done(1)
        tool(2, "Edit", file_path=os.getcwd() + "/src/a.py", old_string="a",
             new_string="b"); done(2)
        out(type="assistant", message={{"content": [{{"type": "text", "text": "Fixed it."}}]}})
        result(text="Fixed the bug in src/a.py")
    elif "SCENARIO:loop" in prompt:
        for i in range(1, 20):
            tool(i, command="npm test"); done(i, ok=False); time.sleep(0.05)
        result()
    elif "SCENARIO:hang" in prompt:
        time.sleep(60)
    elif "SCENARIO:crash" in prompt:
        sys.stderr.write("boom: auth failed ghp_" + "a" * 30); sys.exit(2)
    else:
        result()
    """
)


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    path = bin_dir / "claude"
    path.write_text(FAKE_CLAUDE.format(python=sys.executable))
    path.chmod(stat.S_IRWXU)
    return path


def seen(fake: Path) -> dict[str, Any]:
    return json.loads((fake.parent / "seen.json").read_text())


async def run(
    fake: Path,
    tmp_path: Path,
    prompt: str,
    *,
    limits: WatchdogLimits | None = None,
    cancel: asyncio.Event | None = None,
    **request: object,
):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    events = []

    async def on_event(event: object) -> None:
        events.append(event)

    runner = ClaudeRunner(fake, version=(2, 1, 286), tick_seconds=0.05, grace_seconds=2.0)
    outcome = await runner.run(
        RunRequest(prompt=prompt, cwd=project, **request),  # type: ignore[arg-type]
        on_event=on_event,
        watchdog=Watchdog(limits),
        cancel=cancel or asyncio.Event(),
    )
    return outcome, events


async def test_runner_completes_and_reports_files_and_session(
    fake_claude: Path, tmp_path: Path
) -> None:
    outcome, events = await run(fake_claude, tmp_path, "SCENARIO:ok fix the bug")
    assert outcome.ok and not outcome.stopped_reason
    assert outcome.session_id == "sess-1"
    assert outcome.files_changed == ["src/a.py"]
    assert outcome.steps == 2 and outcome.failed_steps == 0
    assert "Fixed the bug" in outcome.text
    assert any(isinstance(e, ToolStart) for e in events)
    info = seen(fake_claude)
    assert info["cwd"] == str((tmp_path / "project").resolve())
    assert "SCENARIO:ok" in str(info["prompt"])
    # Nothing that could switch billing or leak a key reaches the child.
    leaked = [n for n in info["env"] if n.startswith("ANTHROPIC") or n == "OPENROUTER_API_KEY"]
    assert not leaked


async def test_runner_stops_a_loop_before_it_burns_tokens(
    fake_claude: Path, tmp_path: Path
) -> None:
    outcome, events = await run(fake_claude, tmp_path, "SCENARIO:loop")
    assert not outcome.ok
    assert "repeated the same step" in outcome.stopped_reason
    # Stopped at the third repeat, not after all nineteen.
    assert len([e for e in events if isinstance(e, ToolStart)]) <= 5


async def test_runner_stops_silence(fake_claude: Path, tmp_path: Path) -> None:
    outcome, _ = await run(
        fake_claude, tmp_path, "SCENARIO:hang", limits=WatchdogLimits(idle_seconds=0.5)
    )
    assert not outcome.ok and outcome.stopped_reason == "it stopped responding"


async def test_runner_cancel_ends_the_process(fake_claude: Path, tmp_path: Path) -> None:
    cancel = asyncio.Event()
    asyncio.get_running_loop().call_later(0.5, cancel.set)
    outcome, _ = await run(fake_claude, tmp_path, "SCENARIO:hang", cancel=cancel)
    assert outcome.cancelled and not outcome.ok


async def test_runner_reports_a_crash_without_leaking_secrets(
    fake_claude: Path, tmp_path: Path
) -> None:
    outcome, _ = await run(fake_claude, tmp_path, "SCENARIO:crash")
    assert not outcome.ok and outcome.exit_code == 2
    assert "auth failed" in outcome.error and "ghp_" not in outcome.error


# ----------------------------------------------------------------- arguments


def test_args_for_each_preset(tmp_path: Path) -> None:
    base = RunRequest(prompt="x", cwd=tmp_path)
    read = build_args(RunRequest(prompt="x", cwd=tmp_path, permission="read"), version=(2, 1, 286))
    assert read[read.index("--permission-mode") + 1] == "dontAsk"
    assert "Edit" not in read[read.index("--allowedTools") + 1]
    edit = build_args(base, version=(2, 1, 286))
    assert edit[edit.index("--permission-mode") + 1] == "acceptEdits"
    assert "Bash" not in edit[edit.index("--allowedTools") + 1].split(",")
    run_args = build_args(
        RunRequest(prompt="x", cwd=tmp_path, permission="run"), version=(2, 1, 286)
    )
    assert "Bash" in run_args[run_args.index("--allowedTools") + 1].split(",")
    for args in (read, edit, run_args):
        assert "--dangerously-skip-permissions" not in args
        assert "--bare" not in args
        assert "Bash(git push *)" in args[args.index("--disallowedTools") + 1]
        assert "--permission-prompts" in args


def test_args_resume_fork_and_old_versions(tmp_path: Path) -> None:
    resumed = build_args(
        RunRequest(prompt="x", cwd=tmp_path, session_id="abc"), version=(2, 1, 100)
    )
    assert resumed[resumed.index("--resume") + 1] == "abc"
    assert "--fork-session" not in resumed
    assert "--permission-prompts" not in resumed  # unsupported before 2.1.259
    forked = build_args(RunRequest(prompt="x", cwd=tmp_path, session_id="abc", fork=True))
    assert "--fork-session" in forked
    assert os.fspath(tmp_path)  # keep the fixture used
