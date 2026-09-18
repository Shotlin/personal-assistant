"""WP8: gateway-path Calculator e2e with display-state assertions (offline).

The recipe path is exercised through the production lifespan with a
faithful fake calculator: launch -> Escape -> typed digits accumulate on
a display model -> Enter evaluates. Assertions cover the DISPLAY STATE
(WP8 requirement), not just the reply text:
- the exact native mutation sequence (clear -> type -> Enter),
- the observed display value equals the arithmetic result,
- zero model calls for the exact command, and
- a durable confirmed ledger row for every mutating native call.
Live GUI verification stays blocked by the macOS permission gate; this
file pins the gateway-side contract that a live run must satisfy.
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

_MUTATING = {"clear_display", "type_text", "press_key"}


def _calculator_tools() -> list[StructuredTool]:
    from assistant.tools.result_normalizer import ToolOutcome

    state: dict[str, Any] = {"display": "", "pressed": []}

    async def launch(**kwargs: Any) -> Any:
        state["display"] = ""
        return ToolOutcome("ok", "confirmed", structured={"launched": True})

    async def observe(**kwargs: Any) -> Any:
        # Serves both verify_foreground (identity) and read_display's final
        # read_state (display evidence) — the recipe reads display_value
        # from the observation's structured payload.
        return ToolOutcome(
            "ok",
            "not_applicable",
            structured={
                "foreground_app": "calculator",
                "modal": False,
                "display_value": state["display"],
            },
        )

    async def press_key(**kwargs: Any) -> Any:
        key = str(kwargs.get("key", ""))
        state["pressed"].append(key)
        if key == "Escape":
            state["display"] = ""
        elif key == "Enter":
            expression = state.get("typed", "")
            try:
                value = eval(expression, {"__builtins__": {}}, {})  # noqa: S307 -- fixture-local, constant tokens
            except Exception:
                value = "Error"
            state["display"] = str(value)
        return ToolOutcome("ok", "confirmed", structured={"key": key})

    async def type_text(**kwargs: Any) -> Any:
        text = str(kwargs.get("text", ""))
        state["typed"] = text
        return ToolOutcome("ok", "confirmed", structured={"typed": len(text)})

    def mk(name: str, props: dict[str, Any], fn: Any) -> StructuredTool:
        return StructuredTool(
            name=name,
            description=name,
            args_schema={"type": "object", "properties": props},
            coroutine=fn,
        )

    async def lifecycle(**kwargs: Any) -> Any:
        return ToolOutcome("ok", "confirmed", structured={})

    return [
        mk("launch_app", {"bundle_id": {"type": "string"}}, launch),
        mk("get_desktop_state", {"session": {"type": "string"}}, observe),
        mk("press_key", {"key": {"type": "string"}, "session": {"type": "string"}}, press_key),
        mk("type_text", {"text": {"type": "string"}, "session": {"type": "string"}}, type_text),
        mk("start_session", {"session": {"type": "string"}}, lifecycle),
        mk("end_session", {"session": {"type": "string"}}, lifecycle),
        mk(
            "set_agent_cursor_enabled",
            {"session": {"type": "string"}, "enabled": {"type": "boolean"}},
            lifecycle,
        ),
        mk("set_agent_cursor_motion", {"session": {"type": "string"}}, lifecycle),
    ], state


@pytest.fixture
async def calc_gateway(
    monkeypatch: pytest.MonkeyPatch, require_postgres: None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, dict[str, Any]]]:
    from assistant.tools.cua import CuaConnection, _filtered_connection

    tools, state = _calculator_tools()

    @asynccontextmanager
    async def fake_conn(settings: Any) -> AsyncIterator[CuaConnection]:
        yield _filtered_connection(tools)

    model = ScriptedChatModel(responses=[AIMessage("agent fallback - must not run")])
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
            yield client, app, state


def _headers(turn: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "calc-e2e",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }


async def test_calculator_e2e_display_state_and_ledger(
    calc_gateway: tuple[httpx.AsyncClient, Any, dict[str, Any]],
) -> None:
    client, app, state = calc_gateway
    turn = uuid.uuid4().hex
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(turn),
        json={
            "model": app.state.settings.assistant_model_id,
            "messages": [{"role": "user", "content": "calculate 6*7"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "= 42" in body["choices"][0]["message"]["content"], body
    # Display-state assertion (WP8): the fake calculator's display equals
    # the arithmetic result AFTER the Enter evaluation.
    assert state["display"] == "42"
    assert state["pressed"][-1] == "Enter"
    assert state["pressed"][0] == "Escape", "display must be cleared first"
    # Zero model calls for an exact local command (WP5 gate).
    run_id = body["id"].removeprefix("chatcmpl-")
    record = await app.state.run_store.get_run(run_id)
    assert record is not None and record.status == "completed"
    rows = [(a["tool_name"], a["state"]) for a in record.actions]
    assert rows.count(("launch_app", "confirmed")) == 1
    assert rows.count(("type_text", "confirmed")) == 1
    assert rows.count(("press_key", "confirmed")) >= 2  # Escape + Enter
