"""Rule-based supervision of one Claude Code run (no model calls, no tokens).

The point is to stop *before* a run burns the user's plan on a loop: the same
action again and again, a streak of failing tool calls, a request for a
permission nobody can grant in this mode, or silence. Judgement about whether
the work is *right* belongs to the Deep Agent after the run; this only keeps a
run from running away. Every trip ends the run cleanly and says why.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from assistant.claude_code.events import (
    ClaudeEvent,
    Final,
    PermissionDenied,
    Retry,
    ToolEnd,
    ToolStart,
)


@dataclass(frozen=True, slots=True)
class WatchdogLimits:
    #: The identical call (same tool, same input) this many times is a loop.
    repeat_limit: int = 3
    #: Consecutive failing tool calls before giving up.
    error_streak_limit: int = 4
    #: Permission denials before reporting "needs a decision".
    denial_limit: int = 2
    #: Seconds without any event.
    idle_seconds: float = 180.0
    #: Whole-run ceiling.
    max_seconds: float = 900.0
    #: Provider retries (rate limit / overload) before stopping instead of waiting.
    retry_limit: int = 3


class Watchdog:
    def __init__(
        self,
        limits: WatchdogLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or WatchdogLimits()
        self._clock = clock
        self._started = self._clock()
        self._last_event = self._started
        self._seen: dict[str, int] = {}
        self._error_streak = 0
        self._denials = 0
        self._retries = 0
        self._open: dict[str, str] = {}

    def touch(self) -> None:
        """Any line from the child counts as life, even one we do not parse."""
        self._last_event = self._clock()

    def observe(self, event: ClaudeEvent) -> str | None:
        """Feed one event; return a plain-words reason to stop, or None."""
        self._last_event = self._clock()
        if isinstance(event, ToolStart):
            self._open[event.id] = event.name
            if event.mutating or event.name in {"Read", "Grep", "Glob"}:
                count = self._seen.get(event.fingerprint, 0) + 1
                self._seen[event.fingerprint] = count
                if count >= self.limits.repeat_limit:
                    return f"it repeated the same step ({event.label}) {count} times"
        elif isinstance(event, ToolEnd):
            self._open.pop(event.id, None)
            self._error_streak = 0 if event.ok else self._error_streak + 1
            if self._error_streak >= self.limits.error_streak_limit:
                return f"{self._error_streak} steps in a row failed"
        elif isinstance(event, PermissionDenied):
            self._denials += 1
            if self._denials >= self.limits.denial_limit:
                return f"it needs permission for {event.tool}, which this run does not allow"
        elif isinstance(event, Retry):
            self._retries += 1
            if event.error == "rate_limit" or self._retries >= self.limits.retry_limit:
                return (
                    "Claude is rate limited right now"
                    if event.error == "rate_limit"
                    else "Claude kept failing to respond"
                )
        elif isinstance(event, Final):
            return None
        return self.check_time()

    def check_time(self) -> str | None:
        """Called on a timer too, so silence alone can trip it."""
        now = self._clock()
        if now - self._started > self.limits.max_seconds:
            return "it ran past its time limit"
        if now - self._last_event > self.limits.idle_seconds:
            return "it stopped responding"
        return None
