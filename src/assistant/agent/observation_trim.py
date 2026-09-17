"""Trim old observation payloads from the model context (latency + tokens).

GUI-automation observations (window trees, screenshots) are the largest
things this agent handles. Only the most recent observation is useful for
addressing (older snapshots go stale anyway), so older ones collapse to a
one-line placeholder before each model call. Tool messages in the thread
state stay intact for audit; only what the MODEL sees is trimmed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

OBSERVATION_TOOL_NAMES = frozenset(
    {
        "get_window_state",
        "get_desktop_state",
        "get_accessibility_tree",
        "zoom",
        "list_apps",
        "list_windows",
    }
)

KEEP_LAST_FULL = 1
PLACEHOLDER = (
    "[Earlier observation removed to save context. The latest get_window_state "
    "below has the current element tokens; re-observe if you need this app again.]"
)
_TRIM_THRESHOLD_CHARS = 400

#: Hard ceiling on the NEWEST observation too (master plan 6.3 / F03): a
#: model requesting a huge accessibility tree must not receive an
#: unbounded payload. Above the cap the model sees a bounded head plus a
#: truncation marker with a refinement hint; the full payload stays in
#: thread state for audit, never destroyed.
NEWEST_HARD_CAP_CHARS = 12_000


class ObservationTrimMiddleware(AgentMiddleware):
    """Collapse older observation tool results before each model call."""

    name = "observation-trim"

    def wrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        return handler(self._trim(request))

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        return await handler(self._trim(request))

    def _trim(self, request: Any) -> Any:
        messages = list(getattr(request, "messages", []) or [])
        observation_indices = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, ToolMessage) and message.name in OBSERVATION_TOOL_NAMES
        ]
        stale = []
        if len(observation_indices) > KEEP_LAST_FULL:
            stale = observation_indices[:-KEEP_LAST_FULL]
        changed = False
        for index in stale:
            message = messages[index]
            if len(str(message.content)) <= _TRIM_THRESHOLD_CHARS:
                continue
            messages[index] = message.model_copy(update={"content": PLACEHOLDER})
            changed = True

        # Hard-cap the newest observation regardless of tool arguments.
        if observation_indices:
            newest_index = observation_indices[-1]
            newest = messages[newest_index]
            text = str(newest.content)
            if len(text) > NEWEST_HARD_CAP_CHARS:
                bounded = (
                    text[:NEWEST_HARD_CAP_CHARS]
                    + f"\n[observation truncated at {NEWEST_HARD_CAP_CHARS} chars; "
                    "refine with smaller max_elements/max_depth or a targeted "
                    "query instead of the full tree]"
                )
                messages[newest_index] = newest.model_copy(update={"content": bounded})
                changed = True
        if changed:
            return request.override(messages=messages)
        return request
