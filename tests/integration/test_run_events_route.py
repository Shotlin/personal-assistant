"""Integration tests: GET /v1/runs/{run_id}/events neutral activity SSE (Sani).

Uses the real compose PostgreSQL through the same RunStore the gateway
uses, driven through httpx ASGI transport. The stream is the observable
truth of the registry/ledger: no payloads, no prompts, no reasoning.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from assistant.main import create_app
from assistant.runtime.runs import RunStore
from assistant.settings import Settings
from tests.helpers.gateway_app import POSTGRES_URL

GATEWAY_KEY = "test-gateway-key"


def make_settings() -> Settings:
    return Settings(
        agent_gateway_api_key=GATEWAY_KEY,
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
        designer_enabled=False,
    )


@pytest.fixture
async def gateway(require_postgres: None) -> AsyncIterator[tuple[httpx.AsyncClient, RunStore]]:
    run_store = await RunStore.connect(POSTGRES_URL)
    await run_store.setup()

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        app.state.run_store = run_store
        yield

    app = create_app(make_settings(), lifespan=lifespan)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            yield client, run_store

    await run_store.close()


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {GATEWAY_KEY}"}


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


async def test_events_requires_gateway_key(
    gateway: tuple[httpx.AsyncClient, RunStore],
) -> None:
    client, _ = gateway
    response = await client.get(f"/v1/runs/{uuid.uuid4().hex}/events")
    assert response.status_code == 401


async def test_events_unknown_run_is_404(
    gateway: tuple[httpx.AsyncClient, RunStore],
) -> None:
    client, _ = gateway
    response = await client.get(
        f"/v1/runs/{uuid.uuid4().hex}/events", headers=_headers()
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "run_not_found"


async def test_events_stream_reports_started_actions_and_completion(
    gateway: tuple[httpx.AsyncClient, RunStore],
) -> None:
    client, store = gateway
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    claim = await store.claim(
        user_id=uuid.uuid4().hex[:12],
        chat_id=uuid.uuid4().hex[:12],
        user_message_id=uuid.uuid4().hex[:12],
        request_digest="digest",
        run_id=run_id,
    )
    assert claim.owned is True

    ledger_id = await store.record_action(
        run_id, step_id=f"computer:{uuid.uuid4().hex[:8]}",
        tool_name="computer", target_desc="Opening Calculator",
    )

    async def finish_soon() -> None:
        await asyncio.sleep(0.6)
        await store.mark_action(ledger_id, "confirmed", "artifact:1")
        await store.finish(run_id, "completed")

    finisher = asyncio.create_task(finish_soon())
    try:
        response = await client.get(f"/v1/runs/{run_id}/events", headers=_headers())
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(response.text)
        types = [e["event_type"] for e in events]

        assert "run.started" in types
        assert "tool.started" in types
        assert "tool.completed" in types
        assert "run.completed" in types
        assert types[-1] == "run.completed"

        completed = next(e for e in events if e["event_type"] == "run.completed")
        assert completed["run_id"] == run_id
        assert completed["status"] == "complete"
        assert "timestamp" in completed and "label" in completed

        tool = next(e for e in events if e["event_type"] == "tool.completed")
        assert tool["label"] == "Opening Calculator"
        assert tool["tool"] == "computer"
        assert isinstance(tool.get("duration_ms"), int) and tool["duration_ms"] >= 0
    finally:
        finisher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await finisher


async def test_events_stream_reports_failed_action_and_run(
    gateway: tuple[httpx.AsyncClient, RunStore],
) -> None:
    client, store = gateway
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    await store.claim(
        user_id=uuid.uuid4().hex[:12],
        chat_id=uuid.uuid4().hex[:12],
        user_message_id=uuid.uuid4().hex[:12],
        request_digest="digest",
        run_id=run_id,
    )
    ledger_id = await store.record_action(
        run_id, step_id=f"computer:{uuid.uuid4().hex[:8]}",
        tool_name="computer", target_desc="Reading screen",
    )
    await store.mark_action(ledger_id, "failed", "permission_denied")
    await store.finish(run_id, "failed", "tool failed")

    response = await client.get(f"/v1/runs/{run_id}/events", headers=_headers())
    events = _parse_sse(response.text)
    types = [e["event_type"] for e in events]
    assert "tool.failed" in types
    assert "run.failed" in types
    failed = next(e for e in events if e["event_type"] == "run.failed")
    assert failed["status"] == "failed"
    assert failed["detail"] == "tool failed"


async def test_events_stream_never_contains_payload_material(
    gateway: tuple[httpx.AsyncClient, RunStore],
) -> None:
    """Only labels/states/timestamps travel: args digests and evidence refs do not."""
    client, store = gateway
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    await store.claim(
        user_id=uuid.uuid4().hex[:12],
        chat_id=uuid.uuid4().hex[:12],
        user_message_id=uuid.uuid4().hex[:12],
        request_digest="secret-digest-value",
        run_id=run_id,
    )
    ledger_id = await store.record_action(
        run_id, step_id=f"computer:{uuid.uuid4().hex[:8]}",
        tool_name="computer", target_desc="Opening Chrome",
        args_digest="args-digest-should-not-leak",
    )
    await store.mark_action(ledger_id, "confirmed", "evidence-ref-should-not-leak")
    await store.finish(run_id, "completed")

    response = await client.get(f"/v1/runs/{run_id}/events", headers=_headers())
    assert "args-digest-should-not-leak" not in response.text
    assert "evidence-ref-should-not-leak" not in response.text
    assert "secret-digest-value" not in response.text
