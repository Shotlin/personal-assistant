"""Per-run context the Claude Code tool reads without being handed it.

The Deep Agent's tool call happens deep inside a LangGraph run, so the run's
event channel and conversation id travel as context variables, set by the entry
that owns the run (the same pattern the computer-control scope uses). A tool
called outside such a run simply has no sink and no conversation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

#: ``(kind, data)`` -> the core's ``send_event``; ``agent.progress`` carries steps.
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]

event_sink: ContextVar[EventSink | None] = ContextVar("claude_code_event_sink", default=None)
conversation_id: ContextVar[str] = ContextVar("claude_code_conversation_id", default="")
