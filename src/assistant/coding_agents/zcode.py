"""ZCode (Z.ai) as a backend: ``zcode -p <prompt> --json`` under the user's own login.

ZCode ships a command-line program inside its desktop app (``zcode.cjs``, run
with Node). It is not on PATH, and launched from inside the app bundle it
cannot find its built-in provider file, so a tiny shim folder in Sani's data
directory (a symlink to ``zcode.cjs`` plus a copy of that one JSON file) is used
instead. The app itself is never modified.

UNVERIFIED: the shape of ``--json`` output was not observed against a working
model, so ``ZCodeParser`` is deliberately tolerant and a clean exit counts as
success (``final_from_exit``). Confirm against a real run before relying on the
step-by-step view.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assistant.claude_code.events import (
    AssistantText,
    ClaudeEvent,
    Final,
    Init,
    TextDelta,
    ToolEnd,
    ToolStart,
)
from assistant.claude_code.labels import MUTATING_TOOLS, describe_tool, fingerprint
from assistant.claude_code.locate import ClaudeStatus, child_environment, parse_version
from assistant.claude_code.redact import screen
from assistant.coding_agents.backend import AgentOptions, LineParser, Preflight
from assistant.coding_agents.guide import GUIDE, for_backend
from assistant.coding_agents.zcode_catalog import load_catalog, zcode_default_selection
from assistant.coding_agents.zcode_model import describe, is_zai_plan, probe_default_model

if TYPE_CHECKING:
    from assistant.claude_code.runner import RunRequest
    from assistant.settings import Settings

_APP_CJS = (
    "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",
    "~/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",
)
_BUILTIN_REL = Path("provider") / "zcode-builtin.json"
#: Whole tools ZCode must not have in a given mode (it cannot answer prompts here).
_NO_SHELL = "Bash"
_NO_WRITE = "Bash Edit Write MultiEdit NotebookEdit"

_CANON = {
    "bash": "Bash",
    "shell": "Bash",
    "run_command": "Bash",
    "read": "Read",
    "read_file": "Read",
    "write": "Write",
    "write_file": "Write",
    "edit": "Edit",
    "edit_file": "Edit",
    "multiedit": "MultiEdit",
    "grep": "Grep",
    "search": "Grep",
    "glob": "Glob",
    "ls": "LS",
    "list": "LS",
}


def _home() -> Path:
    return Path(os.environ.get("ZCODE_HOME") or Path.home() / ".zcode").expanduser()


def find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    for pattern in (".nvm/versions/node/*/bin/node",):
        matches = sorted(Path.home().glob(pattern))
        if matches:
            return str(matches[-1])
    for absolute in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if Path(absolute).is_file():
            return absolute
    return None


class ZCodeBackend:
    key = "zcode"
    name = "ZCode"
    tool_name = "zcode"
    sign_in_hint = (
        "ZCode isn't signed in on this Mac. Ask the user to run `zcode login` once in "
        "Terminal (or sign in inside the ZCode app); then try again."
    )
    prompt_on_stdin = False
    final_from_exit = True
    action_prefix = "zcode"
    login_url_pattern = r"https://\S+"
    supports_usage = False

    def __init__(self, data_dir: str = "") -> None:
        self._data_dir = data_dir

    # -- settings ------------------------------------------------------------

    def options(self, settings: Settings) -> AgentOptions:
        return AgentOptions(
            enabled=settings.zcode_cli_enabled,
            binary=settings.zcode_cli_binary,
            # Folders and the permission ceiling are shared with Claude Code on purpose:
            # one list of places Sani may work, one ceiling.
            dirs=settings.claude_code_dirs,
            permission=settings.claude_code_permission,
            model=settings.zcode_cli_model,
            effort=settings.zcode_cli_effort,
            run_seconds=settings.zcode_cli_run_seconds,
        )

    # -- finding it ----------------------------------------------------------

    def locate(self, override: str) -> Path | None:
        if override.strip():
            candidate = Path(override).expanduser()
            return candidate if candidate.is_file() else None
        on_path = shutil.which("zcode")
        if on_path:
            return Path(on_path)
        for raw in _APP_CJS:
            candidate = Path(raw).expanduser()
            if candidate.is_file():
                return candidate
        return None

    def command(self, binary: Path) -> list[str]:
        if binary.suffix != ".cjs":
            return [str(binary)]
        node = find_node()
        if node is None:
            raise OSError("ZCode needs Node.js, and none was found on this Mac.")
        return [node, str(self._shimmed(binary))]

    def _shimmed(self, cjs: Path) -> Path:
        """``zcode.cjs`` or, when it cannot find its provider file, a shim next to a copy."""
        if (cjs.parent / _BUILTIN_REL).is_file():
            return cjs
        source = cjs.resolve().parent.parent / "config" / _BUILTIN_REL
        if not source.is_file() or not self._data_dir:
            return cjs
        shim = Path(self._data_dir) / "zcode-shim"
        (shim / "provider").mkdir(parents=True, exist_ok=True)
        target = shim / _BUILTIN_REL
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, target)
        link = shim / "zcode.cjs"
        if link.is_symlink() and Path(os.readlink(link)) != cjs:
            link.unlink()
        if not link.exists() and not link.is_symlink():
            link.symlink_to(cjs)
        return link

    # -- status --------------------------------------------------------------

    async def read_status(self, binary: Path | None) -> ClaudeStatus:
        if binary is None:
            return ClaudeStatus(
                installed=False,
                detail="ZCode isn't installed. Install the ZCode app, then sign in once.",
            )
        try:
            command = self.command(binary)
        except OSError as problem:
            return ClaudeStatus(installed=True, path=str(binary), detail=str(problem))
        code, out = await _run_quiet([*command, "--version"], timeout=20)
        if code != 0:
            return ClaudeStatus(installed=True, path=str(binary), detail="ZCode didn't start.")
        parsed = parse_version(out)
        version = ".".join(str(n) for n in parsed) if parsed else ""
        signed_in, method = configured_login()
        detail = ""
        if not signed_in:
            detail = (
                "Signed out of the Coding Plan key Sani uses (the ZCode app itself may still be "
                "signed in). Press Sign in."
                if app_signed_in()
                else "Not signed in. Press Sign in."
            )
        return ClaudeStatus(
            installed=True,
            path=str(binary),
            version=version,
            signed_in=signed_in,
            auth_method=method,
            detail=detail,
        )

    async def preflight(self, binary: Path, workspace: Path, settings: Settings) -> Preflight:
        """Refuse to run unless ZCode would use a Z.ai plan (the default policy).

        ``zcode -p`` cannot be told which model to use, so ZCode itself is asked first. If it
        cannot be asked, the run does not start: the point of the guard is to never guess.
        """
        if settings.zcode_allowed_providers == "any":
            return Preflight()
        probe_dir = Path(settings.sani_data_dir or tempfile.gettempdir()) / "zcode-probe"
        probe = await probe_default_model(
            self.command(binary), self.environment(settings), probe_dir
        )
        fix = (
            "Set a Z.ai plan model as ZCode's default in the ZCode app, or allow custom "
            "providers on purpose with ZCODE_ALLOWED_PROVIDERS=any."
        )
        if probe is None:
            return Preflight(
                refusal=(
                    "I could not confirm which account ZCode would use, so I did not start it "
                    "(Sani only runs ZCode on your Z.ai plan). " + fix
                )
            )
        if not probe.provider:
            offered = ", ".join(sorted({f"{p}/{m}" for p, m, _ in probe.available})) or "none"
            return Preflight(
                refusal=(
                    "ZCode has no model selected right now (it offers: " + offered + "), so "
                    "nothing was run. " + fix
                )
            )
        if not is_zai_plan(probe.provider):
            return Preflight(
                refusal=(
                    f"ZCode would use {probe.ref}, {describe(probe.provider)}, not your Z.ai "
                    "plan, so I did not run it and nothing was spent. " + fix
                )
            )
        return Preflight(note=f"Model: {probe.ref} ({describe(probe.provider)})")

    def parse_account(self, login_output: str) -> dict[str, str] | None:
        match = _LOGIN_OK.search(login_output)
        if match is None:
            return None
        label = match.group(1).strip()
        if not label:
            return None
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", label):
            return {"email": label}
        return {"name": label}

    def extra_status(self, binary: Path | None) -> dict[str, Any]:
        return {
            # The ZCode *app* can be signed in while Sani's Coding Plan key is not.
            "app_signed_in": app_signed_in(),
            "catalog": load_catalog(binary),
            "zcode_default": zcode_default_selection(),
        }

    def login_args(self) -> list[str]:
        # --no-browser prints the Z.AI OAuth link so Sani can show it; the CLI waits for
        # the browser to reach its local callback and stores the credential itself.
        return ["login", "--no-browser"]

    def logout_args(self) -> list[str]:
        return ["logout"]

    # -- running it ----------------------------------------------------------

    def build_args(self, request: RunRequest, version: tuple[int, int, int] | None) -> list[str]:
        args = ["-p", request.prompt, "--json", "--no-color", "--cwd", str(request.cwd)]
        if request.permission == "read":
            args += ["--mode", "plan", "--disallowed-tools", _NO_WRITE]
        elif request.permission == "edit":
            args += ["--mode", "edit", "--disallowed-tools", _NO_SHELL]
        else:
            args += ["--mode", "yolo"]
        if request.session_id:
            args += ["--resume", request.session_id]
        for path in request.attachments:
            args += ["--attach", str(path)]
        return args

    def environment(self, settings: Settings | None = None) -> dict[str, str]:
        env = child_environment()
        for name in ("ZCODE_HOME", "ZCODE_PROVIDER", "ZCODE_NODE"):
            value = os.environ.get(name)
            if value:
                env[name] = value
        model = settings.zcode_cli_model if settings is not None else ""
        if model:
            env["ZCODE_MODEL"] = model
        return env

    def make_parser(self, cwd: str) -> LineParser:
        return ZCodeParser(cwd)

    def guide(self) -> str:
        return for_backend(GUIDE, self.name, self.tool_name)


_LOGIN_OK = re.compile(r"^Login successful as (.+?)\.?\s*$", re.MULTILINE)


def _credential_names() -> list[str]:
    """Names (never values) of what ZCode has stored. Empty if unreadable."""
    with contextlib.suppress(OSError, ValueError, AttributeError):
        path = _home() / "v2" / "credentials.json"
        return [str(name) for name in json.loads(path.read_text(encoding="utf-8")).keys()]
    return []


def app_signed_in() -> bool:
    """The ZCode app's own Z.ai sign-in. `zcode logout` does NOT remove this."""
    return any(name.startswith("oauth:zai:access_token") for name in _credential_names())


def configured_login() -> tuple[bool, str]:
    """Whether Sani's Coding Plan key exists: what Sign in / Sign out controls.

    ``zcode logout`` removes the ``account-provider:`` key only, so that key is the one
    honest signal. Names only, never values.
    """
    if any(name.startswith("account-provider:") for name in _credential_names()):
        return True, "z.ai coding plan"
    return False, ""


async def _run_quiet(command: list[str], *, timeout: float) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=child_environment(),
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        return 124, ""
    return process.returncode or 0, out.decode("utf-8", errors="replace")


# --------------------------------------------------------------------- parser


def _first(message: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if message.get(key) not in (None, ""):
            return message[key]
    return None


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text_of(item) for item in value)
    if isinstance(value, dict):
        return _text_of(_first(value, "text", "content", "message", "output"))
    return ""


class ZCodeParser:
    """Reads ZCode's ``--json`` lines. Unknown shapes are skipped, never raised."""

    _START = ("start", "call", "use", "begin", "request")
    _END = ("end", "result", "done", "complete", "finish", "error", "output")
    _FINAL = ("result", "final", "done", "complete", "completed", "finish", "finished", "end")

    def __init__(self, cwd: str = "") -> None:
        self._cwd = cwd
        self._session = ""
        self._names: dict[str, str] = {}
        self._counter = 0
        #: `zcode -p --json` prints ONE pretty-printed result object (verified against a
        #: real run), so lines are collected until a whole JSON value parses.
        self._buffer: list[str] = []

    def feed(self, line: str) -> list[ClaudeEvent]:
        stripped = line.strip()
        message: Any = None
        if stripped.startswith("{") and stripped.endswith("}"):
            # A whole value on one line wins over (and clears) a half-collected buffer.
            with contextlib.suppress(json.JSONDecodeError):
                message = json.loads(stripped)
        if message is None:
            if not self._buffer and not stripped.startswith("{"):
                return []
            self._buffer.append(line.rstrip("\n"))
            try:
                message = json.loads("\n".join(self._buffer))
            except json.JSONDecodeError:
                # Not complete yet. A never-ending buffer is bounded.
                if len(self._buffer) > 2000:
                    self._buffer.clear()
                return []
        self._buffer.clear()
        if not isinstance(message, dict):
            return []
        if "response" in message and ("sessionId" in message or "turnId" in message):
            return self._result(message)
        events: list[ClaudeEvent] = []
        session = str(_first(message, "sessionId", "session_id") or "")
        if session and session != self._session:
            self._session = session
            events.append(Init(session_id=session, model=str(message.get("model") or "")))
        kind = str(_first(message, "type", "event", "kind") or "").lower().replace("-", "_")
        if not kind:
            return events
        if "tool" in kind:
            events += self._tool(kind, message)
        elif self._is_final(kind):
            events.append(self._final(kind, message))
        elif kind == "error":
            events.append(self._error(message))
        elif "delta" in kind or "chunk" in kind:
            text = _text_of(_first(message, "delta", "text", "content"))
            if text:
                events.append(TextDelta(text=text))
        elif any(word in kind for word in ("text", "message", "assistant", "content")):
            text = _text_of(_first(message, "text", "content", "message"))
            if text and str(message.get("role") or "assistant") == "assistant":
                events.append(AssistantText(text=text))
        return events

    def _result(self, message: dict[str, Any]) -> list[ClaudeEvent]:
        """The final object of `zcode -p --json`: the answer, the session and usage."""
        session = str(message.get("sessionId") or "")
        self._session = session or self._session
        projection = message.get("projection")
        projection = projection if isinstance(projection, dict) else {}
        usage = message.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        used = projection.get("contextUsed")
        window = projection.get("contextWindow")
        text = _text_of(message.get("response")).strip()
        turns = projection.get("turnCount")
        return [
            Init(session_id=self._session),
            Final(
                ok=True,
                subtype="success",
                text=screen(text, limit=20000) if text else "",
                session_id=self._session,
                turns=turns if isinstance(turns, int) else 0,
                context_tokens=used if isinstance(used, int) else None,
                context_window=window if isinstance(window, int) else None,
                usage=dict(usage),
            ),
        ]

    def _is_final(self, kind: str) -> bool:
        parts = kind.replace(".", "_").split("_")
        return kind in self._FINAL or (
            parts[-1] in ("completed", "complete", "done", "result", "finished", "final")
            and any(word in parts for word in ("turn", "session", "run", "task", "agent"))
        )

    def _tool(self, kind: str, message: dict[str, Any]) -> list[ClaudeEvent]:
        call_id = str(_first(message, "toolCallId", "tool_call_id", "callId", "id") or "")
        ending = any(word in kind for word in self._END) and not any(
            word in kind for word in ("start", "begin", "request")
        )
        if not ending and any(word in kind for word in self._START):
            raw = str(_first(message, "toolName", "tool_name", "name", "tool") or "tool")
            name = _CANON.get(raw.lower(), raw)
            raw_input = _first(message, "input", "args", "arguments", "params")
            tool_input: dict[str, Any] = dict(raw_input) if isinstance(raw_input, dict) else {}
            if "file_path" not in tool_input and isinstance(tool_input.get("path"), str):
                tool_input["file_path"] = tool_input["path"]
            if not call_id:
                self._counter += 1
                call_id = f"call-{self._counter}"
            self._names[call_id] = name
            label, detail, path = describe_tool(name, tool_input, self._cwd)
            return [
                ToolStart(
                    id=call_id,
                    name=name,
                    label=label,
                    detail=detail,
                    fingerprint=fingerprint(name, tool_input),
                    mutating=name in MUTATING_TOOLS,
                    path=path,
                )
            ]
        if ending and call_id:
            status = str(message.get("status") or "").lower()
            failed = bool(
                _first(message, "is_error", "isError", "error")
                or "error" in kind
                or status in {"error", "failed", "failure"}
            )
            summary = screen(_text_of(_first(message, "error", "output", "result")), limit=600)
            return [ToolEnd(id=call_id, ok=not failed, summary=summary if failed else "")]
        return []

    def _final(self, kind: str, message: dict[str, Any]) -> Final:
        status = str(message.get("status") or message.get("subtype") or "").lower()
        failed = bool(_first(message, "is_error", "isError", "error")) or status in {
            "error",
            "failed",
            "failure",
        }
        text = _text_of(_first(message, "result", "text", "output", "message", "content"))
        return Final(
            ok=not failed,
            subtype=status or ("error" if failed else "success"),
            text=screen(text, limit=20000) if text else "",
            session_id=self._session,
        )

    def _error(self, message: dict[str, Any]) -> Final:
        text = _text_of(_first(message, "message", "error", "text")) or "ZCode reported an error."
        return Final(
            ok=False, subtype="error", text=screen(text, limit=600), session_id=self._session
        )
