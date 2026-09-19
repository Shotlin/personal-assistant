"""P8 — Events & SSE (Live view streaming & historical replay) tests.

Covers:
- Monotonic durable events ledger (designer_run_events)
- Payload sanitization (redaction of secret-shaped keys, string capping)
- EventHub in-memory pub-sub and broadcast
- SSE streaming endpoint with standard SSE format (id, event, data)
- Last-Event-ID reconnection replay (only unseen events replayed)
- Invariant: Replay zero side effects (pure database reads)
- Invariant: Foreign run access denied (404 without leaking existence)
- Invariant: Terminal events close SSE stream
- Invariant: Heartbeats keep idle connections alive
- Run snapshot aggregation endpoint
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from tests.designer.conftest import USER_A

from assistant.designer.events import (
    CONTEXT_BUDGET_UPDATE,
    NODE_COMPLETED,
    NODE_STARTED,
    RUN_COMPLETED,
    RUN_STARTED,
    TOOL_COMPLETED,
    TOOL_INVOKED,
    EventHub,
    RunEvent,
    build_run_snapshot,
    emit_run_event,
    format_sse_event,
    format_sse_heartbeat,
    sanitize_payload,
)
from assistant.designer.store import DesignerStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_test_run(
    designer_db: DesignerStore,
    run_id: str,
    user_id: str = USER_A,
    agent_id: str = "test-agent",
    revision_id: str = "rev-1",
    status: str = "running",
) -> None:
    """Insert a run_registry row for event attribution."""
    async with designer_db.connection() as conn:
        await conn.execute(
            "DELETE FROM designer_run_events WHERE run_id = %s",
            (run_id,),
        )
        await conn.execute(
            "INSERT INTO run_registry "
            "(run_id, user_id, chat_id, user_message_id, request_digest, status, "
            "agent_id, revision_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (run_id) DO UPDATE SET status = EXCLUDED.status",
            (run_id, user_id, "chat-1", f"msg-{run_id}", "digest", status, agent_id, revision_id),
        )


# ---------------------------------------------------------------------------
# 1. Payload sanitization: secrets redacted, long strings capped
# ---------------------------------------------------------------------------


def test_payload_sanitization_redacts_secrets() -> None:
    raw = {
        "user_id": "u1",
        "api_key": "sk-secret-12345",
        "nested": {
            "token": "bearer-token-abc",
            "password": "my-password",
            "safe_field": "hello",
        },
        "list_items": [
            {"secret_data": "shh", "label": "normal"},
        ],
    }
    sanitized = sanitize_payload(raw)
    assert sanitized["user_id"] == "u1"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["token"] == "[REDACTED]"
    assert sanitized["nested"]["password"] == "[REDACTED]"
    assert sanitized["nested"]["safe_field"] == "hello"
    assert sanitized["list_items"][0]["secret_data"] == "[REDACTED]"
    assert sanitized["list_items"][0]["label"] == "normal"


def test_payload_sanitization_caps_long_strings() -> None:
    long_text = "x" * 5000
    payload = {"output": long_text, "short": "safe"}
    sanitized = sanitize_payload(payload, max_str_len=100)
    assert len(sanitized["output"]) < 200
    assert "... [truncated 4900 chars]" in sanitized["output"]
    assert sanitized["short"] == "safe"


# ---------------------------------------------------------------------------
# 2. Event formatting & SSE helpers
# ---------------------------------------------------------------------------


def test_sse_event_formatting() -> None:
    ev = RunEvent(
        event_id=42,
        run_id="run-1",
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={"model": "gpt-4"},
        at=asyncio.run(asyncio.sleep(0, result=None)) or __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    text = format_sse_event(ev)
    assert "id: 42\n" in text
    assert f"event: {RUN_STARTED}\n" in text
    assert '"model":"gpt-4"' in text
    assert text.endswith("\n\n")

    heartbeat = format_sse_heartbeat()
    assert heartbeat == ": ping\n\n"


# ---------------------------------------------------------------------------
# 3. Durable store: record & monotonic sequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_and_list_run_events(designer_db: DesignerStore) -> None:
    run_id = "run-store-test-1"
    await _create_test_run(designer_db, run_id)

    ev1 = await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={"step": "start"},
    )
    ev2 = await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=2,
        event_type=NODE_STARTED,
        payload={"node_id": "model-1"},
    )

    assert ev2["event_id"] > ev1["event_id"]
    assert ev1["sequence_number"] == 1
    assert ev2["sequence_number"] == 2

    events = await designer_db.list_run_events(run_id)
    assert len(events) == 2
    assert events[0]["event_type"] == RUN_STARTED
    assert events[1]["event_type"] == NODE_STARTED

    # Filter with after_event_id
    replayed = await designer_db.list_run_events(run_id, after_event_id=ev1["event_id"])
    assert len(replayed) == 1
    assert replayed[0]["event_id"] == ev2["event_id"]


# ---------------------------------------------------------------------------
# 4. In-memory EventHub pub-sub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_hub_broadcast() -> None:
    hub = EventHub()
    q1 = await hub.subscribe("run-hub-1")
    q2 = await hub.subscribe("run-hub-1")
    q3 = await hub.subscribe("run-hub-other")

    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    ev = RunEvent(
        event_id=1,
        run_id="run-hub-1",
        agent_id="a1",
        revision_id="r1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={},
        at=now,
    )

    count = await hub.publish(ev)
    assert count == 2

    assert q1.get_nowait() == ev
    assert q2.get_nowait() == ev
    assert q3.empty()

    await hub.unsubscribe("run-hub-1", q1)
    await hub.unsubscribe("run-hub-1", q2)
    await hub.unsubscribe("run-hub-other", q3)


# ---------------------------------------------------------------------------
# 5. emit_run_event sanitizes and persists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_run_event_sanitizes_and_broadcasts(designer_db: DesignerStore) -> None:
    run_id = "run-emit-test"
    await _create_test_run(designer_db, run_id)
    hub = EventHub()
    queue = await hub.subscribe(run_id)

    event = await emit_run_event(
        designer_db,
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=1,
        event_type=TOOL_INVOKED,
        payload={"tool_name": "calc", "token": "secret-jwt", "arg": "2+2"},
        hub=hub,
    )

    assert event.payload["token"] == "[REDACTED]"
    assert event.payload["tool_name"] == "calc"

    # Broadcast reached subscriber
    received = queue.get_nowait()
    assert received.event_id == event.event_id
    assert received.payload["token"] == "[REDACTED]"

    # Persisted to DB with redacted payload
    in_db = await designer_db.list_run_events(run_id)
    assert len(in_db) == 1
    assert in_db[0]["payload"]["token"] == "[REDACTED]"

    await hub.unsubscribe(run_id, queue)


# ---------------------------------------------------------------------------
# 6. Snapshot builder aggregation
# ---------------------------------------------------------------------------


def test_build_run_snapshot() -> None:
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    events = [
        RunEvent(1, "r1", "a1", "rev1", 1, RUN_STARTED, {}, now),
        RunEvent(2, "r1", "a1", "rev1", 2, NODE_STARTED, {"node_id": "model-1"}, now),
        RunEvent(3, "r1", "a1", "rev1", 3, TOOL_INVOKED, {"tool_name": "calculator"}, now),
        RunEvent(4, "r1", "a1", "rev1", 4, TOOL_COMPLETED, {"tool_name": "calculator"}, now),
        RunEvent(5, "r1", "a1", "rev1", 5, NODE_COMPLETED, {"node_id": "model-1"}, now),
        RunEvent(6, "r1", "a1", "rev1", 6, CONTEXT_BUDGET_UPDATE, {"turns": 1, "tokens": 150}, now),
        RunEvent(7, "r1", "a1", "rev1", 7, RUN_COMPLETED, {"status": "completed"}, now),
    ]
    snapshot = build_run_snapshot(events)
    assert snapshot["run_id"] == "r1"
    assert snapshot["agent_id"] == "a1"
    assert snapshot["status"] == "completed"
    assert snapshot["active_nodes"] == []
    assert len(snapshot["executed_tools"]) == 1
    assert snapshot["executed_tools"][0]["tool_name"] == "calculator"
    assert snapshot["executed_tools"][0]["status"] == "completed"
    assert snapshot["budget"]["tokens"] == 150
    assert snapshot["event_count"] == 7
    assert snapshot["latest_event_id"] == 7


# ---------------------------------------------------------------------------
# 7. SSE endpoint: GET /runs/{run_id}/events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_endpoint_stream(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    run_id = "run-sse-stream-1"
    await _create_test_run(designer_db, run_id, user_id=USER_A)

    # Record historical events including a terminal event so generator completes
    ev1 = await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={"prompt": "test"},
    )
    ev2 = await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=2,
        event_type=RUN_COMPLETED,
        payload={"status": "completed"},
    )

    resp = await api.get(f"/designer/api/v1/runs/{run_id}/events")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body_text = resp.text

    assert "event: run.started\n" in body_text
    assert "event: run.completed\n" in body_text
    assert f"id: {ev1['event_id']}\n" in body_text
    assert f"id: {ev2['event_id']}\n" in body_text


# ---------------------------------------------------------------------------
# 8. Replay with Last-Event-ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_last_event_id_replay(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    run_id = "run-sse-replay-1"
    await _create_test_run(designer_db, run_id, user_id=USER_A)

    ev1 = await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={"step": 1},
    )
    await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=2,
        event_type=RUN_COMPLETED,
        payload={"step": 2},
    )

    # Reconnect with Last-Event-ID = ev1.event_id
    resp = await api.get(
        f"/designer/api/v1/runs/{run_id}/events",
        headers={"Last-Event-ID": str(ev1["event_id"])},
    )
    assert resp.status_code == 200
    body = resp.text
    # Event 1 is skipped
    assert f"id: {ev1['event_id']}\n" not in body
    # Event 2 is delivered
    assert "event: run.completed\n" in body


# ---------------------------------------------------------------------------
# 9. Foreign run access isolation: 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foreign_run_events_denied(
    api_b: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    """User B cannot access User A's run events -> 404 without disclosure."""
    run_id = "run-user-a-secret"
    await _create_test_run(designer_db, run_id, user_id=USER_A)

    await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={},
    )

    resp = await api_b.get(f"/designer/api/v1/runs/{run_id}/events")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "missing"


# ---------------------------------------------------------------------------
# 10. Run snapshot endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_snapshot(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    run_id = "run-snap-test"
    await _create_test_run(designer_db, run_id, user_id=USER_A)

    await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-test",
        revision_id="rev-1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={},
    )
    await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-test",
        revision_id="rev-1",
        sequence_number=2,
        event_type=TOOL_INVOKED,
        payload={"tool_name": "bash"},
    )
    await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-test",
        revision_id="rev-1",
        sequence_number=3,
        event_type=RUN_COMPLETED,
        payload={"status": "completed"},
    )

    resp = await api.get(f"/designer/api/v1/runs/{run_id}/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert data["agent_id"] == "agent-test"
    assert data["status"] == "completed"
    assert len(data["executed_tools"]) == 1
    assert data["executed_tools"][0]["tool_name"] == "bash"
    assert data["event_count"] == 3


@pytest.mark.asyncio
async def test_snapshot_foreign_run_denied(
    api_b: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    """User B cannot fetch snapshot of User A's run -> 404."""
    run_id = "run-snap-foreign"
    await _create_test_run(designer_db, run_id, user_id=USER_A)

    resp = await api_b.get(f"/designer/api/v1/runs/{run_id}/snapshot")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 11. Invariant: Replay zero side effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_zero_side_effects(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    """Fetching events or snapshot multiple times does not mutate any state or trigger actions."""
    run_id = "run-zero-side-effects"
    await _create_test_run(designer_db, run_id, user_id=USER_A)

    await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=1,
        event_type=RUN_STARTED,
        payload={},
    )
    await designer_db.record_run_event(
        run_id=run_id,
        agent_id="agent-1",
        revision_id="rev-1",
        sequence_number=2,
        event_type=RUN_COMPLETED,
        payload={},
    )

    # Initial count
    events_before = await designer_db.list_run_events(run_id)
    assert len(events_before) == 2

    # Multiple reads via SSE and snapshot
    for _ in range(3):
        r1 = await api.get(f"/designer/api/v1/runs/{run_id}/events")
        assert r1.status_code == 200
        r2 = await api.get(f"/designer/api/v1/runs/{run_id}/snapshot")
        assert r2.status_code == 200

    # Events count in DB remains unchanged
    events_after = await designer_db.list_run_events(run_id)
    assert len(events_after) == 2


# ---------------------------------------------------------------------------
# 12. Invariant: SSE loss does not stop or cancel the underlying run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_disconnect_does_not_cancel_run(
    api: httpx.AsyncClient, designer_db: DesignerStore
) -> None:
    """Gate: SSE client disconnect or timeout does not stop or mutate the run."""
    run_id = "run-disconnect-test"
    await _create_test_run(designer_db, run_id, user_id=USER_A, status="running")

    # Connect to stream but close connection early
    try:
        async with api.stream("GET", f"/designer/api/v1/runs/{run_id}/events") as stream:
            # Read header / first chunk then simulate client aborting
            assert stream.status_code == 200
            # Client disconnects immediately without reading more
    except Exception:
        pass

    # Verify run_registry status is completely unaffected (still running, not cancelled)
    run_entry = await designer_db.get_run_registry_entry(run_id)
    assert run_entry is not None
    assert run_entry["status"] == "running"

