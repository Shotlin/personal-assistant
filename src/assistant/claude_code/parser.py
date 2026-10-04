"""Turn Claude Code's stream-json lines into typed events.

The wire format belongs to Claude Code and changes between versions, so this
parser is deliberately tolerant: unknown message types and unexpected shapes
are skipped, never raised. The only things it must get right are the ones the
UI and supervisor depend on: tool calls, their results, the final result, the
session id, and the plan-limit and context numbers when they are present.
"""

from __future__ import annotations

import json
from typing import Any

from assistant.claude_code.events import (
    AssistantText,
    ClaudeEvent,
    Final,
    Init,
    PermissionDenied,
    RateLimit,
    Retry,
    TextDelta,
    ToolEnd,
    ToolStart,
)
from assistant.claude_code.labels import MUTATING_TOOLS, describe_tool, fingerprint
from assistant.claude_code.redact import screen


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    return None


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return ""


def _fraction_to_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100.0 if value <= 1.0 else value


class StreamParser:
    """Stateful: it remembers tool names so a result can be matched to its call."""

    def __init__(self, cwd: str = "") -> None:
        self._cwd = cwd
        self._tools: dict[str, str] = {}
        self._context_tokens: int | None = None

    def feed(self, line: str) -> list[ClaudeEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(message, dict):
            return []
        kind = message.get("type")
        if kind == "system":
            return self._system(message)
        if kind == "assistant":
            return self._assistant(message)
        if kind == "user":
            return self._user(message)
        if kind == "stream_event":
            return self._stream_event(message)
        if kind == "rate_limit_event":
            return self._rate_limit(message)
        if kind == "result":
            return [self._final(message)]
        return []

    # -- message kinds -----------------------------------------------------

    def _system(self, message: dict[str, Any]) -> list[ClaudeEvent]:
        subtype = message.get("subtype")
        if subtype == "init":
            return [
                Init(
                    session_id=str(message.get("session_id") or ""),
                    model=str(message.get("model") or ""),
                    cwd=str(message.get("cwd") or ""),
                    permission_mode=str(message.get("permissionMode") or ""),
                )
            ]
        if subtype == "api_retry":
            return [
                Retry(
                    attempt=_as_int(message.get("attempt")) or 0,
                    max_retries=_as_int(message.get("max_retries")) or 0,
                    error=str(message.get("error") or ""),
                )
            ]
        if subtype == "permission_denied":
            tool = str(message.get("tool_name") or message.get("tool") or "a tool")
            detail = screen(str(message.get("message") or ""), limit=200)
            return [PermissionDenied(tool=tool, detail=detail)]
        return []

    def _assistant(self, message: dict[str, Any]) -> list[ClaudeEvent]:
        body = message.get("message")
        if not isinstance(body, dict):
            return []
        usage = body.get("usage")
        if isinstance(usage, dict) and not message.get("parent_tool_use_id"):
            keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            total = sum(_as_int(usage.get(key)) or 0 for key in keys)
            if total:
                self._context_tokens = total
        events: list[ClaudeEvent] = []
        content = body.get("content")
        if not isinstance(content, list):
            return events
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and block.get("text"):
                events.append(AssistantText(text=str(block["text"])))
            elif block_type == "tool_use":
                name = str(block.get("name") or "tool")
                raw_input = block.get("input")
                tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
                call_id = str(block.get("id") or f"call-{len(self._tools)}")
                self._tools[call_id] = name
                label, detail, path = describe_tool(name, tool_input, self._cwd)
                events.append(
                    ToolStart(
                        id=call_id,
                        name=name,
                        label=label,
                        detail=detail,
                        fingerprint=fingerprint(name, tool_input),
                        mutating=name in MUTATING_TOOLS,
                        path=path,
                    )
                )
        return events

    def _user(self, message: dict[str, Any]) -> list[ClaudeEvent]:
        body = message.get("message")
        if not isinstance(body, dict):
            return []
        content = body.get("content")
        if not isinstance(content, list):
            return []
        events: list[ClaudeEvent] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                call_id = str(block.get("tool_use_id") or "")
                failed = bool(block.get("is_error"))
                summary = screen(_result_text(block.get("content")), limit=600) if failed else ""
                events.append(ToolEnd(id=call_id, ok=not failed, summary=summary))
        return events

    def _stream_event(self, message: dict[str, Any]) -> list[ClaudeEvent]:
        event = message.get("event")
        if not isinstance(event, dict) or message.get("parent_tool_use_id"):
            return []
        if event.get("type") != "content_block_delta":
            return []
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta" and delta.get("text"):
            return [TextDelta(text=str(delta["text"]))]
        return []

    def _rate_limit(self, message: dict[str, Any]) -> list[ClaudeEvent]:
        info = message.get("rate_limit_info")
        if not isinstance(info, dict):
            info = message
        windows = info.get("unifiedWindows")
        if isinstance(windows, dict) and windows:
            # Real Claude Code reports every window (5-hour, weekly) together.
            events: list[ClaudeEvent] = []
            for kind, window in windows.items():
                if not isinstance(window, dict):
                    continue
                events.append(
                    RateLimit(
                        kind=str(kind),
                        status=str(info.get("status") or ""),
                        resets_at=_as_float(window.get("resetsAt")),
                        used_percent=_fraction_to_percent(_as_float(window.get("utilization"))),
                    )
                )
            if events:
                return events
        used = _as_float(info.get("utilization"))
        if used is None:
            used = _as_float(info.get("used_percentage"))
        else:
            used = _fraction_to_percent(used)
        return [
            RateLimit(
                kind=str(info.get("rateLimitType") or info.get("type") or ""),
                status=str(info.get("status") or ""),
                resets_at=_as_float(info.get("resetsAt") or info.get("resets_at")),
                used_percent=used,
            )
        ]

    def _final(self, message: dict[str, Any]) -> Final:
        subtype = str(message.get("subtype") or "")
        raw_usage = message.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        window: int | None = None
        model_name = ""
        model_usage = message.get("modelUsage")
        if isinstance(model_usage, dict):
            model_name = next((str(name) for name in model_usage), "")
            windows = [
                _as_int(entry.get("contextWindow"))
                for entry in model_usage.values()
                if isinstance(entry, dict)
            ]
            known = [w for w in windows if w]
            window = max(known) if known else None
        denials = tuple(
            str(item.get("tool_name") or item.get("tool") or "a tool")
            for item in (message.get("permission_denials") or [])
            if isinstance(item, dict)
        )
        failed = bool(message.get("is_error")) or subtype != "success"
        return Final(
            ok=not failed,
            subtype=subtype,
            text=str(message.get("result") or ""),
            session_id=str(message.get("session_id") or ""),
            turns=_as_int(message.get("num_turns")) or 0,
            cost_usd=_as_float(message.get("total_cost_usd")),
            duration_ms=_as_int(message.get("duration_ms")) or 0,
            context_tokens=self._context_tokens,
            context_window=window,
            model=model_name,
            denials=denials,
            usage=dict(usage),
        )
