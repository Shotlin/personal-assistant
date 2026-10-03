"""Run one Claude Code turn under supervision.

``claude -p`` is started in the project folder with the user's own login and a
fixed permission preset chosen *before* the run (nobody can answer a prompt in
this mode). Its stream is parsed into events, handed to the caller, and fed to
the watchdog; a trip, a cancel, or the caller's deadline stops the run cleanly
(Ctrl+C first, then terminate, then kill) so the turn is recorded and resumable.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from assistant.claude_code.events import (
    AssistantText,
    ClaudeEvent,
    Final,
    Init,
    ToolEnd,
    ToolStart,
)
from assistant.claude_code.locate import child_environment
from assistant.claude_code.parser import StreamParser
from assistant.claude_code.redact import screen
from assistant.claude_code.watchdog import Watchdog

Permission = Literal["read", "edit", "run"]

_READ_TOOLS = ("Read", "Glob", "Grep", "LS")
_EDIT_TOOLS = (*_READ_TOOLS, "Edit", "MultiEdit", "Write", "NotebookEdit", "TodoWrite")
#: Never allowed, whatever the preset. Best effort against the worst commands;
#: the real boundary is the preset and the project folder.
_ALWAYS_DENIED = (
    "Bash(sudo *)",
    "Bash(rm -rf /*)",
    "Bash(rm -rf ~*)",
    "Bash(git push *)",
    "Bash(git push)",
    "Bash(* | sh)",
    "Bash(* | bash)",
)

_FILE_CHANGING = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_PERMISSION_PROMPTS_NONE_MIN = (2, 1, 259)
_STREAM_LIMIT = 16 * 1024 * 1024

EventCallback = Callable[[ClaudeEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RunRequest:
    prompt: str
    cwd: Path
    permission: Permission = "edit"
    session_id: str | None = None
    #: Resume a copy of the session instead of the session itself.
    fork: bool = False
    max_turns: int = 30
    model: str = ""
    #: Image/file paths the model should look at.
    attachments: tuple[Path, ...] = ()
    #: Extra folders Claude Code may read (where the attachments live).
    extra_dirs: tuple[Path, ...] = ()


@dataclass(slots=True)
class RunOutcome:
    ok: bool = False
    session_id: str = ""
    final: Final | None = None
    #: Plain-words reason the supervisor or a limit ended the run early.
    stopped_reason: str = ""
    cancelled: bool = False
    error: str = ""
    text: str = ""
    steps: int = 0
    failed_steps: int = 0
    files_changed: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    exit_code: int | None = None


def build_args(request: RunRequest, *, version: tuple[int, int, int] | None = None) -> list[str]:
    """The exact command-line for a run. Pure, so it is tested without a process."""
    args = [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--max-turns",
        str(request.max_turns),
    ]
    if request.permission == "read":
        args += ["--permission-mode", "dontAsk", "--allowedTools", ",".join(_READ_TOOLS)]
    elif request.permission == "edit":
        args += ["--permission-mode", "acceptEdits", "--allowedTools", ",".join(_EDIT_TOOLS)]
    else:
        args += [
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            ",".join((*_EDIT_TOOLS, "Bash")),
        ]
    args += ["--disallowedTools", ",".join(_ALWAYS_DENIED)]
    if version is not None and version >= _PERMISSION_PROMPTS_NONE_MIN:
        args += ["--permission-prompts", "none"]
    if request.session_id:
        args += ["--resume", request.session_id]
        if request.fork:
            args += ["--fork-session"]
    if request.model:
        args += ["--model", request.model]
    for folder in request.extra_dirs:
        args += ["--add-dir", str(folder)]
    return args


def build_prompt(request: RunRequest) -> str:
    if not request.attachments:
        return request.prompt
    listing = "\n".join(f"- {path}" for path in request.attachments)
    return f"{request.prompt}\n\nAttached files (look at them with the Read tool):\n{listing}"


async def _terminate(process: asyncio.subprocess.Process, *, grace: float) -> None:
    """Ctrl+C first so the turn is recorded, then escalate."""
    if process.returncode is not None:
        return

    def signal_group(sig: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, sig)

    for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, 3.0), (signal.SIGKILL, 3.0)):
        signal_group(sig)
        try:
            await asyncio.wait_for(process.wait(), timeout=wait)
            return
        except TimeoutError:
            continue


class ClaudeRunner:
    def __init__(
        self,
        binary: Path,
        *,
        version: tuple[int, int, int] | None = None,
        tick_seconds: float = 1.0,
        grace_seconds: float = 8.0,
    ) -> None:
        self._binary = binary
        self._version = version
        self._tick = tick_seconds
        self._grace = grace_seconds

    async def run(
        self,
        request: RunRequest,
        *,
        on_event: EventCallback,
        watchdog: Watchdog,
        cancel: asyncio.Event,
    ) -> RunOutcome:
        outcome = RunOutcome(session_id=request.session_id or "")
        args = build_args(request, version=self._version)
        process = await asyncio.create_subprocess_exec(
            str(self._binary),
            *args,
            cwd=str(request.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_environment(),
            start_new_session=True,
            limit=_STREAM_LIMIT,
        )
        assert process.stdin is not None and process.stdout is not None  # noqa: S101
        stderr_task = asyncio.create_task(self._drain(process.stderr))
        parser = StreamParser(str(request.cwd))
        pending: dict[str, ToolStart] = {}
        assistant_text: list[str] = []
        stopping = False

        async def stop(reason: str = "", *, cancelled: bool = False) -> None:
            nonlocal stopping
            if stopping:
                return
            stopping = True
            outcome.stopped_reason = outcome.stopped_reason or reason
            outcome.cancelled = outcome.cancelled or cancelled
            await _terminate(process, grace=self._grace)

        try:
            process.stdin.write(build_prompt(request).encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            while True:
                if cancel.is_set() and not stopping:
                    await stop(cancelled=True)
                try:
                    raw = await asyncio.wait_for(process.stdout.readline(), timeout=self._tick)
                except TimeoutError:
                    reason = watchdog.check_time()
                    if reason and not stopping:
                        await stop(reason)
                    continue
                if not raw:
                    break
                watchdog.touch()
                for event in parser.feed(raw.decode("utf-8", errors="replace")):
                    self._track(event, outcome, pending, assistant_text)
                    await on_event(event)
                    reason = watchdog.observe(event)
                    if reason and not stopping:
                        await stop(reason)
            await process.wait()
        except asyncio.CancelledError:
            await asyncio.shield(stop(cancelled=True))
            raise
        finally:
            if process.returncode is None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(_terminate(process, grace=1.0))
            stderr_text = await stderr_task
        outcome.exit_code = process.returncode
        final_text = outcome.final.text if outcome.final else ""
        outcome.text = final_text or "\n".join(assistant_text)
        if outcome.final is not None:
            outcome.ok = outcome.final.ok and not outcome.stopped_reason and not outcome.cancelled
            outcome.session_id = outcome.final.session_id or outcome.session_id
        elif not outcome.stopped_reason and not outcome.cancelled:
            message = stderr_text.strip() or "Claude Code ended without a result."
            outcome.error = screen(message, limit=600)
        return outcome

    # ------------------------------------------------------------------

    @staticmethod
    def _track(
        event: ClaudeEvent,
        outcome: RunOutcome,
        pending: dict[str, ToolStart],
        assistant_text: list[str],
    ) -> None:
        if isinstance(event, Init):
            outcome.session_id = event.session_id or outcome.session_id
        elif isinstance(event, ToolStart):
            outcome.steps += 1
            pending[event.id] = event
            if event.name == "Bash":
                outcome.commands.append(event.label)
        elif isinstance(event, ToolEnd):
            start = pending.pop(event.id, None)
            if not event.ok:
                outcome.failed_steps += 1
            elif start is not None and start.name in _FILE_CHANGING and start.path:
                if start.path not in outcome.files_changed:
                    outcome.files_changed.append(start.path)
        elif isinstance(event, AssistantText):
            assistant_text.append(event.text)
        elif isinstance(event, Final):
            outcome.final = event

    @staticmethod
    async def _drain(stream: asyncio.StreamReader | None) -> str:
        if stream is None:
            return ""
        tail = b""
        while chunk := await stream.read(4096):
            tail = (tail + chunk)[-4096:]
        return tail.decode("utf-8", errors="replace")
