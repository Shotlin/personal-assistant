"""WP6 e2e: natural phrasing -> one planner call -> local recipe, no agent.

Through the production lifespan with a COOPERATIVE planner model: the
first call returns the plan JSON; the recipe executes locally; the
general agent is NEVER invoked (its scripted response would fail the
test). Total model calls == 1 in normal conditions (WP6 gate).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from assistant.main import create_application
from assistant.settings import Settings
from tests.helpers.scripted_model import ScriptedChatModel

PLAN_JSON = '{"recipe_id": "open_app.v1", "arguments": {"app_id": "chrome"}}'


class PlannerThenAgentModel(ScriptedChatModel):
    """Call 1 answers the compact planner; any later call = agent = failure."""

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.seen.append(list(messages))
        self.call_index += 1
        if self.call_index == 1:
            message = AIMessage(
                content=PLAN_JSON,
                usage_metadata={"input_tokens": 123, "output_tokens": 24, "total_tokens": 147},
            )
        else:
            raise AssertionError("general agent must not run for a planned recipe")
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.fixture
async def planner_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    from assistant.tools.cua import CuaConnection, _filtered_connection
    from assistant.tools.result_normalizer import ToolOutcome

    async def observe(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "not_applicable",
                           structured={"foreground_app": "chrome", "modal": False})

    async def launch(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "confirmed", structured={"launched": "chrome"})

    observe_tool = StructuredTool(
        name="get_window_state",
        description="window state",
        args_schema={
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "include_screenshot": {"type": "boolean"},
                "max_elements": {"type": "integer"},
                "max_depth": {"type": "integer"},
            },
        },
        coroutine=observe,
    )
    launch_tool = StructuredTool(
        name="launch_app",
        description="launch app",
        args_schema={
            "type": "object",
            "properties": {"session": {"type": "string"}, "app_id": {"type": "string"}},
        },
        coroutine=launch,
    )
    @asynccontextmanager
    async def fake_cua_connection(settings: Any) -> AsyncIterator[CuaConnection]:
        yield _filtered_connection([observe_tool, launch_tool])

    model = PlannerThenAgentModel(
        responses=[AIMessage(content=PLAN_JSON), AIMessage("agent fallback - must not run")]
    )
    monkeypatch.setattr("assistant.main.build_chat_model", lambda settings: model)
    monkeypatch.setattr("assistant.main.open_cua_connection", fake_cua_connection)
    app = create_application(
        Settings(
            agent_gateway_api_key="test-gateway-key",
            model_provider="openrouter",
            openrouter_api_key="dummy",
            cua_enabled=True,
            active_cursor_persistence_enabled=False,
            compact_planner_enabled=True,
        )
    )
    async with app.router.lifespan_context(app):
        app.state._model = model
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway"
        ) as client:
            yield client, app


async def test_natural_phrasing_uses_one_call_and_local_execution(
    planner_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = planner_gateway
    turn = uuid.uuid4().hex
    headers = {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "planner-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }
    payload = {
        "model": app.state.settings.assistant_model_id,
        "messages": [{"role": "user", "content": "please launch chrome for me"}],
        "stream": False,
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "Opened chrome." in body["choices"][0]["message"]["content"], body
    assert body["usage"]["prompt_tokens"] == 123
    assert body["usage"]["completion_tokens"] == 24
    assert app.state._model.call_index == 1, (
        "one planner call; recipe execution adds zero further model calls"
    )
    run_id = body["id"].removeprefix("chatcmpl-")
    record = await app.state.run_store.get_run(run_id)
    assert record is not None and record.status == "completed"
    assert [(a["tool_name"], a["state"]) for a in record.actions] == [
        ("launch_app", "confirmed")
    ]


async def test_stream_true_gets_sse_not_json(
    planner_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    """P1-4 regression: fast paths honor the streaming contract."""
    client, app = planner_gateway
    turn = uuid.uuid4().hex
    headers = {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "planner-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }
    payload = {
        "model": app.state.settings.assistant_model_id,
        "messages": [{"role": "user", "content": "please launch chrome for me"}],
        "stream": True,
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream"), (
        f"stream=true must get SSE, got {response.headers['content-type']}"
    )
    assert "data: [DONE]" in response.text
    first = json.loads(response.text.splitlines()[0].removeprefix("data: "))
    run_id = first["id"].removeprefix("chatcmpl-")
    record = await app.state.run_store.get_run(run_id)
    assert record is not None and record.status == "completed"
    assert app.state._model.call_index == 1
