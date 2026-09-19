"""WP8 negative tests: the fast path must be narrow; the agent stays king.

These pin the SAFETY property of the routing layer: anything that is not
an exact, unambiguous, single-clause local command must reach the general
agent — never a recipe. The scripted agent FAILS the test if never
invoked, so a router regression (over-eager matching) is caught.
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

#: Master-plan negative cases (WP5/WP8): each must NOT execute a recipe.
NEGATIVE_INPUTS = [
    "Do not open Chrome",
    "Open Terminal, delete the project, then open Chrome",
    "Open Excel",
    "open calculator and compute 2+2",
    "please don't launch anything",
]


@pytest.fixture
async def negative_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, Any]]:
    from assistant.tools.cua import CuaConnection, _filtered_connection
    from assistant.tools.result_normalizer import ToolOutcome

    async def observe(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "not_applicable", structured={"foreground_app": "chrome"})

    async def noop(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "confirmed", structured={})

    def mk(name: str, props: dict[str, Any], fn: Any) -> StructuredTool:
        return StructuredTool(
            name=name,
            description=name,
            args_schema={"type": "object", "properties": props},
            coroutine=fn,
        )

    tools = [
        mk("launch_app", {"bundle_id": {"type": "string"}}, noop),
        mk("get_desktop_state", {"session": {"type": "string"}}, observe),
        mk("start_session", {"session": {"type": "string"}}, noop),
        mk("end_session", {"session": {"type": "string"}}, noop),
        mk(
            "set_agent_cursor_enabled",
            {"session": {"type": "string"}, "enabled": {"type": "boolean"}},
            noop,
        ),
        mk("set_agent_cursor_motion", {"session": {"type": "string"}}, noop),
    ]

    @asynccontextmanager
    async def fake_conn(settings: Any) -> AsyncIterator[CuaConnection]:
        yield _filtered_connection(tools)

    model = ScriptedChatModel(responses=[AIMessage("agent reply") for _ in NEGATIVE_INPUTS])
    import assistant.main as main_mod

    main_mod.build_chat_model = lambda settings: model
    main_mod.open_cua_connection = fake_conn
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
            yield client, app, model


def _headers(turn: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "negative-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }


@pytest.mark.parametrize("text", NEGATIVE_INPUTS)
async def test_negations_and_multi_clauses_reach_the_agent(
    negative_gateway: tuple[httpx.AsyncClient, Any, Any], text: str
) -> None:
    client, app, model = negative_gateway
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
    assert response.status_code == 200
    # The agent (not a recipe) answered: no 'Opened'/'=' prefix from the
    # recipe renderer appears in the reply.
    content = response.json()["choices"][0]["message"]["content"]
    assert not content.startswith(("Opened ", "= ")), (
        f"{text!r} was routed to a recipe; must reach the agent"
    )


async def test_no_negation_ever_dispatches_a_native_mutation(
    negative_gateway: tuple[httpx.AsyncClient, Any, Any],
) -> None:
    """Cumulative: across all negatives, the desktop is never touched."""
    client, app, _ = negative_gateway
    for text in NEGATIVE_INPUTS:
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
        assert response.status_code == 200
        run_id = response.json()["id"].removeprefix("chatcmpl-")
        record = await app.state.run_store.get_run(run_id)
        assert record is not None
        assert record.actions == [], (
            f"{text!r} produced native actions {record.actions}"
        )
