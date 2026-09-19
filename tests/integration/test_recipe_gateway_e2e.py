"""WP5 gate: exact known command finishes WITHOUT any model call.

Through the production lifespan: 'Open Chrome' routes to open_app.v1,
executes locally through the RecipeExecutor, and the agent is never
invoked (model_calls == 0). The scripted model asserts this by failing
the test if it is ever invoked.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from langchain_core.tools import StructuredTool

from assistant.main import create_application
from assistant.settings import Settings
from tests.helpers.scripted_model import ScriptedChatModel


def _fake_tool(name: str, result: dict[str, Any]) -> StructuredTool:
    async def coro(**kwargs: Any) -> Any:
        return result

    return StructuredTool(
        name=name,
        description=name,
        args_schema={"type": "object", "properties": {}},
        coroutine=coro,
    )


class NeverCalledModel(ScriptedChatModel):
    """Scripted model whose invocation FAILS the test (recipe must not call it)."""

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        raise AssertionError("model must not be called for an exact local command")


@pytest.fixture
async def recipe_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    from assistant.tools.cua import CuaConnection
    from assistant.tools.policy import apply_tool_policy

    async def observe(**kwargs: Any) -> Any:
        # get_window_state evidence: foreground_app identity, no modal.
        return {"status": "ok", "foreground_app": "chrome", "modal": False}

    async def launch(**kwargs: Any) -> Any:
        return {"status": "ok", "launched": kwargs.get("app_id", "?")}

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
    wrapped, _ = apply_tool_policy([observe_tool, launch_tool])

    @asynccontextmanager
    async def fake_cua_connection(settings: Any) -> AsyncIterator[CuaConnection]:
        yield CuaConnection(
            tools=wrapped,
            tool_names=["get_window_state", "launch_app"],
            discovered_names=["get_window_state", "launch_app"],
            skipped_names=[],
            tools_by_name={t.name: t for t in wrapped},
            lifecycle_tools_by_name={},
        )

    model = NeverCalledModel(responses=[AIMessage('should never be reached')])
    monkeypatch.setattr("assistant.main.build_chat_model", lambda settings: model)
    monkeypatch.setattr("assistant.main.open_cua_connection", fake_cua_connection)
    app = create_application(
        Settings(
            agent_gateway_api_key="test-gateway-key",
            model_provider="openrouter",
            openrouter_api_key="dummy",
            cua_enabled=True,
        designer_enabled=False,  # hermetic legacy path
            active_cursor_persistence_enabled=False,
        )
    )
    async with app.router.lifespan_context(app):
        app.state._never_model = model
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway"
        ) as client:
            yield client, app


async def test_exact_open_command_uses_zero_model_calls(
    recipe_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = recipe_gateway
    turn = uuid.uuid4().hex
    headers = {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "recipe-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }
    payload = {
        "model": app.state.settings.assistant_model_id,
        "messages": [{"role": "user", "content": "Open Chrome"}],
        "stream": False,
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "Opened chrome." in body["choices"][0]["message"]["content"], body
    # Zero model calls: the scripted model raises if ever invoked, and its
    # call counter stayed at zero through the whole request.
    assert getattr(app.state._never_model, "call_index", 0) == 0, (
        "recipe path must not call the model"
    )
    # The run is still claimed and completed in the registry.
    run_id = body["id"].removeprefix("chatcmpl-")
    record = await app.state.run_store.get_run(run_id)
    assert record is not None and record.status == "completed"

