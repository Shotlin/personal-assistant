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
from copy import copy
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

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
    child_environment,
    parse_version,
)
from assistant.claude_code.parser import StreamParser
from assistant.claude_code.redact import screen
from assistant.claude_code.runner import ClaudeRunner, Permission, Runner, RunOutcome, RunRequest
from assistant.claude_code.store import SessionStore
from assistant.claude_code.watchdog import Watchdog, WatchdogLimits
from assistant.coding_agents.backend import Backend, session_key
from assistant.coding_agents.claude import ClaudeBackend
from assistant.coding_agents.guide import GUIDE, TOOL_DESCRIPTION, for_backend
from assistant.core.protocol import AGENT_PROGRESS
from assistant.settings import Settings

logger = logging.getLogger("assistant.claude_code")

_ORDER: dict[str, int] = {"read": 0, "edit": 1, "run": 2}
_STATUS_TTL_SECONDS = 20.0
_ATTACHMENT_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".md", ".json"}
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_SUMMARY_LIMIT = 3000

__all__ = ["GUIDE", "TOOL_DESCRIPTION", "ClaudeCodeToolkit", "ClaudeCodeArgs"]


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


def args_schema_for(name: str) -> type[BaseModel]:
    """The tool's argument schema with this backend's name in the descriptions."""
    if name == "Claude Code":
        return ClaudeCodeArgs
    fields: dict[str, Any] = {}
    for key, info in ClaudeCodeArgs.model_fields.items():
        copied = copy(info)
        copied.description = (info.description or "").replace("Claude Code", name)
        fields[key] = (info.annotation, copied)
    return create_model(f"{name.replace(' ', '')}Args", **fields)


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
        backend: Backend | None = None,
        locator: Callable[..., Path | None] | None = None,
        status_reader: Callable[[Path | None], Awaitable[ClaudeStatus]] | None = None,
        store: SessionStore | None = None,
        runner_factory: Callable[[Path, tuple[int, int, int] | None], Runner] | None = None,
        limits: WatchdogLimits | None = None,
    ) -> None:
        self._settings = settings
        self._backend: Backend = backend or ClaudeBackend()
        self._name = self._backend.name
        self._opts = self._backend.options(settings)
        # Tests inject find_claude_binary/read_status fakes; otherwise the backend decides.
        self._locator = locator or (lambda override="": self._backend.locate(override))
        self._status_reader = status_reader or self._backend.read_status
        self._store = store or (
            SessionStore(settings.sani_db_path) if settings.sani_data_dir else None
        )
        bind = getattr(self._backend, "bind_store", None)
        if bind is not None:
            bind(self._store)
        self._runner_factory = runner_factory or (
            lambda binary, version: ClaudeRunner(
                binary, version=version, backend=self._backend, settings=settings
            )
        )
        self._limits = limits
        self._status: tuple[float, ClaudeStatus] | None = None
        self._cdp_job: Any = None
        self._last_auto = -1000.0
        self._locks: dict[str, asyncio.Lock] = {}
        self._rates: dict[str, dict[str, Any]] = {}
        self._last_run: dict[str, Any] = {}
        self._login = LoginSession(self._backend, on_success=self._login_succeeded)

    # -- configuration -------------------------------------------------------

    def allowed_dirs(self) -> list[Path]:
        roots: list[Path] = []
        home = Path.home().resolve()
        for raw in self._opts.dirs.split(os.pathsep):
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
                f"No project folders are set up for {self._name} yet. The user adds one in "
                "Sani's Settings → Claude Code → Project folders (a list shared by every "
                "coding tool, not a setting inside the coding tool itself)."
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
                "That list is Sani's own, shared by every coding tool: the user adds the folder in "
                "Sani's Settings → Claude Code → Project folders. It is not a setting inside "
                f"{self._name}, so do not look for it there."
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
                raise ProjectNotAllowed(f"I can't share {item} with {self._name} from there.")
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
        binary = self._locator(override=self._opts.binary)
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
        await self._maybe_auto_read()
        status = await self.claude_status(fresh=True)
        # A finished sign-in is re-read from Claude Code itself, never assumed.
        if self._login.view().state == SUCCEEDED and not status.signed_in:
            status = await self.claude_status(fresh=True)
        return {
            "login": self._login.view().as_dict(),
            "enabled": self._opts.enabled,
            "permission": self._opts.permission,
            "folders": [str(root) for root in self.allowed_dirs()],
            self._backend.key: status.as_dict(),
            "backend": self._backend.key,
            "extra": self._backend.extra_status(self._locator(override=self._opts.binary)),
            "selection": await self._selection(),
            "balances": await self._balances(),
            "account": await self._account(),
            "cdp": await self._cdp(),
            "control": await self._control_view(),
            "account_change": await self._account_change(),
            "usage": await self.usage_state(),
        }

    # -- sign in / out (buttons in Settings) --------------------------------------

    async def handle_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """``claude_code.login_start`` / ``login_code`` / ``login_cancel`` / ``logout``."""
        binary = self._locator(override=self._opts.binary)
        prefix = self._backend.action_prefix
        if method == f"{prefix}.status":
            return await self.status_report()
        if method == f"{prefix}.select":
            error = await self._select(
                str(params.get("provider") or ""), str(params.get("model") or "")
            )
            if error:
                return {"error": error}
        elif method == f"{prefix}.login_start":
            if binary is None:
                return {"error": f"{self._name} isn't installed."}
            self._status = None
            await self._login.start(binary)
        elif method == f"{prefix}.login_code":
            await self._login.submit_code(str(params.get("code") or ""))
        elif method == f"{prefix}.login_cancel":
            await self._login.cancel()
        elif method == f"{prefix}.logout":
            if binary is None:
                return {"error": f"{self._name} isn't installed."}
            await self._login.cancel()
            ok, message = await sign_out(binary, self._backend)
            self._status = None
            if not ok:
                return {"error": message}
        elif method == f"{prefix}.control" and getattr(self._backend, "control", None):
            control: Any = getattr(self._backend, "control")  # noqa: B009
            if params.get("confirm") is not True:
                return {"error": "Confirm first: this lets Sani close and reopen ZCode."}
            await control.set_enabled(params.get("enabled") is True)
        elif method == f"{prefix}.ack_change" and self._backend.key == "zcode":
            from assistant.coding_agents.zcode_cdp.sync import AccountSync

            await AccountSync(self._store).acknowledge()
        elif method == f"{prefix}.read" and self._backend.key == "zcode":
            error = self._zcode_job().start(confirmed=params.get("confirm") is True)
            if error:
                return {"error": error}
            return await self.status_report()
        elif method == f"{prefix}.usage_refresh" and self._backend.supports_usage:
            error = await self._refresh_usage(binary)
            if error:
                return {"error": error}
        else:
            return {"error": "unknown action"}
        self._status = None
        return await self.status_report()

    async def _login_succeeded(self, output: str) -> None:
        """Keep the account label the sign-in command printed (name or email only)."""
        found = self._backend.parse_account(output)
        if not found or self._store is None:
            return
        keep = {k: screen(v, limit=120) for k, v in found.items() if k in {"name", "email"}}
        await self._store.put_state(f"{self._backend.key}_account", {**keep, "as_of": time.time()})

    async def _account(self) -> dict[str, Any] | None:
        if self._backend.key != "zcode":
            return None
        from assistant.coding_agents.zcode_app import saved_account

        return await saved_account(self)

    async def _cdp(self) -> dict[str, Any] | None:
        """What the last read of the ZCode window found (contract, models, sessions)."""
        if self._backend.key != "zcode":
            return None
        from assistant.coding_agents.zcode_cdp.reader import saved_overview

        overview = await saved_overview(self._store) or {}
        return {**overview, "job": self._zcode_job().view()}

    async def _account_change(self) -> dict[str, Any] | None:
        if self._backend.key != "zcode" or self._store is None:
            return None
        from assistant.coding_agents.zcode_cdp.sync import AccountSync

        return await AccountSync(self._store).view()

    async def _maybe_auto_read(self) -> None:
        """Re-read ZCode on its own once its sign-in file changed (with the user's standing yes).

        Waits until the file has been still for 20 seconds (a sign-in is finished), never while a
        run uses the window, and at most once every five minutes.
        """
        control: Any = getattr(self._backend, "control", None)
        if control is None or self._store is None:
            return
        from assistant.coding_agents.zcode_cdp.sync import AccountSync, credential_signal

        sync = AccountSync(self._store)
        fingerprint, modified = credential_signal()
        await sync.signal(fingerprint, modified)
        stale = await sync.stale()
        if not stale or not await control.enabled():
            return
        job = self._zcode_job()
        if job.reading or control.busy or time.time() - float(stale.get("modified", 0)) < 20:
            return
        if time.monotonic() - self._last_auto < 300:
            return
        self._last_auto = time.monotonic()
        job.start(confirmed=True)

    async def _control_view(self) -> dict[str, Any] | None:
        """Whether Sani may close/reopen ZCode to drive it, and whether the port is open now."""
        control: Any = getattr(self._backend, "control", None)
        if control is None:
            return None
        return {"enabled": await control.enabled(), "open": control.is_open}

    def _zcode_job(self) -> Any:
        """The one background read of the ZCode window (created on first use)."""
        if self._cdp_job is None:
            from assistant.coding_agents.zcode_cdp.job import ReadJob
            from assistant.coding_agents.zcode_cdp.launcher import ZCodeLauncher
            from assistant.coding_agents.zcode_cdp.reader import read_snapshot

            control: Any = getattr(self._backend, "control", None)

            async def reader() -> Any:
                if control is not None and control.is_open:
                    snap = await control.read_in_place()
                    if snap is not None:
                        return snap
                return await read_snapshot(ZCodeLauncher(), consent=True)

            self._cdp_job = ReadJob(self._store, reader)
        return self._cdp_job

    async def _balances(self) -> dict[str, Any] | None:
        if self._backend.key != "zcode":
            return None
        from assistant.coding_agents.zcode_app import saved_balances

        return await saved_balances(self)

    async def _selection(self) -> dict[str, str]:
        if self._store is None:
            return {}
        saved = await self._store.get_state(f"{self._backend.key}_selection")
        return {k: str(v) for k, v in saved.items() if k in {"provider", "model"}}

    async def _select(self, provider: str, model: str) -> str:
        """Remember the plan and model the user picked (must exist in this backend's catalog)."""
        catalog = self._backend.extra_status(self._locator(override=self._opts.binary)).get(
            "catalog"
        )
        plans = catalog if isinstance(catalog, list) else []
        known = any(
            plan.get("id") == provider and any(m.get("id") == model for m in plan.get("models", []))
            for plan in plans
        )
        if not known:
            return "That plan and model aren't available."
        if self._store is None:
            return "Nowhere to save the choice."
        await self._store.put_state(
            f"{self._backend.key}_selection", {"provider": provider, "model": model}
        )
        return ""

    async def _refresh_usage(self, binary: Path | None) -> str:
        """Ask Claude Code for its 5-hour and weekly numbers with one tiny request.

        Claude Code only reports them while it works, so this sends a one-word
        prompt with no tools. It uses a little of the user's own plan, never an API key.
        """
        if binary is None:
            return "Claude Code isn't installed."
        folders = self.allowed_dirs()
        if not folders:
            return "Add a project folder first."
        process = await asyncio.create_subprocess_exec(
            str(binary),
            "-p",
            "Reply with just: ok",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "1",
            "--tools",
            "",
            cwd=str(folders[0]),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=child_environment(),
        )
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            return "Claude Code took too long to answer."
        parser = StreamParser()
        found = False
        for raw in out.decode("utf-8", errors="replace").splitlines():
            for event in parser.feed(raw):
                if isinstance(event, RateLimit):
                    self._remember_rate(event)
                    found = True
        if not found:
            return "Claude Code didn't report its limits."
        if self._store is not None:
            with contextlib.suppress(Exception):
                await self._store.put_state(
                    "usage", {"rates": self._rates, "last_run": self._last_run}
                )
        return ""

    # -- the tool -----------------------------------------------------------

    def guide(self) -> str:
        """The paragraph that tells the Deep Agent how to use this coding tool."""
        return self._backend.guide()

    def as_tool(self) -> BaseTool:
        return StructuredTool.from_function(
            coroutine=self.run,
            name=self._backend.tool_name,
            description=for_backend(TOOL_DESCRIPTION, self._name, self._backend.tool_name),
            args_schema=args_schema_for(self._name),
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
            return f"Not started. {self._backend.sign_in_hint}"
        binary = self._locator(override=self._opts.binary)
        if binary is None:  # pragma: no cover - status just proved it exists
            return f"Not started. {self._name} disappeared."

        # Before anything is spent: e.g. ZCode must be on the user's Z.ai plan, not a custom key.
        preflight = await self._backend.preflight(
            binary, Path(self._settings.sani_data_dir or "/tmp"), self._settings
        )
        if preflight.refusal:
            return f"Not started. {preflight.refusal}"

        requested = mode if mode in _ORDER else "edit"
        ceiling = self._opts.permission
        effective: Permission = requested if _ORDER[requested] <= _ORDER[ceiling] else ceiling  # type: ignore[assignment]
        clamp_note = (
            (
                f" (limited to “{ceiling}” by Sani's own setting: Settings → Claude Code → "
                "What a run may do; the user raises it there, not inside the coding tool)"
            )
            if effective != requested
            else ""
        )

        conversation = context.conversation_id.get()
        session_id = ""
        skey = session_key(self._backend, conversation)
        if self._store is not None and conversation and not new_session:
            session_id = await self._store.get_session(skey, str(project))
        lock = self._locks.setdefault(str(project), asyncio.Lock())
        if lock.locked():
            return f"Not started. {self._name} is already working in that folder; wait for it."
        extra = self.attachments_dir()
        request = RunRequest(
            prompt=task.strip(),
            cwd=project,
            permission=effective,
            session_id=session_id or None,
            max_turns=self._opts.max_turns,
            model=self._opts.model,
            effort=self._opts.effort,
            attachments=files,
            extra_dirs=(extra,) if files and extra is not None and extra.is_dir() else (),
        )
        version = parse_version(status.version)
        runner = self._runner_factory(binary, version)
        watchdog = Watchdog(self._limits or WatchdogLimits(max_seconds=self._deadline()))
        cancel = asyncio.Event()
        reporter = _Reporter(project, self._remember_rate, self._backend)
        async with lock:
            if created:
                await reporter.plain(f"Created the folder {project.name}", detail=str(project))
            await reporter.start(
                purpose.strip() or f"Working in {project.name}",
                request=task.strip(),
                continuing=bool(session_id),
                note=preflight.note,
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
                return f"{self._name} could not be started: {problem}"
            await reporter.finish(outcome)
        await self._after_run(conversation, project, outcome)
        return self._summary(
            outcome,
            project,
            effective,
            clamp_note,
            bool(session_id),
            self._name,
            preflight.note,
        )

    def _deadline(self) -> float:
        return float(self._opts.run_seconds)

    async def _after_run(self, conversation: str, project: Path, outcome: RunOutcome) -> None:
        final = outcome.final
        self._last_run = {
            "conversation": conversation,
            "session_id": outcome.session_id,
            "context_tokens": final.context_tokens if final else None,
            "context_window": final.context_window if final else None,
            "model": final.model if final else "",
            "cost_usd": final.cost_usd if final else None,
            "turns": final.turns if final else 0,
            "at": time.time(),
        }
        if self._store is None:
            return
        with contextlib.suppress(Exception):
            if conversation and outcome.session_id:
                await self._store.set_session(
                    session_key(self._backend, conversation), str(project), outcome.session_id
                )
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
        outcome: RunOutcome,
        project: Path,
        mode: str,
        clamp_note: str,
        resumed: bool,
        name: str = "Claude Code",
        model_note: str = "",
    ) -> str:
        lines: list[str] = []
        if outcome.cancelled:
            lines.append(f"{name} was stopped because the user cancelled.")
        elif outcome.stopped_reason:
            lines.append(
                f"{name} was stopped early because {outcome.stopped_reason}. "
                "Do not simply retry: tell the user what happened, or make ONE changed attempt "
                "with a narrower request."
            )
        elif outcome.ok:
            lines.append(f"{name} finished.")
        else:
            reason = outcome.error or (outcome.final.subtype if outcome.final else "")
            lines.append(f"{name} did not finish successfully ({reason or 'unknown reason'}).")
        lines.append(
            f"Folder: {project} · mode: {mode}{clamp_note}"
            + (" · continued session" if resumed else "")
        )
        if model_note:
            lines.append(model_note)
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
        for note in outcome.notes:
            lines.append("Check: " + note)
        final = outcome.final
        if final and final.denials:
            lines.append(
                "Blocked tools (not allowed in this mode): " + ", ".join(sorted(set(final.denials)))
            )
        if mode != "run":
            lines.append(
                f"Verify: this run could not run commands (mode \u201c{mode}\u201d), so anything "
                f"{name} says it ran, tested, or printed is its own claim, not a result. Check "
                "files directly, or ask the user to allow \u201cEdit files and run commands\u201d."
            )
        text = outcome.text.strip()
        if text:
            if len(text) > _SUMMARY_LIMIT:
                text = text[:_SUMMARY_LIMIT].rstrip() + "…"
            lines.append(f"{name} said:\n" + text)
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

    def __init__(
        self, project: Path, on_rate: Callable[[RateLimit], None], backend: Backend | None = None
    ) -> None:
        self._backend: Backend = backend or ClaudeBackend()
        self._name = self._backend.name
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

    async def start(
        self, title: str, *, request: str, continuing: bool, note: str = ""
    ) -> None:
        self._title = screen(title, limit=80)
        self._started = time.monotonic()
        detail = " · ".join(
            part
            for part in (f"continuing the same {self._name} session" if continuing else "", note)
            if part
        )
        await self._send(
            {
                "id": f"{self._group}:round",
                "kind": "round",
                "label": self._title,
                "status": "running",
                "tool": self._backend.tool_name,
                "detail": screen(detail, limit=300),
            }
        )
        await self._send(
            {
                "id": f"{self._group}:prompt",
                "kind": "prompt",
                "label": f"Sani asked {self._name}",
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
        if outcome is not None:
            if outcome.files_changed:
                shown = ", ".join(outcome.files_changed[:8])
                more = len(outcome.files_changed) - 8
                tail = f" and {more} more" if more > 0 else ""
                await self._send(
                    {
                        "id": f"{self._group}:files",
                        "kind": "note",
                        "label": f"Files changed: {shown}{tail}",
                        "status": "info",
                    }
                )
            for index, line in enumerate(outcome.notes[:6]):
                await self._send(
                    {
                        "id": f"{self._group}:check{index}",
                        "kind": "note",
                        "label": screen(line, limit=300),
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
                    "label": f"{self._name} replied",
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
                "tool": self._backend.tool_name,
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
                    "label": f"Waiting for {self._name} to respond again",
                    "status": "info",
                }
            )
        elif isinstance(event, RateLimit):
            self._on_rate(event)
        elif isinstance(event, Final):
            return
