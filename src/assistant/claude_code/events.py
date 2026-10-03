"""Typed events parsed from Claude Code's ``--output-format stream-json`` stream."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Init:
    session_id: str
    model: str = ""
    cwd: str = ""
    permission_mode: str = ""


@dataclass(frozen=True, slots=True)
class ToolStart:
    """A tool call Claude Code is making. ``fingerprint`` identifies repeats."""

    id: str
    name: str
    label: str
    detail: str
    fingerprint: str
    #: True when the call can change files or run commands.
    mutating: bool = False
    #: Path touched, relative to the project, when the tool has one.
    path: str = ""


@dataclass(frozen=True, slots=True)
class ToolEnd:
    id: str
    ok: bool
    summary: str = ""


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class AssistantText:
    text: str


@dataclass(frozen=True, slots=True)
class PermissionDenied:
    tool: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Plan limits as Claude Code reports them. Unknown stays None."""

    kind: str = ""
    status: str = ""
    resets_at: float | None = None
    used_percent: float | None = None


@dataclass(frozen=True, slots=True)
class Retry:
    attempt: int
    max_retries: int
    error: str = ""


@dataclass(frozen=True, slots=True)
class Final:
    ok: bool
    subtype: str
    text: str
    session_id: str = ""
    turns: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0
    #: Tokens in the conversation after this run (best estimate of context use).
    context_tokens: int | None = None
    context_window: int | None = None
    denials: tuple[str, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)


ClaudeEvent = (
    Init
    | ToolStart
    | ToolEnd
    | TextDelta
    | AssistantText
    | PermissionDenied
    | RateLimit
    | Retry
    | Final
)
