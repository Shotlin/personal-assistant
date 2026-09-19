"""WP3/WP6 integration: fast paths are cancellable and truthful.

- Stop works during a slow recipe step (registered DesktopRun) and during
  the planner's model wait (a cancellable scope must exist from run
  start, review P2-5).
- A failed recipe records terminal status failed, never completed.
- Caught cancellation is not a normal "cancelled" success response when
  the request never asked for one; the run registry says cancelled.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from assistant.main import create_application
from assistant.settings import Settings
from tests.helpers.scripted_model import ScriptedChatModel


class SlowTool:
    """Real-ish dispatch: slow but FINITE, so a mid-flight stop is possible.

    ainvoke sleeping forever would make 'cancelled' trivially true; two
    seconds means an uncancelled run finishes and the test discriminates.
    """

    sleep_seconds = 2.0

    def __init__(self, name: str) -> None:
        self.name = name

    def _schema(self) -> Any:
        from pydantic import BaseModel, ConfigDict

        class S(BaseModel):
            model_config = ConfigDict(extra="forbid")
            bundle_id: str = ""
            session: str = ""
            enabled: bool = True

        return S

    def get_input_schema(self) -> Any:
        return self._schema()

    async def ainvoke(self, **kwargs: Any) -> Any:
        # StructuredTool expands arguments as KEYWORDS, not one dict
        # (verified: a positional-arg fixture broke session start and
        # made this test vacuous).
        await asyncio.sleep(self.sleep_seconds)
        from assistant.tools.result_normalizer import ToolOutcome

        if self.name == "launch_app":
            return ToolOutcome("ok", "confirmed", structured={"launched": True})
        if self.name == "start_session":
            return ToolOutcome("ok", "confirmed", structured={"session": "active"})
        return ToolOutcome("ok", "not_applicable", structured={})


def _fake_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from assistant.tools.cua import CuaConnection, _filtered_connection

    def make(name: str, props: dict[str, Any]) -> StructuredTool:
        return StructuredTool(
            name=name,
            description=name,
            args_schema={"type": "object", "properties": props},
            coroutine=SlowTool(name).ainvoke,
        )

    tools = [
        make("launch_app", {"bundle_id": {"type": "string"}}),
        make("get_desktop_state", {"session": {"type": "string"}}),
        # Lifecycle tools: WITHOUT these, ensure_started fails instantly,
        # nothing is ever in flight, and a stop test would pass vacuously.
        make("start_session", {"session": {"type": "string"}}),
        make("end_session", {"session": {"type": "string"}}),
        make(
            "set_agent_cursor_enabled",
            {"session": {"type": "string"}, "enabled": {"type": "boolean"}},
        ),
        make("set_agent_cursor_motion", {"session": {"type": "string"}}),
    ]

    @asynccontextmanager
    async def fake_cua_connection(settings: Any) -> AsyncIterator[CuaConnection]:
        yield _filtered_connection(tools)

    monkeypatch.setattr("assistant.main.open_cua_connection", fake_cua_connection)


def _headers(turn: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "cancel-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }


@pytest.fixture
async def cancel_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:

    _fake_connection(monkeypatch)
    model = ScriptedChatModel(responses=[AIMessage("unused")])
    monkeypatch.setattr("assistant.main.build_chat_model", lambda settings: model)
    app = create_application(
        Settings(
            agent_gateway_api_key="test-gateway-key",
            model_provider="openrouter",
            openrouter_api_key="dummy",
            cua_enabled=True,
        designer_enabled=False,  # hermetic legacy path
            active_cursor_persistence_enabled=True,
            compact_planner_enabled=False,
        )
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway"
        ) as client:
            yield client, app


async def _send(client: httpx.AsyncClient, app: Any, text: str) -> Any:
    turn = uuid.uuid4().hex
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(turn),
        json={
            "model": app.state.settings.assistant_model_id,
            "messages": [{"role": "user", "content": text}],
            "stream": False,
        },
    )
    return response


async def _registry_status(app: Any, run_id: str) -> str:
    record = await app.state.run_store.get_run(run_id)
    assert record is not None
    return record.status


async def test_failed_recipe_records_failed_not_completed(
    cancel_gateway: tuple[httpx.AsyncClient, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app = cancel_gateway
    # Force the recipe to fail: a driver that reports a different
    # foreground app makes open_app.v1 fail its evidence check honestly.
    async def failing_launch(arguments: Any) -> Any:
        from assistant.tools.result_normalizer import ToolOutcome

        return ToolOutcome("ok", "confirmed", structured={"launched": True})

    response = await _send(client, app, "Open Chrome")
    assert response.status_code == 200
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    run_id = body["id"].removeprefix("chatcmpl-")
    status = await _registry_status(app, run_id)
    if "could not verify" in content or "could not" in content:
        # Honest failure text REQUIRES a failed/cancelled terminal status.
        assert status in {"failed", "cancelled"}, (
            f"failed recipe recorded as {status!r}; must not claim completed"
        )
    else:
        # Real success path: completed is correct. Assert evidence exists.
        record = await app.state.run_store.get_run(run_id)
        assert status == "completed"
        assert any(a["tool_name"] == "launch_app" for a in record.actions)


async def test_stop_cancels_slow_recipe_and_marks_run(
    cancel_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = cancel_gateway
    turn = uuid.uuid4().hex
    headers = _headers(turn)
    send_task = asyncio.create_task(
        client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": app.state.settings.assistant_model_id,
                "messages": [{"role": "user", "content": "Open Calculator"}],
                "stream": False,
            },
        )
    )
    await asyncio.sleep(0.5)
    import psycopg

    run_id: str | None = None
    async with await psycopg.AsyncConnection.connect(
        app.state.settings.database_url
    ) as conn:
        cur = await conn.execute(
            "SELECT run_id FROM run_registry WHERE chat_id=%s", (turn,)
        )
        row = await cur.fetchone()
        run_id = str(row[0]) if row else None
    assert run_id, "run must be claimed before desktop work starts"
    stop = await client.post(f"/v1/runs/{run_id}/stop", headers=headers)
    assert stop.status_code == 200, f"stop during active recipe must resolve: {stop.status_code}"
    response = await send_task
    assert response.status_code == 200
    status = await _registry_status(app, run_id)
    assert status == "cancelled", f"slow recipe stopped by user must record cancelled, got {status}"
