"""Truthful run timing (master plan WP1 / sections 10 and F06).

A run timeline records monotonic events and guarantees the terminal event
(``run_finished``) is marked exactly once, after real completion/cleanup --
never when a streaming response object is merely created.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("assistant.observability.timing")

TERMINAL_EVENT = "run_finished"


class RunTimeline:
    """Ordered monotonic event log for one run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._events: list[dict[str, Any]] = []
        self._terminal_marked = False
        self.started_ns = time.monotonic_ns()

    def mark(self, event: str, *, metadata: dict[str, Any] | None = None) -> None:
        """Record a non-terminal event at the current monotonic time."""
        self._append(event, metadata)

    def mark_terminal(
        self, event: str = TERMINAL_EVENT, *, metadata: dict[str, Any] | None = None
    ) -> None:
        """Record the terminal event exactly once (duplicates are ignored)."""
        if self._terminal_marked:
            logger.warning(
                "terminal_event_duplicate_ignored",
                extra={
                    "event": "terminal_event_duplicate_ignored",
                    "run_id": self.run_id,
                    "attempted": event,
                },
            )
            return
        self._terminal_marked = True
        self._append(event, metadata)

    @property
    def terminal_marked(self) -> bool:
        return self._terminal_marked

    @property
    def elapsed_ms(self) -> int:
        return (time.monotonic_ns() - self.started_ns) // 1_000_000

    def _append(self, event: str, metadata: dict[str, Any] | None) -> None:
        self._events.append(
            {
                "event": event,
                "monotonic_ns": time.monotonic_ns(),
                "metadata": dict(metadata or {}),
            }
        )

    def events(self) -> list[dict[str, Any]]:
        """Event copies for diagnostics (never includes payloads)."""
        return [dict(event) for event in self._events]
