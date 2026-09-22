"""GET /v1/runs/{run_id}/events -- neutral safe run-activity SSE (Sani).

Streams observable execution state for any run in the durable registry:
run status transitions and action-ledger rows as they are written. This is
the same data the gateway itself records (tool name, bounded target
description, state, timestamps) -- never hidden chain-of-thought, prompts,
credentials, tool arguments, or screenshots.

The route reads the shared
``run_registry``/``action_ledger`` tables through ``RunStore`` and needs no
run metadata. Transport: SSE with
heartbeats; a client disconnect never cancels the underlying run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from assistant.api.auth import SettingsDep, require_gateway_key
from assistant.api.schemas import GatewayError

logger = logging.getLogger("assistant.api.run_events")

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

_POLL_INTERVAL_SECONDS = 0.4
_HEARTBEAT_SECONDS = 10.0
_MAX_STREAM_SECONDS = 900.0

_TOOL_LABELS = {
    "computer": "Computer control",
    "calculator": "Calculator",
}


def _iso(value: Any) -> str:
    """Best-effort ISO-8601 UTC timestamp for a DB datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    return datetime.now(UTC).isoformat()


def _tool_label(action: dict[str, Any]) -> str:
    """Deterministic display label from real ledger fields only."""
    target = str(action.get("target_desc") or "").strip()
    if target:
        return target[:120]
    tool = str(action.get("tool_name") or "").strip()
    return _TOOL_LABELS.get(tool.lower(), tool.replace("_", " ").strip().title() or "Tool")


def _duration_ms(started: Any, ended: Any) -> int:
    if isinstance(started, datetime) and isinstance(ended, datetime):
        return max(0, int((ended - started).total_seconds() * 1000))
    return 0


def _event(
    seq: int,
    run_id: str,
    event_type: str,
    *,
    timestamp: str,
    label: str,
    status: str,
    tool: str = "",
    duration_ms: int = 0,
    detail: str = "",
) -> str:
    payload: dict[str, Any] = {
        "sequence": seq,
        "run_id": run_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "label": label,
        "status": status,
    }
    if tool:
        payload["tool"] = tool
    if duration_ms:
        payload["duration_ms"] = duration_ms
    if detail:
        payload["detail"] = detail[:300]
    return f"id: {seq}\nevent: {event_type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def run_activity_stream(
    run_store: Any,
    run_id: str,
    *,
    poll_interval_seconds: float = _POLL_INTERVAL_SECONDS,
    max_stream_seconds: float = _MAX_STREAM_SECONDS,
) -> AsyncIterator[str]:
    """Emit safe SSE activity events for one run until it reaches a terminal state."""
    seq = 0
    run_started_emitted = False
    processing_emitted = False
    terminal_emitted = False
    seen_action_states: dict[str, str] = {}
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_heartbeat = started

    while True:
        snapshot = await run_store.run_activity(run_id)
        if snapshot is None:
            raise GatewayError("run_not_found", f"No run {run_id!r}")

        if not run_started_emitted:
            run_started_emitted = True
            seq += 1
            yield _event(
                seq,
                run_id,
                "run.started",
                timestamp=_iso(snapshot.get("created_at")),
                label="Agent started",
                status="running",
            )

        for action in snapshot.get("actions", []):
            step_id = str(action.get("step_id") or "")
            state = str(action.get("state") or "")
            previous = seen_action_states.get(step_id)
            if previous == state:
                continue
            seen_action_states[step_id] = state
            tool = str(action.get("tool_name") or "")
            label = _tool_label(action)
            started_at = action.get("created_at")
            updated_at = action.get("updated_at")
            if previous is None and state in ("planned", "dispatched"):
                seq += 1
                yield _event(
                    seq,
                    run_id,
                    "tool.started",
                    timestamp=_iso(started_at),
                    label=label,
                    status="running",
                    tool=tool,
                )
                continue
            if state == "confirmed":
                seq += 1
                yield _event(
                    seq,
                    run_id,
                    "tool.completed",
                    timestamp=_iso(updated_at),
                    label=label,
                    status="complete",
                    tool=tool,
                    duration_ms=_duration_ms(started_at, updated_at),
                )
            elif state == "failed":
                seq += 1
                yield _event(
                    seq,
                    run_id,
                    "tool.failed",
                    timestamp=_iso(updated_at),
                    label=label,
                    status="failed",
                    tool=tool,
                    duration_ms=_duration_ms(started_at, updated_at),
                )
            elif state == "unknown":
                seq += 1
                yield _event(
                    seq,
                    run_id,
                    "tool.unknown",
                    timestamp=_iso(updated_at),
                    label=label,
                    status="unknown",
                    tool=tool,
                    detail="Outcome could not be confirmed",
                )
            elif previous is None:
                # First sight of a row already past 'planned': still report
                # the observable start, then let the state branch above run
                # on the next poll for its terminal event.
                seq += 1
                yield _event(
                    seq,
                    run_id,
                    "tool.started",
                    timestamp=_iso(started_at),
                    label=label,
                    status="running",
                    tool=tool,
                )
                seen_action_states[step_id] = "planned"

        status = str(snapshot.get("status") or "")
        if status in TERMINAL_STATUSES and not terminal_emitted:
            # Drain any pending tool state once more before closing.
            terminal_emitted = True
            seq += 1
            yield _event(
                seq,
                run_id,
                f"run.{status}",
                timestamp=_iso(snapshot.get("updated_at")),
                label=(
                    "Completed"
                    if status == "completed"
                    else "Failed"
                    if status == "failed"
                    else "Cancelled"
                ),
                status="complete" if status == "completed" else status,
                duration_ms=_duration_ms(snapshot.get("created_at"), snapshot.get("updated_at")),
                detail=str(snapshot.get("failure_reason") or ""),
            )
            return
        if not processing_emitted and status not in TERMINAL_STATUSES:
            processing_emitted = True
            seq += 1
            yield _event(
                seq,
                run_id,
                "agent.processing",
                timestamp=datetime.now(UTC).isoformat(),
                label="Agent processing",
                status="running",
            )

        now = loop.time()
        if now - started >= max_stream_seconds:
            logger.warning(
                "run_events_stream_capped",
                extra={"event": "run_events_stream_capped", "run_id": run_id},
            )
            return
        if now - last_heartbeat >= _HEARTBEAT_SECONDS:
            last_heartbeat = now
            yield ": ping\n\n"
        await asyncio.sleep(poll_interval_seconds)


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str, request: Request, settings: SettingsDep) -> Any:
    """Safe observable activity for one run, as SSE. Requires the gateway key."""
    run_store = getattr(request.app.state, "run_store", None)
    if run_store is None:
        raise GatewayError("provider_unavailable", "Run registry is not available on this gateway.")
    # Existence check happens in the handler (not the generator) so an
    # unknown run is a clean 404 before any SSE headers are sent.
    if await run_store.run_activity(run_id) is None:
        raise GatewayError("run_not_found", f"No run {run_id!r}")

    async def generator() -> AsyncIterator[str]:
        # A disconnect must never cancel the underlying run: observe it and
        # stop producing; the registry keeps its own truth either way.
        disconnect = asyncio.create_task(request.is_disconnected())
        try:
            stream = run_activity_stream(run_store, run_id)
            async for chunk in stream:
                if disconnect.done() and disconnect.result():
                    break
                yield chunk
        finally:
            disconnect.cancel()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
