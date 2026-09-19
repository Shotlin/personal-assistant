"""Durable monotonic run events and real-time SSE streaming (P8, R04, R17, R18, Fix 7).

Live view in Agent Designer renders exclusively from event streams (no monitoring
LLM calls). Key invariants:
- Events are append-only and monotonic: (event_id, sequence_number).
- SSE endpoint supports historical replay via Last-Event-ID.
- Event payloads are sanitized: secrets redacted and large outputs capped.
- Replaying events has zero side effects (pure DB reads).
- Foreign run access returns 404/permission_denied without disclosing existence.
- SSE client disconnect does not cancel or terminate the underlying agent run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("assistant.designer.events")

# Standard event vocabulary (R04, R17)
RUN_STARTED = "run.started"
NODE_STARTED = "node.started"
NODE_COMPLETED = "node.completed"
NODE_FAILED = "node.failed"
TOOL_INVOKED = "tool.invoked"
TOOL_COMPLETED = "tool.completed"
TOOL_FAILED = "tool.failed"
CONTEXT_BUDGET_UPDATE = "context.budget_update"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"

TERMINAL_EVENTS = frozenset({RUN_COMPLETED, RUN_FAILED})

SENSITIVE_KEY_SUBSTRINGS = (
    "password",
    "secret",
    "api_key",
    "token",
    "authorization",
    "cookie",
    "credential",
    "private_key",
)

MAX_STRING_LENGTH = 4096


@dataclass(frozen=True)
class RunEvent:
    """A monotonic event in an agent's execution timeline."""

    event_id: int
    run_id: str
    agent_id: str
    revision_id: str
    sequence_number: int
    event_type: str
    payload: dict[str, Any]
    at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "revision_id": self.revision_id,
            "sequence_number": self.sequence_number,
            "event_type": self.event_type,
            "payload": self.payload,
            "at": self.at.isoformat(),
        }


def sanitize_payload(obj: Any, *, max_str_len: int = MAX_STRING_LENGTH) -> Any:
    """Recursively sanitize payloads by redacting sensitive keys and capping string lengths."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            key_lower = str(k).lower()
            if any(sub in key_lower for sub in SENSITIVE_KEY_SUBSTRINGS):
                cleaned[str(k)] = "[REDACTED]"
            else:
                cleaned[str(k)] = sanitize_payload(v, max_str_len=max_str_len)
        return cleaned
    if isinstance(obj, list):
        return [sanitize_payload(item, max_str_len=max_str_len) for item in obj]
    if isinstance(obj, str):
        if len(obj) > max_str_len:
            return obj[:max_str_len] + f"... [truncated {len(obj) - max_str_len} chars]"
        return obj
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return str(obj)


class EventHub:
    """In-memory pub-sub distributing real-time events to active SSE connections."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[RunEvent]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent]:
        """Create and register an event queue for a specific run."""
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        async with self._lock:
            if run_id not in self._subscribers:
                self._subscribers[run_id] = set()
            self._subscribers[run_id].add(queue)
        return queue

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue[RunEvent]) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            queues = self._subscribers.get(run_id)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    self._subscribers.pop(run_id, None)

    async def publish(self, event: RunEvent) -> int:
        """Broadcast an event to all subscribers for its run_id."""
        async with self._lock:
            queues = list(self._subscribers.get(event.run_id, ()))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("event_queue_full", extra={"run_id": event.run_id})
        return len(queues)


# Global process-local hub instance
_GLOBAL_EVENT_HUB = EventHub()


def get_event_hub() -> EventHub:
    return _GLOBAL_EVENT_HUB


async def emit_run_event(
    store: Any,
    *,
    run_id: str,
    agent_id: str,
    revision_id: str,
    sequence_number: int,
    event_type: str,
    payload: dict[str, Any],
    hub: EventHub | None = None,
) -> RunEvent:
    """Sanitize, persist to PostgreSQL, and broadcast to active SSE listeners."""
    safe_payload = sanitize_payload(payload)
    record = await store.record_run_event(
        run_id=run_id,
        agent_id=agent_id,
        revision_id=revision_id,
        sequence_number=sequence_number,
        event_type=event_type,
        payload=safe_payload,
    )
    event = RunEvent(
        event_id=record["event_id"],
        run_id=record["run_id"],
        agent_id=record["agent_id"],
        revision_id=record["revision_id"],
        sequence_number=record["sequence_number"],
        event_type=record["event_type"],
        payload=record["payload"],
        at=record["at"],
    )
    active_hub = hub or get_event_hub()
    await active_hub.publish(event)
    return event


def format_sse_event(event: RunEvent) -> str:
    """Format a RunEvent as standard Server-Sent Event text."""
    payload_json = json.dumps(event.to_dict(), separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {payload_json}\n\n"


def format_sse_heartbeat() -> str:
    """Format an SSE heartbeat comment to keep the connection alive."""
    return ": ping\n\n"


def build_run_snapshot(events: list[RunEvent]) -> dict[str, Any]:
    """Aggregate a sequence of events into an execution summary snapshot."""
    if not events:
        return {
            "status": "unknown",
            "active_nodes": [],
            "executed_tools": [],
            "event_count": 0,
            "latest_event_id": 0,
        }

    status = "running"
    active_nodes: set[str] = set()
    executed_tools: list[dict[str, Any]] = []
    budget_stats: dict[str, Any] = {}
    error_message: str | None = None

    run_id = events[0].run_id
    agent_id = events[0].agent_id
    revision_id = events[0].revision_id

    for ev in events:
        if ev.event_type == RUN_STARTED:
            status = "running"
        elif ev.event_type == NODE_STARTED:
            node_id = str(ev.payload.get("node_id") or "")
            if node_id:
                active_nodes.add(node_id)
        elif ev.event_type == NODE_COMPLETED:
            node_id = str(ev.payload.get("node_id") or "")
            active_nodes.discard(node_id)
        elif ev.event_type == TOOL_INVOKED:
            executed_tools.append({
                "tool_name": ev.payload.get("tool_name"),
                "status": "invoked",
                "sequence_number": ev.sequence_number,
            })
        elif ev.event_type in (TOOL_COMPLETED, TOOL_FAILED):
            if executed_tools:
                executed_tools[-1]["status"] = (
                    "completed" if ev.event_type == TOOL_COMPLETED else "failed"
                )
                if ev.event_type == TOOL_FAILED:
                    executed_tools[-1]["error"] = ev.payload.get("error")
        elif ev.event_type == CONTEXT_BUDGET_UPDATE:
            budget_stats = ev.payload
        elif ev.event_type == RUN_COMPLETED:
            status = "completed"
            active_nodes.clear()
        elif ev.event_type == RUN_FAILED:
            status = "failed"
            error_message = str(ev.payload.get("error") or "")
            active_nodes.clear()

    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "revision_id": revision_id,
        "status": status,
        "active_nodes": sorted(active_nodes),
        "executed_tools": executed_tools,
        "budget": budget_stats,
        "error": error_message,
        "event_count": len(events),
        "latest_event_id": events[-1].event_id,
    }
