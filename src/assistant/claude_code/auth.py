"""Sign in and out of the user's Claude Code from inside Sani.

``claude auth login`` is run as a child process. Started without a terminal it
opens the browser, prints the sign-in link, and waits. When the browser can reach
Claude Code's local callback the command finishes by itself; when the browser
shows a code instead, the code is written to the command's input. Sani never
sees a password or a token: the sign-in happens on Anthropic's own page and the
credential is stored by Claude Code itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assistant.coding_agents.backend import Backend

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CODE = re.compile(r"[A-Za-z0-9._~#:/+=-]{6,600}")
_LOGIN_TIMEOUT_SECONDS = 300.0

IDLE = "idle"
WAITING = "waiting"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class LoginView:
    state: str = IDLE
    url: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"state": self.state, "url": self.url, "message": self.message}


class LoginSession:
    """At most one sign-in at a time."""

    def __init__(
        self,
        backend: Backend | None = None,
        on_success: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        if backend is None:
            from assistant.coding_agents.claude import ClaudeBackend

            backend = ClaudeBackend()
        self._backend = backend
        #: Called with what the login command printed once it succeeded (for the account label).
        self._on_success = on_success
        self._url = re.compile(backend.login_url_pattern)
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._view = LoginView()
        self._buffer = ""
        self._started = 0.0

    def view(self) -> LoginView:
        return self._view

    @property
    def active(self) -> bool:
        return self._view.state == WAITING

    async def start(self, binary: Path) -> LoginView:
        if self.active:
            return self._view
        await self._reap()
        self._buffer = ""
        self._started = time.monotonic()
        self._view = LoginView(WAITING, "", "Opening your browser…")
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._backend.command(binary),
                *self._backend.login_args(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._backend.environment(),
                start_new_session=True,
            )
        except OSError as problem:
            self._view = LoginView(FAILED, "", f"Couldn't start {self._backend.name}: {problem}")
            return self._view
        self._reader = asyncio.create_task(self._watch(self._process))
        return self._view

    async def submit_code(self, code: str) -> LoginView:
        code = code.strip()
        process = self._process
        if not self.active or process is None or process.stdin is None:
            return self._view
        if not _CODE.fullmatch(code):
            self._view = LoginView(
                WAITING, self._view.url, "That doesn't look like a sign-in code."
            )
            return self._view
        try:
            process.stdin.write(code.encode() + b"\n")
            await process.stdin.drain()
            self._view = LoginView(WAITING, self._view.url, "Checking the code…")
        except (BrokenPipeError, ConnectionResetError):
            self._view = LoginView(FAILED, "", "Sign-in ended before the code was used. Try again.")
        return self._view

    async def cancel(self) -> LoginView:
        if self.active:
            await self._reap()
            self._view = LoginView(CANCELLED, "", "Sign-in cancelled.")
        return self._view

    async def _watch(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None  # noqa: S101
        try:
            async with asyncio.timeout(_LOGIN_TIMEOUT_SECONDS):
                while chunk := await process.stdout.read(1024):
                    self._buffer = (self._buffer + _ANSI.sub("", chunk.decode("utf-8", "replace")))[
                        -4000:
                    ]
                    match = self._url.search(self._buffer)
                    if match and self._view.state == WAITING and not self._view.url:
                        self._view = LoginView(
                            WAITING, match.group(0), "Finish signing in in your browser."
                        )
                code = await process.wait()
        except TimeoutError:
            await self._reap()
            self._view = LoginView(FAILED, "", "Sign-in took too long. Try again.")
            return
        if self._view.state == CANCELLED:
            return
        if code == 0:
            if self._on_success is not None:
                with contextlib.suppress(Exception):
                    await self._on_success(self._buffer)
            self._view = LoginView(SUCCEEDED, "", "Signed in.")
        else:
            self._view = LoginView(FAILED, "", "Sign-in didn't complete. Try again.")

    async def _reap(self) -> None:
        process, self._process = self._process, None
        reader, self._reader = self._reader, None
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader


async def sign_out(binary: Path, backend: Backend | None = None) -> tuple[bool, str]:
    """Sign out the coding CLI Sani uses. True when it exited cleanly."""
    if backend is None:
        from assistant.coding_agents.claude import ClaudeBackend

        backend = ClaudeBackend()
    process = await asyncio.create_subprocess_exec(
        *backend.command(binary),
        *backend.logout_args(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=backend.environment(),
    )
    try:
        await asyncio.wait_for(process.communicate(), timeout=20)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        return False, "Signing out took too long."
    return process.returncode == 0, "" if process.returncode == 0 else "Couldn't sign out."
