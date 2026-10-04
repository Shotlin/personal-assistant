"""Run one task in ZCode's own window, as a drop-in for the CLI runner.

It has the same ``run(request, on_event=, watchdog=, cancel=)`` shape as ``ClaudeRunner`` so the
toolkit's folders, run limit, sessions, chat steps and summary all apply unchanged. What differs:
the "process" is ZCode's window, the stream is the page's conversation rows, and the result's
file list comes from the disk, not from ZCode's account of itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Protocol

from assistant.claude_code.events import (
    ClaudeEvent,
    Final,
    PermissionDenied,
    ToolEnd,
    ToolStart,
)
from assistant.claude_code.redact import screen
from assistant.claude_code.runner import RunOutcome, RunRequest
from assistant.claude_code.watchdog import Watchdog
from assistant.coding_agents.zcode_cdp import policy, usage, verify
from assistant.coding_agents.zcode_cdp.choice import pick_model, reasoning_level
from assistant.coding_agents.zcode_cdp.client import CdpError
from assistant.coding_agents.zcode_cdp.control import ControlRefused
from assistant.coding_agents.zcode_cdp.driver import WindowDriver
from assistant.coding_agents.zcode_cdp.rows import RowTracker, pending_approvals, phase_is_done
from assistant.coding_agents.zcode_model import is_zai_plan

EventCallback = Callable[[ClaudeEvent], Awaitable[None]]
Remember = Callable[[str, dict[str, Any]], Awaitable[None]]
Selection = Callable[[], Awaitable[dict[str, str]]]


class Control(Protocol):
    """Exclusive use of ZCode's window (the real ``ControlSession`` or a test double)."""

    def session(self) -> AbstractAsyncContextManager[WindowDriver]: ...


_FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


class ZCodeWindowRunner:
    def __init__(
        self,
        control: Control,
        *,
        tick_seconds: float = 0.7,
        stop_wait_seconds: float = 20.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        idle_wait_seconds: float = 60.0,
        allow_any_provider: bool = False,
        selection: Selection | None = None,
        remember: Remember | None = None,
    ) -> None:
        self._control = control
        self._allow_any = allow_any_provider
        self._selection = selection
        self._remember = remember
        self._steps: list[dict[str, str]] = []
        self._tick = tick_seconds
        self._stop_wait = stop_wait_seconds
        self._sleep = sleep
        self._idle_wait = idle_wait_seconds

    async def run(
        self,
        request: RunRequest,
        *,
        on_event: EventCallback,
        watchdog: Watchdog,
        cancel: asyncio.Event,
    ) -> RunOutcome:
        outcome = RunOutcome(session_id=request.session_id or "")
        started = time.time()
        model_line: dict[str, str] = {}
        try:
            async with self._control.session() as driver:
                await self._run_in(driver, request, outcome, on_event, watchdog, cancel, model_line)
        except ControlRefused as refused:
            outcome.error = str(refused)
        except CdpError as problem:
            outcome.error = screen(f"ZCode's window stopped answering: {problem}", limit=400)
        await self._record(request, outcome, started, model_line)
        return outcome

    async def _record(
        self, request: RunRequest, outcome: RunOutcome, started: float, model: dict[str, str]
    ) -> None:
        """Keep a short, redacted account of the last run for the diagnostics view (S7)."""
        if self._remember is None:
            return
        record = {
            "at": started,
            "seconds": round(time.time() - started, 1),
            "ok": outcome.ok,
            "project": request.cwd.name,
            "model": model.get("model", ""),
            "plan": model.get("plan", ""),
            "session_id": outcome.session_id,
            "stopped_reason": screen(outcome.stopped_reason, limit=200),
            "cancelled": outcome.cancelled,
            "error": screen(outcome.error, limit=300),
            "steps": self._steps[:40],
            "files_changed": outcome.files_changed[:40],
            "notes": [screen(n, limit=300) for n in outcome.notes[:12]],
            "tokens_used": outcome.final.usage.get("tokens_used") if outcome.final else None,
        }
        with contextlib.suppress(Exception):
            await self._remember("zcode_last_run", record)

    async def _balances(self, driver: WindowDriver) -> list[dict[str, Any]] | None:
        """ZCode's own balance figures, or None if they could not be read (never a guess)."""
        try:
            read = await driver.pages.balances()
        except (CdpError, TimeoutError):
            return None
        return [dict(i) for i in read.get("items", [])] or None

    async def _run_in(
        self,
        driver: WindowDriver,
        request: RunRequest,
        outcome: RunOutcome,
        on_event: EventCallback,
        watchdog: Watchdog,
        cancel: asyncio.Event,
        model_line: dict[str, str],
    ) -> None:
        project = Path(request.cwd)
        if not await driver.wait_until_idle(max_wait=self._idle_wait):
            outcome.error = "You are using ZCode right now, so Sani did not take over its window."
            return
        name = await driver.workspace_name(str(project))
        before, before_complete = await asyncio.to_thread(verify.snapshot, project)

        before_balances = await self._balances(driver)

        baseline = 0
        resumed = False
        if request.session_id and await driver.open_task(request.session_id, str(project)):
            resumed = True
            baseline = max((int(r["id"]) for r in (await driver.state(0))["rows"]), default=0)
        else:
            await driver.new_task(name)
            if request.session_id:
                outcome.notes.append(
                    "The earlier ZCode task could not be reopened, so this started a new task."
                )
        await driver.set_mode()
        info = await self._apply_choice(driver, request, outcome)
        if info is None:
            return
        model_line.update(model=info["model"], plan=info["plan"] or info["plan_id"])
        tracker = RowTracker(cwd=str(project), last_row=baseline)
        await driver.send(request.prompt, baseline=baseline)
        watchdog.touch()

        stopped_at: float | None = None
        loop = asyncio.get_running_loop()
        last_waiting: tuple[str, ...] = ()
        last_answered: tuple[str, ...] = ()
        last_answer_at = -100.0
        same_card_answers = 0
        last_seq = ""
        state: dict[str, Any] = {}
        done_seen = False

        async def stop(reason: str = "", *, cancelled: bool = False) -> None:
            nonlocal stopped_at
            if stopped_at is not None:
                return
            outcome.stopped_reason = outcome.stopped_reason or reason
            outcome.cancelled = outcome.cancelled or cancelled
            stopped_at = loop.time()
            with contextlib.suppress(CdpError):
                await driver.stop()

        try:
            while True:
                if cancel.is_set():
                    await stop(cancelled=True)
                state = await driver.state(baseline)
                if state.get("seq") != last_seq:
                    last_seq = str(state.get("seq"))
                    watchdog.touch()
                events = tracker.feed(state)
                for event in events:
                    self._track(event, outcome)
                    self._note_step(event)
                    await on_event(event)
                    reason = watchdog.observe(event)
                    if reason:
                        await stop(reason)
                while tracker.ran_unasked:
                    # ZCode ran this on its own (it judged it safe, so no card was shown). Sani
                    # cannot prevent that; it can stop the run and say so.
                    unasked = tracker.ran_unasked.pop(0)
                    verdict = policy.decide(
                        unasked.tool, unasked.input, ceiling=request.permission, project=project
                    )
                    if not verdict.allow:
                        note = (
                            f"ZCode used {unasked.tool} without asking, which the run limit does "
                            f"not allow ({verdict.reason}); Sani stopped the run."
                        )
                        outcome.notes.append(note)
                        await stop(note.rstrip("."))
                pending = pending_approvals(state)
                if pending and state.get("card"):
                    waiting = tuple(item.call_id for item in pending)
                    if waiting != last_waiting:
                        last_waiting, same_card_answers = waiting, 0
                    if loop.time() - last_answer_at >= 4.0 or waiting != last_answered:
                        if same_card_answers >= 3:
                            outcome.error = "ZCode's permission card would not accept an answer."
                            break
                        same_card_answers += 1
                        last_answered, last_answer_at = waiting, loop.time()
                        verdicts = [
                            policy.decide(
                                item.tool, item.input, ceiling=request.permission, project=project
                            )
                            for item in pending
                        ]
                        refused = next((v for v in verdicts if not v.allow), None)
                        await driver.answer("Deny" if refused else "Allow", waiting_on=waiting)
                        if refused is not None:
                            denied = PermissionDenied(tool=pending[0].tool, detail=refused.reason)
                            await on_event(denied)
                            outcome.notes.append(f"Denied {pending[0].tool}: {refused.reason}.")
                            reason = watchdog.observe(denied)
                            if reason:
                                await stop(reason)
                if phase_is_done(state.get("phase")) and not state.get("card"):
                    if done_seen:
                        break
                    done_seen = True  # one more read so the last rows are not lost
                reason = watchdog.check_time()
                if reason:
                    await stop(reason)
                if stopped_at is not None and loop.time() - stopped_at > self._stop_wait:
                    outcome.error = "ZCode did not stop when asked."
                    break
                await self._sleep(self._tick)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await asyncio.shield(driver.stop())
            raise

        final = tracker.final(state, model=info.get("model", ""))
        after_balances = await self._balances(driver)
        total, parts = usage.tokens_used(before_balances, after_balances)
        outcome.notes.append(usage.describe(total, parts))
        if after_balances and self._remember is not None:
            with contextlib.suppress(Exception):
                await self._remember(
                    "zcode_balances", {"as_of": time.time(), "items": after_balances}
                )
        final = dataclasses.replace(final, usage={"tokens_used": total, "by_model": parts})
        outcome.final = final
        await on_event(final)
        outcome.session_id = final.session_id or outcome.session_id
        outcome.text = tracker.last_text or "\n".join(tracker.texts)
        outcome.ok = final.ok and not outcome.stopped_reason and not outcome.cancelled
        after, after_complete = await asyncio.to_thread(verify.snapshot, project)
        change = verify.compare(before, after, complete=before_complete and after_complete)
        outcome.files_changed = change.changed
        outcome.notes.extend(verify.reconcile(change, tracker.claimed_files))
        if resumed:
            outcome.notes.append("Continued the earlier ZCode task.")
        bash_calls = [c for c, s in tracker.opened.items() if s.name == "Bash"]
        if bash_calls and not all(c in tracker.outputs for c in bash_calls):
            outcome.notes.append(
                "Commands ZCode says it ran have no captured output, so treat what it reports "
                "about them as its own claim, not a result."
            )

    async def _apply_choice(
        self, driver: WindowDriver, request: RunRequest, outcome: RunOutcome
    ) -> dict[str, Any] | None:
        """Set Sani's chosen model and reasoning level, then confirm what will really run.

        Nothing is sent unless the model that will run is on the user's Z.ai plan.
        """
        info = await driver.model_info()
        want_model, want_plan = request.model.strip(), ""
        if not want_model and self._selection is not None:
            chosen = await self._selection()
            want_model, want_plan = chosen.get("model", ""), chosen.get("provider", "")
        if want_model:
            entry, error = pick_model(
                info["offered"], want_model, want_plan, allow_any=self._allow_any
            )
            if entry is None:
                outcome.error = error
                return None
            await driver.set_model(entry)
            info = await driver.model_info()
        if not info["model"] or not info["plan_id"]:
            outcome.error = "I could not confirm which model ZCode will use, so nothing was run."
            return None
        if not self._allow_any and not is_zai_plan(info["plan_id"]):
            outcome.error = (
                f"ZCode would use {info['model']} on {info['plan_id']}, not your Z.ai plan, so "
                "I did not run it and nothing was spent."
            )
            return None
        if request.effort:
            level = reasoning_level(request.effort)
            if level:
                await driver.set_reasoning(level)
            else:
                outcome.notes.append(
                    f"ZCode has no “{request.effort}” reasoning level (low, high, max), so it "
                    "was left as it is."
                )
        return info

    def _note_step(self, event: ClaudeEvent) -> None:
        if isinstance(event, ToolStart):
            self._steps.append({"label": screen(event.label, limit=120), "status": "running"})
        elif isinstance(event, ToolEnd) and self._steps:
            for step in reversed(self._steps):
                if step["status"] == "running":
                    step["status"] = "complete" if event.ok else "failed"
                    break

    @staticmethod
    def _track(event: ClaudeEvent, outcome: RunOutcome) -> None:
        if isinstance(event, ToolStart):
            outcome.steps += 1
            if event.name == "Bash":
                outcome.commands.append(event.label)
        elif isinstance(event, ToolEnd) and not event.ok:
            outcome.failed_steps += 1
        elif isinstance(event, Final):
            outcome.final = event
