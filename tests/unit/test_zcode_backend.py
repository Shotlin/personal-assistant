"""ZCode backend: arguments, tolerant parser, the provider-file shim, and a full run.

The run goes through a fake ``zcode`` program. The ``--json`` line shapes below
are an assumption (a real signed-in run was never captured), so these tests prove
Sani's handling of them, not ZCode's actual output.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from assistant.claude_code import context
from assistant.claude_code.events import AssistantText, Final, Init, ToolEnd, ToolStart
from assistant.claude_code.locate import ClaudeStatus
from assistant.claude_code.runner import ClaudeRunner, RunRequest
from assistant.claude_code.store import SessionStore
from assistant.claude_code.toolkit import ClaudeCodeToolkit
from assistant.coding_agents.backend import session_key
from assistant.coding_agents.claude import ClaudeBackend
from assistant.coding_agents.zcode import ZCodeBackend, ZCodeParser, configured_login
from assistant.core.protocol import AGENT_PROGRESS
from assistant.settings import Settings


def request(permission: str = "edit", **extra: Any) -> RunRequest:
    return RunRequest(prompt="fix it", cwd=Path("/work/app"), permission=permission, **extra)  # type: ignore[arg-type]


def test_modes_map_to_zcode_modes_and_never_give_shell_below_run() -> None:
    backend = ZCodeBackend()
    read = backend.build_args(request("read"), None)
    edit = backend.build_args(request("edit"), None)
    run = backend.build_args(request("run"), None)
    assert read[:2] == ["-p", "fix it"] and "--json" in read
    assert read[read.index("--mode") + 1] == "plan"
    assert "Bash" in read[read.index("--disallowed-tools") + 1]
    assert edit[edit.index("--mode") + 1] == "edit"
    assert edit[edit.index("--disallowed-tools") + 1] == "Bash"
    assert run[run.index("--mode") + 1] == "yolo" and "--disallowed-tools" not in run
    assert edit[edit.index("--cwd") + 1] == "/work/app"


def test_resume_and_attachments_are_passed() -> None:
    args = ZCodeBackend().build_args(
        request(session_id="sess_9", attachments=(Path("/a.png"), Path("/b.pdf"))), None
    )
    assert args[args.index("--resume") + 1] == "sess_9"
    assert args.count("--attach") == 2


def test_environment_never_carries_other_services_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("ZCODE_HOME", "/h/.zcode")
    env = ZCodeBackend().environment()
    assert "OPENROUTER_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["ZCODE_HOME"] == "/h/.zcode"


def test_parser_reads_a_tool_round_text_and_the_final_result() -> None:
    parser = ZCodeParser("/work/app")
    out: list[Any] = []
    for message in (
        {"type": "session.started", "sessionId": "sess_1", "model": "glm-5.3"},
        {"type": "assistant_message", "text": "Looking."},
        {
            "type": "tool_call_start",
            "toolCallId": "t1",
            "toolName": "bash",
            "input": {"command": "npm test"},
        },
        {"type": "tool_call_result", "toolCallId": "t1", "status": "failed", "error": "exit 1"},
        {
            "type": "tool_call_start",
            "toolCallId": "t2",
            "toolName": "edit",
            "input": {"path": "/work/app/a.py"},
        },
        {"type": "tool_call_result", "toolCallId": "t2"},
        {"type": "turn.completed", "result": "Done."},
    ):
        out += parser.feed(json.dumps(message))
    assert isinstance(out[0], Init) and out[0].session_id == "sess_1"
    starts = [e for e in out if isinstance(e, ToolStart)]
    assert starts[0].name == "Bash" and starts[0].mutating
    assert starts[1].name == "Edit" and starts[1].path == "a.py"
    ends = [e for e in out if isinstance(e, ToolEnd)]
    assert [e.ok for e in ends] == [False, True] and "exit 1" in ends[0].summary
    assert any(isinstance(e, AssistantText) for e in out)
    final = out[-1]
    assert isinstance(final, Final) and final.ok and final.text == "Done."
    assert final.session_id == "sess_1"


def test_parser_skips_noise_and_flags_errors() -> None:
    parser = ZCodeParser()
    assert parser.feed("ZCode Built-in skipped (not-due)") == []
    assert parser.feed("{broken") == []
    assert parser.feed('{"hello": 1}') == []
    failed = parser.feed(json.dumps({"type": "error", "message": "Model not found"}))
    assert isinstance(failed[-1], Final) and not failed[-1].ok


def test_the_shim_makes_the_app_cli_find_its_provider_file(tmp_path: Path) -> None:
    resources = tmp_path / "ZCode.app" / "Contents" / "Resources"
    (resources / "glm").mkdir(parents=True)
    (resources / "config" / "provider").mkdir(parents=True)
    cjs = resources / "glm" / "zcode.cjs"
    cjs.write_text("// cli")
    (resources / "config" / "provider" / "zcode-builtin.json").write_text('{"a":1}')
    backend = ZCodeBackend(str(tmp_path / "data"))
    command = backend.command(cjs)
    shim = Path(command[1])
    assert shim.parent == tmp_path / "data" / "zcode-shim"
    assert shim.resolve() == cjs.resolve()
    assert (shim.parent / "provider" / "zcode-builtin.json").read_text() == '{"a":1}'
    assert backend.command(cjs) == command  # second call reuses it
    # The app bundle itself is untouched.
    assert sorted(p.name for p in (resources / "glm").iterdir()) == ["zcode.cjs"]


def test_a_plain_binary_is_run_directly(tmp_path: Path) -> None:
    binary = tmp_path / "zcode"
    assert ZCodeBackend().command(binary) == [str(binary)]


def test_signed_in_means_the_coding_plan_key_not_the_app_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from assistant.coding_agents.zcode import app_signed_in

    monkeypatch.setenv("ZCODE_HOME", str(tmp_path))
    assert configured_login() == (False, "") and not app_signed_in()
    (tmp_path / "v2").mkdir()
    creds = tmp_path / "v2" / "credentials.json"
    # What `zcode logout` leaves behind: the app's OAuth entries, but no coding-plan key.
    creds.write_text(json.dumps({"oauth:zai:access_token": "SECRET-VALUE", "zcodejwttoken": "x"}))
    assert configured_login() == (False, "")  # Sign out must be able to flip this
    assert app_signed_in()
    creds.write_text(
        json.dumps({"oauth:zai:access_token": "x", "account-provider:coding-plan:a:b:api-key": "y"})
    )
    assert configured_login() == (True, "z.ai coding plan")


def test_login_output_gives_the_account_label() -> None:
    backend = ZCodeBackend()
    ok = (
        "Open this URL to sign in: https://chat.z.ai/x\n"
        "Login successful as me@example.com.\nModel: m\n"
    )
    assert backend.parse_account(ok) == {"email": "me@example.com"}
    assert backend.parse_account("Login successful as Sayan Mondal.\nModel: m\n") == {
        "name": "Sayan Mondal"
    }
    assert backend.parse_account("Login successful.\nModel: m\n") is None
    assert backend.parse_account("Error: nope") is None
    assert ClaudeBackend().parse_account(ok) is None


def test_backends_keep_sessions_apart() -> None:
    assert session_key(ClaudeBackend(), "chat-1") == "chat-1"
    assert session_key(ZCodeBackend(), "chat-1") == "zcode:chat-1"


FAKE = textwrap.dedent(
    """\
    #!{python}
    import json, sys
    args = sys.argv[1:]
    open({seen!r}, "w").write(json.dumps(args))
    emit = lambda m: print(json.dumps(m), flush=True)
    emit({{"type": "session.started", "sessionId": "sess_z1"}})
    emit({{"type": "tool_call_start", "toolCallId": "t1", "toolName": "edit",
          "input": {{"path": "src/a.py"}}}})
    emit({{"type": "tool_call_result", "toolCallId": "t1"}})
    emit({{"type": "turn.completed", "result": "Fixed the bug."}})
    """
)


@pytest.fixture
def fake_zcode(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "zcode"
    path.parent.mkdir()
    path.write_text(FAKE.format(python=sys.executable, seen=str(tmp_path / "seen.json")))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class Sink:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, kind: str, data: dict[str, Any]) -> None:
        self.frames.append((kind, data))


def make_toolkit(
    tmp_path: Path, fake: Path, *, allowed: str = "any"
) -> tuple[ClaudeCodeToolkit, Path, SessionStore]:
    project = tmp_path / "work" / "app"
    project.mkdir(parents=True)
    (tmp_path / "data").mkdir()
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
        zcode_allowed_providers=allowed,
    )
    backend = ZCodeBackend(settings.sani_data_dir)
    store = SessionStore(settings.sani_db_path)

    async def reader(_binary: Path | None) -> ClaudeStatus:
        return ClaudeStatus(installed=True, path=str(fake), version="0.16.9", signed_in=True)

    toolkit = ClaudeCodeToolkit(
        settings,
        backend=backend,
        locator=lambda **_: fake,
        status_reader=reader,
        store=store,
        runner_factory=lambda binary, version: ClaudeRunner(
            binary, version=version, backend=backend, tick_seconds=0.05, grace_seconds=2.0
        ),
    )
    return toolkit, project, store


async def test_a_zcode_run_streams_steps_and_resumes_in_its_own_namespace(
    tmp_path: Path, fake_zcode: Path
) -> None:
    toolkit, project, store = make_toolkit(tmp_path, fake_zcode)
    tool = toolkit.as_tool()
    assert tool.name == "zcode" and "ZCode" in tool.description
    schema: Any = tool.args_schema
    assert "ZCode" in schema.model_fields["task"].description
    sink = Sink()
    token = context.event_sink.set(sink)
    conv = context.conversation_id.set("chat-1")
    try:
        reply = await toolkit.run("fix the bug", str(project), mode="edit")
        again = await toolkit.run("and the docs", str(project), mode="edit")
    finally:
        context.event_sink.reset(token)
        context.conversation_id.reset(conv)
    assert reply.startswith("ZCode finished.")
    assert "Files changed: src/a.py" in reply and "ZCode said:\nFixed the bug." in reply
    assert "Session: sess_z1" in reply
    steps = [d["step"] for k, d in sink.frames if k == AGENT_PROGRESS and "step" in d]
    assert any(s["label"] == "Sani asked ZCode" for s in steps)
    assert any(s["label"] == "ZCode replied" for s in steps)
    # Stored under the zcode namespace, so it can never collide with Claude's session.
    assert await store.get_session("zcode:chat-1", str(project.resolve())) == "sess_z1"
    assert await store.get_session("chat-1", str(project.resolve())) == ""
    assert "continued session" in again
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen[seen.index("--resume") + 1] == "sess_z1"


async def test_a_nonzero_exit_without_a_result_is_a_failure(tmp_path: Path) -> None:
    broken = tmp_path / "bin" / "zcode"
    broken.parent.mkdir()
    broken.write_text(
        f"#!{sys.executable}\nimport sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n"
    )
    broken.chmod(broken.stat().st_mode | stat.S_IEXEC)
    toolkit, project, _ = make_toolkit(tmp_path, broken)
    reply = await toolkit.run("go", str(project))
    assert reply.startswith("ZCode did not finish successfully") and "boom" in reply


def test_options_share_folders_and_ceiling_with_claude_code() -> None:
    settings = Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        claude_code_dirs="/x",
        claude_code_permission="read",
        zcode_cli_enabled=True,
    )
    options = ZCodeBackend().options(settings)
    assert options.enabled and options.dirs == "/x" and options.permission == "read"


# ------------------------------------------------------------- plan/model picker


def _fake_app(tmp_path: Path) -> Path:
    resources = tmp_path / "ZCode.app" / "Contents" / "Resources"
    (resources / "glm").mkdir(parents=True)
    (resources / "config" / "provider").mkdir(parents=True)
    cjs = resources / "glm" / "zcode.cjs"
    cjs.write_text("// cli")
    config = {
        "config": {
            "providerConfigRules": {
                "providerRules": [
                    {
                        "providerId": "account:bm-start",
                        "providerName": "Start Plan",
                        "config": {"group": "bigmodel-family"},
                    },
                    {
                        "providerId": "account:zai-start",
                        "providerName": "Start Plan",
                        "config": {"group": "zai-family"},
                    },
                    {
                        "providerId": "account:empty",
                        "providerName": "Nothing",
                        "config": {"group": "zai-family"},
                    },
                ]
            },
            "modelConfigRules": {
                "modelRules": [
                    {"modelMatch": ".*", "config": {"properties": {"contextWindow": 200000}}},
                    {
                        "modelMatch": ".*glm-5\\.3(?:-flash)?",
                        "config": {
                            "properties": {"contextWindow": 1000000},
                            "optionSpecs": {"maxOutputTokens": {"max": 128000}},
                        },
                    },
                ],
                "builtinProviderModelRules": [
                    {
                        "providerId": "account:zai-start",
                        "modelId": "GLM-5.3-Flash",
                        "config": {"enabled": True},
                    },
                    {
                        "providerId": "account:zai-start",
                        "modelId": "GLM-5-Turbo",
                        "config": {"enabled": True},
                    },
                    {
                        "providerId": "account:zai-start",
                        "modelId": "GLM-5.2",
                        "config": {"enabled": False},
                    },
                    {
                        "providerId": "account:bm-start",
                        "modelId": "GLM-5.3",
                        "config": {"enabled": True},
                    },
                ],
            },
        }
    }
    (resources / "config" / "provider" / "zcode-builtin.json").write_text(json.dumps(config))
    return cjs


def test_catalog_lists_plans_with_model_sizes_and_skips_disabled(tmp_path: Path) -> None:
    from assistant.coding_agents.zcode_catalog import load_catalog

    plans = load_catalog(_fake_app(tmp_path))
    assert [p["id"] for p in plans] == ["account:zai-start", "account:bm-start"]  # Z.ai first
    start = plans[0]
    assert start["name"] == "Start Plan" and start["family_name"] == "Z.ai"
    models = {m["id"]: m for m in start["models"]}
    assert set(models) == {"GLM-5.3-Flash", "GLM-5-Turbo"}  # disabled and empty plans dropped
    assert models["GLM-5.3-Flash"]["context_window"] == 1_000_000  # case-insensitive rule
    assert models["GLM-5.3-Flash"]["max_output"] == 128_000
    assert models["GLM-5-Turbo"]["context_window"] == 200_000


def test_catalog_is_empty_when_the_app_is_missing(tmp_path: Path) -> None:
    from assistant.coding_agents.zcode_catalog import load_catalog

    assert load_catalog(None) == []
    assert load_catalog(tmp_path / "nope" / "zcode.cjs") == []


def test_default_selection_reads_only_the_selection_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from assistant.coding_agents.zcode_catalog import zcode_default_selection

    monkeypatch.setenv("ZCODE_HOME", str(tmp_path))
    assert zcode_default_selection() is None
    (tmp_path / "v2").mkdir()
    (tmp_path / "v2" / "provider_config.json").write_text(
        json.dumps(
            {
                "config": {
                    "defaultModelSelection": {
                        "providerId": "account:zai-start",
                        "modelId": "GLM-5.3-Flash",
                    },
                    "providerConfigRules": {"secret": "KEY-SHOULD-NEVER-APPEAR"},
                }
            }
        )
    )
    chosen = zcode_default_selection()
    assert chosen == {"provider": "account:zai-start", "model": "GLM-5.3-Flash"}
    assert "KEY-SHOULD-NEVER-APPEAR" not in json.dumps(chosen)


async def test_status_report_carries_catalog_and_the_saved_choice(tmp_path: Path) -> None:
    cjs = _fake_app(tmp_path)
    toolkit, _project, store = make_toolkit(tmp_path, cjs)
    report = await toolkit.status_report()
    assert report["backend"] == "zcode" and report["balances"] is None
    assert report["extra"]["catalog"][0]["id"] == "account:zai-start"
    assert report["selection"] == {}

    ok = await toolkit.handle_action(
        "zcode.select", {"provider": "account:zai-start", "model": "GLM-5.3-Flash"}
    )
    assert ok["selection"] == {"provider": "account:zai-start", "model": "GLM-5.3-Flash"}
    assert (await store.get_state("zcode_selection"))["model"] == "GLM-5.3-Flash"

    # Anything not in the catalog is refused and does not overwrite the saved choice.
    bad = await toolkit.handle_action("zcode.select", {"provider": "x", "model": "y"})
    assert "error" in bad
    again = await toolkit.status_report()
    assert again["selection"]["model"] == "GLM-5.3-Flash"


# ------------------------------------------------------------ app-driving helpers


async def test_zcode_choice_tells_the_agent_the_pick_the_limit_and_folders(
    tmp_path: Path,
) -> None:
    from assistant.coding_agents.zcode_app import build_tools

    toolkit, project, _store = make_toolkit(tmp_path, _fake_app(tmp_path))
    choice = next(t for t in build_tools(toolkit) if t.name == "zcode_choice")
    empty = await choice.ainvoke({})
    assert "none picked in Sani yet" in empty and str(project.parent.resolve()) in empty
    await toolkit.handle_action(
        "zcode.select", {"provider": "account:zai-start", "model": "GLM-5.3-Flash"}
    )
    text = await choice.ainvoke({})
    assert "Plan: Start Plan (Z.ai)" in text and "Model: GLM-5.3-Flash" in text
    assert "Edit automatically" in text  # default ceiling is "edit"


async def test_saved_balances_must_match_a_known_plan_and_model(tmp_path: Path) -> None:
    from assistant.coding_agents.zcode_app import build_tools

    toolkit, _project, store = make_toolkit(tmp_path, _fake_app(tmp_path))
    save = next(t for t in build_tools(toolkit) if t.name == "zcode_save_balances")
    result = await save.ainvoke(
        {
            "items": [
                {
                    "plan": "Start Plan",
                    "model": "glm-5.3-flash",
                    "remaining": 4_000_000,
                    "total": 5_000_000,
                    "expires": "Oct 4, 21:29",
                },
                {"plan": "Start Plan", "model": "Made-Up", "remaining": 1, "total": 2},
                {"plan": "Start Plan", "model": "GLM-5-Turbo", "remaining": 9, "total": 5},
                {"plan": "Nope Plan", "model": "GLM-5.3-Flash", "remaining": 1, "total": 2},
            ]
        }
    )
    assert result.startswith("Saved 1 balance figure(s).") and "3 figure(s)" in result
    saved = await store.get_state("zcode_balances")
    assert saved["items"] == [
        {
            "provider": "account:zai-start",
            "plan": "Start Plan",
            "model": "GLM-5.3-Flash",
            "remaining": 4_000_000,
            "total": 5_000_000,
            "expires": "Oct 4, 21:29",
        }
    ]
    report = await toolkit.status_report()
    assert report["balances"]["items"][0]["remaining"] == 4_000_000
    assert report["balances"]["as_of"] > 0
    none = await save.ainvoke({"items": [{"plan": "x", "model": "y", "remaining": 1, "total": 2}]})
    assert none.startswith("Nothing saved")
    assert (await store.get_state("zcode_balances"))["items"][0]["model"] == "GLM-5.3-Flash"


async def test_saved_account_is_validated_and_shown(tmp_path: Path) -> None:
    from assistant.coding_agents.zcode_app import build_tools

    toolkit, _project, _store = make_toolkit(tmp_path, _fake_app(tmp_path))
    save = next(t for t in build_tools(toolkit) if t.name == "zcode_save_account")
    assert (await toolkit.status_report())["account"] is None
    assert (await save.ainvoke({})).startswith("Nothing saved")
    assert (await save.ainvoke({"email": "not an email!"})).startswith("Nothing saved")
    assert await save.ainvoke({"name": "Sayan", "email": "me@example.com"}) == "Saved the account."
    account = (await toolkit.status_report())["account"]
    assert account["email"] == "me@example.com" and account["name"] == "Sayan"
    assert account["as_of"] > 0


def test_cli_mode_is_the_default_and_validated() -> None:
    base: dict[str, Any] = dict(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
    )
    assert Settings(**base).zcode_mode == "cli"
    with pytest.raises(Exception, match="ZCODE_MODE"):
        Settings(**base, zcode_mode="robot")


FAKE_LOGIN = textwrap.dedent(
    """\
    #!{python}
    import sys, time
    if sys.argv[1:2] == ["login"]:
        print("Open this URL to sign in: https://chat.z.ai/api/oauth/authorize?x=1", flush=True)
        time.sleep(0.3)
        print("Login successful as me@example.com.", flush=True)
        print("Model: GLM-5.3-Flash", flush=True)
    """
)


async def test_signing_in_through_sani_keeps_the_account_the_cli_printed(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "bin" / "zcode"
    fake.parent.mkdir()
    fake.write_text(FAKE_LOGIN.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    toolkit, _project, store = make_toolkit(tmp_path, fake)
    started = await toolkit.handle_action("zcode.login_start", {})
    assert started["login"]["state"] == "waiting"
    for _ in range(100):
        await asyncio.sleep(0.1)
        report = await toolkit.status_report()
        if report["login"]["state"] == "succeeded":
            break
    assert report["login"]["state"] == "succeeded"
    assert report["account"]["email"] == "me@example.com"
    assert (await store.get_state("zcode_account"))["email"] == "me@example.com"


# A real `zcode -p --json` result (ids shortened), captured from ZCode 0.16.9.
REAL_RESULT = """{
  "sessionId": "sess_582b3b0d",
  "traceId": "c2ffd6e8",
  "turnId": "turn_9dbf17f8",
  "response": "\\n\\npong",
  "usage": {
    "source": "provider",
    "modelRequestCount": 1,
    "inputTokens": 22905,
    "outputTokens": 16,
    "totalTokens": 22921,
    "reasoningTokens": 12
  },
  "eventCount": 18,
  "projection": {
    "status": "idle",
    "turnCount": 1,
    "totalTokenCount": 22921,
    "contextUsed": 22921,
    "contextWindow": 200000
  }
}
"""


def test_parser_reads_the_real_pretty_printed_result_object() -> None:
    parser = ZCodeParser("/work")
    events: list[Any] = []
    for line in REAL_RESULT.splitlines(keepends=True):
        events += parser.feed(line)
    assert [type(e).__name__ for e in events] == ["Init", "Final"]
    final = events[-1]
    assert final.ok and final.text == "pong" and final.session_id == "sess_582b3b0d"
    assert final.context_tokens == 22921 and final.context_window == 200000
    assert final.turns == 1 and final.usage["totalTokens"] == 22921


def test_parser_still_reads_compact_one_line_results_and_ignores_stderr_noise() -> None:
    parser = ZCodeParser()
    assert parser.feed("ZCode Built-in skipped (not-due)") == []
    one_line = json.dumps(json.loads(REAL_RESULT))
    events = parser.feed(one_line + "\n")
    assert isinstance(events[-1], Final) and events[-1].text == "pong"


FAKE_REAL = textwrap.dedent(
    """\
    #!{python}
    import sys
    open({seen!r}, "w").write(" ".join(sys.argv[1:]))
    sys.stdout.write({body!r})
    """
)


async def test_a_run_returns_the_answer_text_from_the_real_result_shape(tmp_path: Path) -> None:
    fake = tmp_path / "bin" / "zcode"
    fake.parent.mkdir()
    fake.write_text(
        FAKE_REAL.format(python=sys.executable, seen=str(tmp_path / "seen.txt"), body=REAL_RESULT)
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    toolkit, project, store = make_toolkit(tmp_path, fake)
    conv = context.conversation_id.set("chat-9")
    try:
        reply = await toolkit.run("Reply with the single word: pong", str(project), mode="read")
    finally:
        context.conversation_id.reset(conv)
    assert reply.startswith("ZCode finished.")
    assert "ZCode said:\npong" in reply and "Session: sess_582b3b0d" in reply
    assert "conversation 11% full" in reply  # 22921 / 200000
    assert await store.get_session("zcode:chat-9", str(project.resolve())) == "sess_582b3b0d"
    seen = (tmp_path / "seen.txt").read_text()
    assert "--mode plan" in seen and "--json" in seen


def test_guide_points_folder_and_limit_changes_at_sanis_own_settings() -> None:
    zcode = ZCodeBackend().guide()
    assert "Settings → Claude Code" in zcode  # not rewritten to "Settings → ZCode"
    flat = " ".join(zcode.split())  # the guide wraps lines
    assert "`zcode`" in zcode and "Do not open the coding tool's app" in flat
    assert "`claude_code`" not in zcode


async def test_refusals_say_the_folder_list_and_limit_are_sanis_not_the_tools(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "bin" / "zcode"
    fake.parent.mkdir()
    fake.write_text(
        FAKE_REAL.format(python=sys.executable, seen=str(tmp_path / "s"), body=REAL_RESULT)
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    toolkit, project, _ = make_toolkit(tmp_path, fake)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    refused = await toolkit.run("go", str(outside))
    assert "Sani's own" in refused and "not a setting inside ZCode" in refused
    ok = await toolkit.run("go", str(project), mode="run")  # the ceiling here is "edit"
    assert "limited to “edit” by Sani's own setting" in ok


async def test_a_run_that_could_not_run_commands_warns_that_its_output_is_unverified(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "bin" / "zcode"
    fake.parent.mkdir()
    fake.write_text(
        FAKE_REAL.format(python=sys.executable, seen=str(tmp_path / "s"), body=REAL_RESULT)
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    toolkit, project, _ = make_toolkit(tmp_path, fake)
    edit_reply = await toolkit.run("go", str(project), mode="edit")
    assert "Verify: this run could not run commands" in edit_reply
    assert "its own claim, not a result" in edit_reply
    run_reply = await toolkit.run("go", str(project), mode="run")  # ceiling is "edit"
    assert "Verify:" in run_reply  # clamped to edit, so still unverified


# ------------------------------------------------------------ "only my Z.ai plan" guard

FAKE_ENGINE = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys
    here = os.path.dirname(os.path.abspath(__file__))
    if sys.argv[1:2] == ["app-server"]:
        open(os.path.join(here, "probes.txt"), "a").write("x")
        mode = open(os.path.join(here, "mode.txt")).read().strip()
        if mode == "broken":
            print("not the protocol", flush=True)
            sys.exit(1)
        request = json.loads(sys.stdin.readline())
        print(json.dumps({{"id": "server-1", "method": "session/requestRuntimePreferences",
                          "params": {{}}}}), flush=True)
        json.loads(sys.stdin.readline())
        models = {{
            "zai": {{"current": {{"providerId": "account:zai-start-plan",
                                  "modelId": "GLM-5.3-Flash"}}}},
            "custom": {{"current": {{"providerId": "new-provider", "modelId": "Qwen3.6"}},
                        "available": [{{"ref": {{"providerId": "new-provider",
                                                 "modelId": "Qwen3.6"}},
                                        "providerLabel": "New provider"}}]}},
            "none": {{"available": []}},
        }}
        print(json.dumps({{"id": request["id"],
                          "result": {{"settings": {{"model": models[mode]}}}}}}), flush=True)
        sys.stdin.read()
    else:
        open(os.path.join(here, "ran.txt"), "w").write(" ".join(sys.argv[1:]))
        sys.stdout.write({body!r})
    """
)


def engine(tmp_path: Path, mode: str) -> Path:
    fake = tmp_path / "bin" / "zcode"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(FAKE_ENGINE.format(python=sys.executable, body=REAL_RESULT))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (fake.parent / "mode.txt").write_text(mode)
    return fake


@pytest.fixture(autouse=True)
def _fresh_probe_cache() -> None:
    from assistant.coding_agents.zcode_model import clear_cache

    clear_cache()


async def test_guard_lets_a_zai_plan_run_and_says_which_model_was_used(tmp_path: Path) -> None:
    fake = engine(tmp_path, "zai")
    toolkit, project, _ = make_toolkit(tmp_path, fake, allowed="zai")
    reply = await toolkit.run("go", str(project), mode="read")
    assert reply.startswith("ZCode finished.")
    assert "Model: account:zai-start-plan/GLM-5.3-Flash (your Z.ai plan)" in reply
    assert (fake.parent / "ran.txt").exists()


async def test_guard_refuses_a_custom_provider_and_spends_nothing(tmp_path: Path) -> None:
    fake = engine(tmp_path, "custom")
    toolkit, project, _ = make_toolkit(tmp_path, fake, allowed="zai")
    reply = await toolkit.run("go", str(project), mode="read")
    assert reply.startswith("Not started.")
    assert "new-provider/Qwen3.6" in reply and "a custom provider" in reply
    assert "nothing was spent" in reply and "ZCODE_ALLOWED_PROVIDERS=any" in reply
    assert not (fake.parent / "ran.txt").exists()  # the real run never started


async def test_guard_refuses_when_zcode_has_no_model_or_cannot_be_asked(tmp_path: Path) -> None:
    fake = engine(tmp_path, "none")
    toolkit, project, _ = make_toolkit(tmp_path, fake, allowed="zai")
    none = await toolkit.run("go", str(project), mode="read")
    assert none.startswith("Not started.") and "no model selected" in none
    from assistant.coding_agents.zcode_model import clear_cache

    clear_cache()
    (fake.parent / "mode.txt").write_text("broken")
    broken = await toolkit.run("go", str(project), mode="read")
    assert broken.startswith("Not started.") and "could not confirm" in broken
    assert not (fake.parent / "ran.txt").exists()


async def test_any_skips_the_check_and_the_check_is_cached(tmp_path: Path) -> None:
    fake = engine(tmp_path, "custom")
    toolkit, project, _ = make_toolkit(tmp_path, fake, allowed="any")
    assert (await toolkit.run("go", str(project), mode="read")).startswith("ZCode finished.")
    assert not (fake.parent / "probes.txt").exists()  # no probe at all when anything is allowed

    fake2 = engine(tmp_path / "second", "zai")
    toolkit2, project2, _ = make_toolkit(tmp_path / "second", fake2, allowed="zai")
    await toolkit2.run("one", str(project2), mode="read")
    await toolkit2.run("two", str(project2), mode="read")
    assert (fake2.parent / "probes.txt").read_text() == "x"  # asked once, then cached
