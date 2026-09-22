"""WP7: failed fast-path recipes feed the general-agent fallback correctly.

Phase 1.1 contract (token optimization): a failed recipe renders an
honest failure to the user WITHOUT an automatic agent turn; the exchange
(objective + actual outcome) is persisted, so any subsequent general-
agent turn IS the fallback and must receive the executed-step history.
The fallback then decides next steps without silently re-running a step
that was already confirmed natively (master plan WP7: 'does not restart
completed steps').
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
async def fallback_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, Any]]:
    from assistant.tools.cua import CuaConnection, _filtered_connection
    from assistant.tools.result_normalizer import ToolOutcome

    calls: list[tuple[str, dict[str, Any]]] = []

    async def launch(**kwargs: Any) -> Any:
        calls.append(("launch_app", dict(kwargs)))
        return ToolOutcome("ok", "confirmed", structured={"launched": True})

    async def observe(**kwargs: Any) -> Any:
        calls.append(("get_desktop_state", dict(kwargs)))
        return ToolOutcome(
            "ok", "not_applicable",
            structured={"foreground_app": "finder", "modal": False},
        )

    async def lifecycle(**kwargs: Any) -> Any:
        calls.append(("lifecycle", dict(kwargs)))
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

    model = ScriptedChatModel(responses=[AIMessage("fallback handled it")])
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
        app.state._model = model  # direct handle for call-count assertions
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway"
        ) as client:
            yield client, app, calls


def _headers(turn: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "fallback-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }


async def test_failed_recipe_is_honest_and_history_reaches_fallback(
    fallback_gateway: tuple[httpx.AsyncClient, Any, Any],
) -> None:
    client, app, calls = fallback_gateway
    # 'Open Chrome' matches, but observation reports finder, so the recipe
    # fails its evidence check honestly and records 'failed'.
    turn = uuid.uuid4().hex
    objective = "Open Chrome"
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(turn),
        json={
            "model": app.state.settings.assistant_model_id,
            "messages": [{"role": "user", "content": objective}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    assert "could not verify" in content, body
    run_id = body["id"].removeprefix("chatcmpl-")
    record = await app.state.run_store.get_run(run_id)
    assert record is not None and record.status == "failed"
    assert any(name == "launch_app" for name, _ in calls), (
        "fixture requires the recipe to have attempted the launch"
    )
    # No automatic agent turn on failure (token gate): exactly zero model
    # calls for this request.
    assert app.state._model.call_index == 0

    # The failed exchange is persisted for the thread...
    snapshot = await app.state.agent.aget_state(
        {"configurable": {"thread_id": f"owui:fallback-e2e:{turn}"}}
    )
    persisted = snapshot.values.get("messages", []) if snapshot else []
    assert [(m.type, str(m.content)) for m in persisted] == [
        ("human", objective),
        ("ai", content),
    ], "failed fast-path exchange must persist for continuity"

    # ...and the NEXT agent turn (the actual fallback) receives both the
    # original objective and the honest outcome in its prompt history.
    followup = await client.post(
        "/v1/chat/completions",
        headers={**_headers(turn), "X-OpenWebUI-User-Message-Id": uuid.uuid4().hex},
        json={
            "model": app.state.settings.assistant_model_id,
            "messages": [{"role": "user", "content": "what happened on my last request?"}],
            "stream": False,
        },
    )
    assert followup.status_code == 200
    assert followup.json()["choices"][0]["message"]["content"] == "fallback handled it"
    seen = [(m.type, str(m.content)) for m in app.state._model.seen[-1]]
    flat = "\n".join(text for _, text in seen)
    assert objective in flat, "fallback must receive the original objective"
    assert "could not verify" in flat, (
        "fallback must receive the executed-step outcome so it can decide "
        "without restarting completed steps"
    )
