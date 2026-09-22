"""WP4/WP5: honest failures must be diagnosable from the registry alone.

A failed fast-path run currently records only status='failed'; the
registry row says nothing about WHY. The master plan (11.x) requires run
records to carry error codes, never prompt bodies. finish() therefore
accepts an optional bounded failure reason stored in a new
`failure_reason` column (additive migration), and the chat route records
the recipe's honest reason (e.g. 'Requested app was not observed in the
foreground') on failed fast paths. Diagnostics stay content-free: the
reason is a recipe/tool error string, never user text.
"""

from __future__ import annotations

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


@pytest.fixture
async def diag_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    from assistant.tools.cua import CuaConnection, _filtered_connection
    from assistant.tools.result_normalizer import ToolOutcome

    async def launch(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "confirmed", structured={"launched": True})

    async def observe(**kwargs: Any) -> Any:
        # A different foreground app makes the recipe's evidence check fail
        # honestly: the launch was confirmed natively but verification failed.
        return ToolOutcome(
            "ok", "not_applicable",
            structured={"foreground_app": "finder", "modal": False},
        )

    async def lifecycle(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "confirmed", structured={})

    def mk(name: str, props: dict[str, Any], fn: Any) -> StructuredTool:
        return StructuredTool(
            name=name, description=name,
            args_schema={"type": "object", "properties": props}, coroutine=fn,
        )

    tools = [
        mk("launch_app", {"bundle_id": {"type": "string"}}, launch),
        mk("get_desktop_state", {"session": {"type": "string"}}, observe),
        mk("start_session", {"session": {"type": "string"}}, lifecycle),
        mk("end_session", {"session": {"type": "string"}}, lifecycle),
        mk(
            "set_agent_cursor_enabled",
            {"session": {"type": "string"}, "enabled": {"type": "boolean"}},
            lifecycle,
        ),
        mk("set_agent_cursor_motion", {"session": {"type": "string"}}, lifecycle),
    ]

    @asynccontextmanager
    async def fake_conn(settings: Any) -> AsyncIterator[CuaConnection]:
        yield _filtered_connection(tools)

    model = ScriptedChatModel(responses=[AIMessage("agent reply")])
    import assistant.main as main_mod

    main_mod.build_chat_model = lambda settings: model
    main_mod.open_cua_connection = fake_conn
    app = create_application(
        Settings(
            agent_gateway_api_key="test-gateway-key",
            model_provider="openrouter",
            openrouter_api_key="dummy",
            cua_enabled=True,
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


def _headers(turn: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "diag-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }


@pytest.mark.parametrize("stream", [False, True])
async def test_failed_recipe_reason_is_queryable_from_registry(
    diag_gateway: tuple[httpx.AsyncClient, Any], stream: bool
) -> None:
    client, app = diag_gateway
    turn = uuid.uuid4().hex
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(turn),
        json={
            "model": app.state.settings.assistant_model_id,
            "messages": [{"role": "user", "content": "Open Chrome"}],
            "stream": stream,
        },
    )
    assert response.status_code == 200
    if stream:
        assert "data: [DONE]" in response.text
        chunks = [json_loads(line.removeprefix("data: "))
                  for line in response.text.splitlines()
                  if line.startswith("data: {")]
        run_id = chunks[0]["id"].removeprefix("chatcmpl-")
        reply = "".join(
            c["choices"][0]["delta"].get("content", "") or "" for c in chunks
        )
    else:
        body = response.json()
        run_id = body["id"].removeprefix("chatcmpl-")
        reply = body["choices"][0]["message"]["content"]
    assert "could not verify" in reply
    record = await app.state.run_store.get_run(run_id)
    assert record is not None
    assert record.status == "failed"
    # The registry alone must answer 'why did this run fail' (bounded,
    # no user content).
    reason = getattr(record, "failure_reason", None)
    assert isinstance(reason, str) and reason, (
        "failed run must carry a queryable failure reason"
    )
    assert "foreground" in reason or "observed" in reason
    assert "Open Chrome" not in reason, "failure reason must not contain user text"
    assert len(reason) <= 300


def json_loads(payload: str) -> Any:
    import json

    return json.loads(payload)


async def test_regenerating_a_failed_turn_executes_again(
    diag_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    """Live incident 2026-09-18: Open WebUI regeneration reuses the same
    user-message id ('2/2' in the UI). The old dedup answered the retry
    with '[This message was already received...]' forever. A retry after
    a TERMINAL failure must execute again."""
    client, app = diag_gateway
    turn = uuid.uuid4().hex
    payload = {
        "model": app.state.settings.assistant_model_id,
        "messages": [{"role": "user", "content": "Open Chrome"}],
        "stream": False,
    }
    first = await client.post("/v1/chat/completions", headers=_headers(turn), json=payload)
    assert first.status_code == 200
    assert "could not verify" in first.json()["choices"][0]["message"]["content"]

    # Regeneration: SAME chat id and user-message id (exactly what Open
    # WebUI sends on the '2/2' retry path).
    second = await client.post("/v1/chat/completions", headers=_headers(turn), json=payload)
    assert second.status_code == 200
    content = second.json()["choices"][0]["message"]["content"]
    assert "already received" not in content, (
        f"failed turn was not retryable: {content!r}"
    )
    assert "could not verify" in content, "the retry must actually execute"
    run_id = second.json()["id"].removeprefix("chatcmpl-")
    record = await app.state.run_store.get_run(run_id)
    assert record is not None
    assert record.status == "failed"
    assert record.failure_reason  # second attempt's own reason is queryable
