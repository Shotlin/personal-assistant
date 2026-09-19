"""WP7/P1-3: fast-path recipe turns must reach the agent's thread memory.

Verified by probe (2026-09-17): after 'Open Chrome' executes through the
recipe route ('Opened chrome.', zero model calls), agent.aget_state for
the thread holds ZERO messages -- the next agent turn cannot remember
the exchange, violating the master-plan memory contract (F04/P1-3).
Fast paths persist the actual exchange (user text + rendered outcome,
including honest failures) via aupdate_state; best-effort: a memory
write failure is logged and never breaks the run.
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


def _tools() -> list[StructuredTool]:
    from assistant.tools.result_normalizer import ToolOutcome

    async def launch(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "confirmed", structured={"launched": True})

    async def observe(**kwargs: Any) -> Any:
        return ToolOutcome(
            "ok", "not_applicable", structured={"foreground_app": "chrome", "modal": False}
        )

    async def lifecycle(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "confirmed", structured={})

    def mk(name: str, props: dict[str, Any], fn: Any) -> StructuredTool:
        return StructuredTool(
            name=name,
            description=name,
            args_schema={"type": "object", "properties": props},
            coroutine=fn,
        )

    return [
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


@pytest.fixture
async def memory_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, ScriptedChatModel]]:
    from assistant.tools.cua import CuaConnection, _filtered_connection

    @asynccontextmanager
    async def fake_conn(settings: Any) -> AsyncIterator[CuaConnection]:
        yield _filtered_connection(_tools())

    model = ScriptedChatModel(
        responses=[AIMessage("I opened Chrome for you just now.")]
    )
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
            designer_enabled=False,  # hermetic legacy path
        )
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway"
        ) as client:
            yield client, app, model


def _headers(turn: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "memory-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }


async def test_recipe_turn_is_visible_to_the_next_agent_turn(
    memory_gateway: tuple[httpx.AsyncClient, Any, ScriptedChatModel],
) -> None:
    client, app, model = memory_gateway
    turn = uuid.uuid4().hex
    r1 = await client.post(
        "/v1/chat/completions",
        headers=_headers(turn),
        json={
            "model": app.state.settings.assistant_model_id,
            "messages": [{"role": "user", "content": "Open Chrome"}],
            "stream": False,
        },
    )
    assert r1.status_code == 200
    assert "Opened chrome." in r1.json()["choices"][0]["message"]["content"]
    assert model.call_index == 0

    # The thread checkpoint must now hold the recipe exchange...
    agent = app.state.agent
    config = {"configurable": {"thread_id": f"owui:memory-e2e:{turn}"}}
    snapshot = await agent.aget_state(config)
    messages = (snapshot.values if snapshot else {}).get("messages", [])
    assert len(messages := list(messages)) == 2, (
        f"recipe turn invisible to agent memory: {len(messages)} messages"
    )
    assert "Open Chrome" in str(messages[0].content)
    assert "Opened chrome." in str(messages[-1].content)

    # ...and the next agent turn must SEE it (memory continuity, F04).
    turn2 = uuid.uuid4().hex
    r2 = await client.post(
        "/v1/chat/completions",
        headers={**_headers(turn2), "X-OpenWebUI-Chat-Id": turn,
                 "X-OpenWebUI-User-Message-Id": turn2},
        json={
            "model": app.state.settings.assistant_model_id,
            "messages": [{"role": "user", "content": "what did you just open?"}],
            "stream": False,
        },
    )
    assert r2.status_code == 200
    seen = model.seen[-1]
    flat = " ".join(str(m.content) for m in seen)
    assert "Open Chrome" in flat and "Opened chrome." in flat, (
        "general agent must receive the persisted recipe exchange"
    )
