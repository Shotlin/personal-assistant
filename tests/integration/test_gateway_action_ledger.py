"""Gateway passes RunActionLedger into the run scope (WP4/WP5 seam).

Through the REAL production lifespan (cua disabled, desktop persistence
off — the ledger path is independent of sessions), a scripted model that
invokes one mutating tool must leave exactly one 'confirmed' ledger row
attributed to the claimed run. No ledger without a claim (no
user-message id) by design: rows are FK-bound to run_registry.
"""

from __future__ import annotations

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


def _fake_click_tool() -> StructuredTool:
    async def coro(**kwargs: Any) -> Any:
        return {"ok": True, "clicked": kwargs.get("element_token", "?")}

    return StructuredTool(
        name="click",
        description="click",
        args_schema={"type": "object", "properties": {}},
        coroutine=coro,
    )


class ToolCallingScript(ScriptedChatModel):
    """First response invokes the click tool; second responds with text."""

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.seen.append(list(messages))
        if self.call_index == 0:
            message: AIMessage = AIMessage(
                content="",
                tool_calls=[
                    {"name": "click", "args": {"element_token": "e1"}, "id": "call1"}
                ],
            )
        else:
            message = AIMessage("clicked for ledger test")
        self.call_index += 1
        return ChatResult(
            generations=[ChatGeneration(message=locals().get("message", AIMessage("")))]
        )


@pytest.fixture
async def ledger_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    model = ToolCallingScript(responses=[])
    monkeypatch.setattr("assistant.main.build_chat_model", lambda settings: model)

    # One wrapped mutating tool through the REAL seam (open_cua_connection
    # -> assemble_tool_inventory -> build_agent). cua_enabled=True here so
    # the fake connection is used; no driver/daemon is touched.
    from assistant.tools.cua import CuaConnection
    from assistant.tools.policy import apply_tool_policy

    wrapped_click = apply_tool_policy([_fake_click_tool()])[0][0]

    @asynccontextmanager
    async def fake_cua_connection(settings: Any) -> AsyncIterator[CuaConnection]:
        yield CuaConnection(
            tools=[wrapped_click],
            tool_names=["click"],
            discovered_names=["click"],
            skipped_names=[],
            tools_by_name={"click": wrapped_click},
            lifecycle_tools_by_name={},
        )

    monkeypatch.setattr("assistant.main.open_cua_connection", fake_cua_connection)
    app = create_application(
        Settings(
            agent_gateway_api_key="test-gateway-key",
            model_provider="openrouter",
            openrouter_api_key="dummy",
            cua_enabled=True,
            active_cursor_persistence_enabled=False,
        )
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway"
        ) as client:
            yield client, app


async def test_mutating_tool_call_writes_one_confirmed_row(
    ledger_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = ledger_gateway
    run_store = app.state.run_store
    turn = uuid.uuid4().hex
    headers = {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "ledger-gw",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }
    payload = {
        "model": app.state.settings.assistant_model_id,
        "messages": [{"role": "user", "content": "click the button"}],
        "stream": False,
    }
    first = await client.post("/v1/chat/completions", headers=headers, json=payload)
    assert first.status_code == 200
    run_id = first.json()["id"].removeprefix("chatcmpl-")
    record = await run_store.get_run(run_id)
    assert record is not None
    states = [a["state"] for a in record.actions]
    tools = [a["tool_name"] for a in record.actions]
    assert tools.count("click") == 1, f"expected exactly one click row, got {tools}"
    assert states.count("confirmed") == 1, f"expected exactly one confirmed, got {states}"
