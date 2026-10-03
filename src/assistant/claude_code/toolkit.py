"""The ``claude_code`` tool: how the Deep Agent hands software work to Claude Code.

Sani acts like the person at the keyboard: it writes one clear request, runs the
user's own Claude Code in a folder the user has allowed, lets a rule-based
watchdog stop a runaway run, streams every step into the chat, and returns a
short honest summary the Deep Agent can read, judge, and (at most a few times)
follow up on. No API key and no extra model call is involved in the run itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from assistant.claude_code import context
from assistant.claude_code.auth import SUCCEEDED, LoginSession, sign_out
from assistant.claude_code.events import (
    ClaudeEvent,
    Final,
    PermissionDenied,
    RateLimit,
    Retry,
    ToolEnd,
    ToolStart,
)
from assistant.claude_code.locate import (
    ClaudeStatus,
    find_claude_binary,
    parse_version,
    read_status,
)
from assistant.claude_code.redact import screen
from assistant.claude_code.runner import ClaudeRunner, Permission, RunOutcome, RunRequest
from assistant.claude_code.store import SessionStore
from assistant.claude_code.watchdog import Watchdog, WatchdogLimits
from assistant.core.protocol import AGENT_PROGRESS
from assistant.settings import Settings

logger = logging.getLogger("assistant.claude_code")

_ORDER: dict[str, int] = {"read": 0, "edit": 1, "run": 2}
_STATUS_TTL_SECONDS = 20.0
_ATTACHMENT_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".md", ".json"}
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_SUMMARY_LIMIT = 3000

TOOL_DESCRIPTION = (
    "Do software work with the user's own Claude Code: write, change, debug, test or explain code "
    "in a project folder. Use it for any coding request, never for chat, desktop clicking or "
    "browsing. Give ONE complete, self-contained request (the goal, the files or area involved, "
    "constraints, and how to check it worked, for example 'run the tests'). Claude Code keeps this "
    "chat's session, so follow-ups continue where it left off. The result tells you what changed "
    "and whether it worked; read it before answering. If a run fails for a clear, fixable reason, "
    "make ONE corrected follow-up. If it stops, is blocked, or fails the same way twice, stop and "
    "tell the user plainly what happened and what you need, instead of trying again."
)

GUIDE = """
Coding with Claude Code: you have a `claude_code` tool that drives the user's own Claude Code.
- Use it for software work (build, edit, fix, test, explain a codebase). Do not use it for chat,
  the desktop, or the web.
- Write the request like a good engineer would: goal, where, constraints, how to verify.
- Pick mode "read" to only look, "edit" to change files, "run" to also run commands and tests.
- Judge each result. One corrected follow-up is fine; if it stops, is blocked or fails the same way
  twice, stop and explain in plain words what happened and what you need from the user.
- Never paste long output back. Say what changed, whether it worked, and what to do next.
- If the user attached an image, pass its path in `attachments`.
- Give every call a short `purpose` (under 8 words); the user sees it as the round's title.
- New project: pass `create_folder=true` and a project_dir inside an allowed folder.
- Claude Code cannot wait for an answer mid-run. If its reply asks you something with options,
  decide who answers. Answer it yourself (send a follow-up call) when the user's request, the chat
  so far, or sound engineering defaults settle it, and say in one line what you chose and why.
  Ask the user only for things that are theirs to decide (brand, taste, money, anything hard to
  undo). Ask by ending your message with a block like:
  [Options]
  - First choice
  - Second choice
  (2 to 4 short choices; the user can tap one or type their own).
"""


class ClaudeCodeArgs(BaseModel):
    task: str = Field(description="The complete, self-contained request for Claude Code.")
    project_dir: str = Field(description="Absolute path of the project folder to work in.")
    mode: Literal["read", "edit", "run"] = Field(
        default="edit",
        description="read = look only; edit = change files; run = change files and run commands.",
    )
    new_session: bool = Field(
        default=False,
        description="Start a fresh Claude Code session instead of continuing this chat's.",
    )
    attachments: list[str] = Field(
        default_factory=list,
        description="Paths of images or files the user attached that Claude Code should look at.",
    )
    create_folder: bool = Field(
        default=False,
        description=(
            "Create project_dir first if it does not exist yet (only inside a folder the user "
            "allowed). Use it when starting a new project."
        ),
    )
    purpose: str = Field(
        default="",
        description=(
            "Under 8 words, shown to the user as this round's title, e.g. 'Build the first "
            "version' or 'Fix the failing build'."
        ),
    )


class ProjectNotAllowed(ValueError):
    """The folder is not one the user allowed. The message is shown to the user."""


def _real(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


class ClaudeCodeToolkit:
    def __init__(
        self,
        settings: Settings,
        *,
        locator: Callable[..., Path | None] = find_claude_binary,
        status_reader: Callable[[Path | None], Awaitable[ClaudeStatus]] = read_status,
        store: SessionStore | None = None,
        runner_factory: Callable[[Path, tuple[int, int, int] | None], ClaudeRunner] | None = None,
        limits: WatchdogLimits | None = None,
    ) -> None:
        self._settings = settings
        self._locator = locator
        self._status_reader = status_reader
        self._store = store or (
            SessionStore(settings.sani_db_path) if settings.sani_data_dir else None
        )
        self._runner_factory = runner_factory or (
            lambda binary, version: ClaudeRunner(binary, version=version)
        )
        self._limits = limits
        self._status: tuple[float, ClaudeStatus] | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._rates: dict[str, dict[str, Any]] = {}
        self._last_run: dict[str, Any] = {}
        self._login = LoginSession()

    # -- configuration -------------------------------------------------------

    def allowed_dirs(self) -> list[Path]:
        roots: list[Path] = []
        home = Path.home().resolve()
        for raw in self._settings.claude_code_dirs.split(os.pathsep):
            raw = raw.strip()
            if not raw:
                continue
            root = _real(raw)
            # Never the whole disk or the whole home folder.
            if root == Path(root.anchor) or root == home or not root.is_dir():
                continue
            roots.append(root)
        return roots

    def attachments_dir(self) -> Path | None:
        if not self._settings.sani_data_dir:
            return None
        return _real(self._settings.sani_data_dir) / "attachments"

    def resolve_project(self, raw: str, *, create: bool = False) -> Path:
        """The folder to work in. With ``create``, a missing one is made, but only
        inside a folder the user allowed (never by following a link out of one)."""
        if not raw.strip():
            raise ProjectNotAllowed("Tell me which project folder to work in.")
        roots = self.allowed_dirs()
        if not roots:
            raise ProjectNotAllowed(
                "No project folders are set up for Claude Code yet. Add one in "
                "Settings → Claude Code."
            )
        project = _real(raw)
        if create and not project.exists():
            existing = project
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            if any(_inside(existing, root) for root in roots) and existing.is_dir():
                project.mkdir(parents=True, exist_ok=True)
                project = _real(project)
            else:
                names = ", ".join(str(root) for root in roots)
                raise ProjectNotAllowed(
                    f"I can only create folders inside the ones Sani may work in ({names})."
                )
        if not project.is_dir():
            raise ProjectNotAllowed(f"{raw} isn't a folder on this Mac.")
        if not any(_inside(project, root) for root in roots):
            names = ", ".join(str(root) for root in roots)
            raise ProjectNotAllowed(
                f"{project} isn't one of the folders Sani may work in ({names}). "
                "The user can add it in Settings → Claude Code."
            )
        return project

    def _clean_attachments(self, raw: list[str], project: Path) -> tuple[Path, ...]:
        allowed = [project]
        extra = self.attachments_dir()
        if extra is not None:
            allowed.append(extra)
        files: list[Path] = []
        for item in raw:
            path = _real(item)
            if not any(_inside(path, root) for root in allowed):
                raise ProjectNotAllowed(f"I can't share {item} with Claude Code from there.")
            if not path.is_file() or path.suffix.lower() not in _ATTACHMENT_SUFFIXES:
                raise ProjectNotAllowed(f"{item} isn't an image or document I can pass along.")
            if path.stat().st_size > _MAX_ATTACHMENT_BYTES:
                raise ProjectNotAllowed(f"{item} is too large to pass along.")
            files.append(path)
        return tuple(files)

    # -- status (for the UI and for preflight) ---------------------------------

    async def claude_status(self, *, fresh: bool = False) -> ClaudeStatus:
        now = time.monotonic()
        if not fresh and self._status and now - self._status[0] < _STATUS_TTL_SECONDS:
            return self._status[1]
        binary = self._locator(override=self._settings.claude_code_binary)
        status = await self._status_reader(binary)
        self._status = (now, status)
        return status

    async def usage_state(self) -> dict[str, Any]:
        if not self._rates and not self._last_run and self._store is not None:
            saved = await self._store.get_state("usage")
            self._rates = dict(saved.get("rates") or {})
            self._last_run = dict(saved.get("last_run") or {})
        window = self._last_run.get("context_window")
        tokens = self._last_run.get("context_tokens")
        percent = round(100 * tokens / window) if tokens and window else None
        return {
            "rates": self._rates,
            "last_run": self._last_run,
            "context_percent": percent,
        }

    async def status_report(self) -> dict[str, Any]:
        status = await self.claude_status(fresh=True)
        # A finished sign-in is re-read from Claude Code itself, never assumed.
        if self._login.view().state == SUCCEEDED and not status.signed_in:
            status = await self.claude_status(fresh=True)
        return {
            "login": self._login.view().as_dict(),
            "enabled": self._settings.claude_code_enabled,
            "permission": self._settings.claude_code_permission,
            "folders": [str(root) for root in self.allowed_dirs()],
            "claude": status.as_dict(),
            "usage": await self.usage_state(),
        }

    # -- sign in / out (buttons in Settings) --------------------------------------

    async def handle_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """``claude_code.login_start`` / ``login_code`` / ``login_cancel`` / ``logout``."""
        binary = self._locator(override=self._settings.claude_code_binary)
        if method == "claude_code.login_start":
            if binary is None:
                return {"error": "Claude Code isn't installed."}
            self._status = None
            await self._login.start(binary)
        elif method == "claude_code.login_code":
            await self._login.submit_code(str(params.get("code") or ""))
        elif method == "claude_code.login_cancel":
            await self._login.cancel()
        elif method == "claude_code.logout":
            if binary is None:
                return {"error": "Claude Code isn't installed."}
            await self._login.cancel()
            ok, message = await sign_out(binary)
            self._status = None
            if not ok:
                return {"error": message}
        else:
            return {"error": "unknown action"}
        self._status = None
        return await self.status_report()

    # -- the tool -----------------------------------------------------------

    def as_tool(self) -> BaseTool:
        return StructuredTool.from_function(
            coroutine=self.run,
            name="claude_code",
            description=TOOL_DESCRIPTION,
            args_schema=ClaudeCodeArgs,
        )

    async def run(
        self,
        task: str,
        project_dir: str,
        mode: str = "edit",
        new_session: bool = False,
        attachments: list[str] | None = None,
        purpose: str = "",
        create_folder: bool = False,
    ) -> str:
        if not task.strip():
            return "Nothing to do: the request was empty."
        existed = _real(project_dir).exists() if project_dir.strip() else False
        try:
            project = self.resolve_project(project_dir, create=create_folder)
            files = self._clean_attachments(attachments or [], project)
        except ProjectNotAllowed as problem:
            return f"Not started. {problem}"
        created = create_folder and not existed
        status = await self.claude_status()
        if not status.installed:
            return f"Not started. {status.detail}"
        if not status.signed_in:
            return (
                "Not started. Claude Code isn't signed in on this Mac. Ask the user to run "
                "`claude auth login` once in Terminal (it opens a browser); then try again."
            )
        binary = self._locator(override=self._settings.claude_code_binary)
        if binary is None:  # pragma: no cover - status just proved it exists
            return "Not started. Claude Code disappeared."

        requested = mode if mode in _ORDER else "edit"
        ceiling = self._settings.claude_code_permission
        effective: Permission = requested if _ORDER[requested] <= _ORDER[ceiling] else ceiling  # type: ignore[assignment]
        clamp_note = (
            f" (limited to “{ceiling}” by the user's setting)" if effective != requested else ""
        )

        conversation = context.conversation_id.get()
        session_id = ""
        if self._store is not None and conversation and not new_session:
            session_id = await self._store.get_session(conversation, str(project))
        lock = self._locks.setdefault(str(project), asyncio.Lock())
        if lock.locked():
            return "Not started. Claude Code is already working in that folder; wait for it."
        extra = self.attachments_dir()
        request = RunRequest(
            prompt=task.strip(),
            cwd=project,
            permission=effective,
            session_id=session_id or None,
            max_turns=self._settings.claude_code_max_turns,
            attachments=files,
            extra_dirs=(extra,) if files and extra is not None and extra.is_dir() else (),
        )
        version = parse_version(status.version)
        runner = self._runner_factory(binary, version)
        watchdog = Watchdog(self._limits or WatchdogLimits(max_seconds=self._deadline()))
        cancel = asyncio.Event()
        reporter = _Reporter(project, self._remember_rate)
        async with lock:
            if created:
                await reporter.plain(f"Created the folder {project.name}", detail=str(project))
            await reporter.start(
                purpose.strip() or f"Working in {project.name}",
                request=task.strip(),
                continuing=bool(session_id),
            )
            try:
                outcome = await runner.run(
                    request, on_event=reporter.handle, watchdog=watchdog, cancel=cancel
                )
            except asyncio.CancelledError:
                cancel.set()
                raise
            except OSError as problem:
                await reporter.finish(None, error=str(problem))
                return f"Claude Code could not be started: {problem}"
            await reporter.finish(outcome)
        await self._after_run(conversation, project, outcome)
        return self._summary(outcome, project, effective, clamp_note, bool(session_id))

    def _deadline(self) -> float:
        return float(self._settings.claude_code_run_seconds)

    async def _after_run(self, conversation: str, project: Path, outcome: RunOutcome) -> None:
        final = outcome.final
        self._last_run = {
            "conversation": conversation,
            "session_id": outcome.session_id,
            "context_tokens": final.context_tokens if final else None,
            "context_window": final.context_window if final else None,
            "cost_usd": final.cost_usd if final else None,
            "turns": final.turns if final else 0,
            "at": time.time(),
        }
        if self._store is None:
            return
        with contextlib.suppress(Exception):
            if conversation and outcome.session_id:
                await self._store.set_session(conversation, str(project), outcome.session_id)
            await self._store.put_state("usage", {"rates": self._rates, "last_run": self._last_run})

    def _remember_rate(self, limit: RateLimit) -> None:
        key = limit.kind or "plan"
        self._rates[key] = {
            "status": limit.status,
            "resets_at": limit.resets_at,
            "used_percent": limit.used_percent,
            "seen_at": time.time(),
        }

    # -- the answer the Deep Agent reads ----------------------------------------

    @staticmethod
    def _summary(
        outcome: RunOutcome, project: Path, mode: str, clamp_note: str, resumed: bool
    ) -> str:
        lines: list[str] = []
        if outcome.cancelled:
            lines.append("Claude Code was stopped because the user cancelled.")
        elif outcome.stopped_reason:
            lines.append(
                f"Claude Code was stopped early because {outcome.stopped_reason}. "
                "Do not simply retry: tell the user what happened, or make ONE changed attempt "
                "with a narrower request."
            )
        elif outcome.ok:
            lines.append("Claude Code finished.")
        else:
            reason = outcome.error or (outcome.final.subtype if outcome.final else "")
            lines.append(f"Claude Code did not finish successfully ({reason or 'unknown reason'}).")
        lines.append(
            f"Folder: {project} · mode: {mode}{clamp_note}"
            + (" · continued session" if resumed else "")
        )
        lines.append(
            f"Steps: {outcome.steps}"
            + (f" ({outcome.failed_steps} failed)" if outcome.failed_steps else "")
        )
        if outcome.files_changed:
            shown = ", ".join(outcome.files_changed[:12])
            more = len(outcome.files_changed) - 12
            lines.append(f"Files changed: {shown}" + (f" and {more} more" if more > 0 else ""))
        if outcome.commands:
            lines.append("Commands: " + "; ".join(outcome.commands[:6]))
        final = outcome.final
        if final and final.denials:
            lines.append(
                "Blocked tools (not allowed in this mode): " + ", ".join(sorted(set(final.denials)))
            )
        text = outcome.text.strip()
        if text:
            if len(text) > _SUMMARY_LIMIT:
                text = text[:_SUMMARY_LIMIT].rstrip() + "…"
            lines.append("Claude Code said:\n" + text)
        if final:
            used = f"{final.turns} turns"
            if final.cost_usd is not None:
                used += f", est. ${final.cost_usd:.2f}"
            if final.context_tokens and final.context_window:
                percent = round(100 * final.context_tokens / final.context_window)
                used += f", conversation {percent}% full"
            lines.append("Used: " + used)
        if outcome.session_id:
            lines.append(f"Session: {outcome.session_id}")
        return "\n".join(lines)


class _Reporter:
    """Turns one Claude Code round into chat steps.

    A round reads as a short conversation: a header (why Sani is calling Claude
    Code), the request Sani wrote, Claude Code's steps, and Claude Code's reply.
    Every step carries the round's ``group`` so the UI can fold them together.
    """

    def __init__(self, project: Path, on_rate: Callable[[RateLimit], None]) -> None:
        self._project = project
        self._on_rate = on_rate
        self._open: dict[str, tuple[ToolStart, float]] = {}
        self._counter = 0
        self._group = f"r{time.time_ns()}"
        self._started = time.monotonic()
        self._title = ""

    async def _send(self, step: dict[str, Any]) -> None:
        sink = context.event_sink.get()
        if sink is None:
            return
        step = {**step, "group": self._group}
        with contextlib.suppress(Exception):
            await sink(AGENT_PROGRESS, {"message": step["label"], "step": step})

    async def plain(self, label: str, *, detail: str = "") -> None:
        """A step by Sani itself (not part of a Claude Code round)."""
        sink = context.event_sink.get()
        if sink is None:
            return
        step = {
            "id": f"sani:{time.time_ns()}",
            "label": label,
            "status": "complete",
            "tool": "folder",
            "detail": detail,
        }
        with contextlib.suppress(Exception):
            await sink(AGENT_PROGRESS, {"message": label, "step": step})

    async def start(self, title: str, *, request: str, continuing: bool) -> None:
        self._title = screen(title, limit=80)
        self._started = time.monotonic()
        await self._send(
            {
                "id": f"{self._group}:round",
                "kind": "round",
                "label": self._title,
                "status": "running",
                "tool": "claude_code",
                "detail": "continuing the same Claude Code session" if continuing else "",
            }
        )
        await self._send(
            {
                "id": f"{self._group}:prompt",
                "kind": "prompt",
                "label": "Sani asked Claude Code",
                "status": "complete",
                "detail": screen(request, limit=2500),
            }
        )

    async def finish(self, outcome: RunOutcome | None, *, error: str = "") -> None:
        if outcome is not None and outcome.stopped_reason:
            await self._send(
                {
                    "id": f"{self._group}:note",
                    "kind": "note",
                    "label": f"Stopped: {outcome.stopped_reason}",
                    "status": "info",
                }
            )
        reply = ""
        if outcome is not None:
            reply = outcome.text.strip()
        elif error:
            reply = error
        if reply:
            await self._send(
                {
                    "id": f"{self._group}:reply",
                    "kind": "reply",
                    "label": "Claude Code replied",
                    "status": "complete",
                    "detail": screen(reply, limit=1800),
                }
            )
        ok = outcome is not None and outcome.ok
        await self._send(
            {
                "id": f"{self._group}:round",
                "kind": "round",
                "label": self._title,
                "status": "complete" if ok else "failed" if outcome is not None else "failed",
                "tool": "claude_code",
                "duration_ms": int((time.monotonic() - self._started) * 1000),
            }
        )

    async def handle(self, event: ClaudeEvent) -> None:
        if isinstance(event, ToolStart):
            self._open[event.id] = (event, time.monotonic())
            await self._send(
                {
                    "id": f"cc:{event.id}",
                    "label": event.label,
                    "status": "running",
                    "tool": event.name,
                    "detail": event.detail,
                }
            )
        elif isinstance(event, ToolEnd):
            started = self._open.pop(event.id, None)
            if started is None:
                return
            start, t0 = started
            detail = start.detail
            if not event.ok and event.summary:
                detail = f"{detail}\n\n→ {event.summary}".strip()
            await self._send(
                {
                    "id": f"cc:{start.id}",
                    "label": start.label,
                    "status": "complete" if event.ok else "failed",
                    "tool": start.name,
                    "detail": detail,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                }
            )
        elif isinstance(event, PermissionDenied):
            self._counter += 1
            await self._send(
                {
                    "id": f"cc:denied-{self._counter}",
                    "label": f"{event.tool} isn't allowed in this mode",
                    "status": "info",
                    "tool": event.tool,
                }
            )
        elif isinstance(event, Retry):
            self._counter += 1
            await self._send(
                {
                    "id": f"cc:retry-{self._counter}",
                    "label": "Waiting for Claude to respond again",
                    "status": "info",
                }
            )
        elif isinstance(event, RateLimit):
            self._on_rate(event)
        elif isinstance(event, Final):
            return
