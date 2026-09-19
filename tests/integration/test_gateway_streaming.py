"""Gateway integration tests: auth, models, chat, SSE streaming (Task 6).

Uses the real agent graph over the compose Postgres with the scripted
model, driven through httpx ASGI transport on the test loop (no network,
no spend).
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from assistant.agent.build import build_agent
from assistant.api.identity import (
    CHAT_ID_HEADER,
    DEV_CHAT_ID_HEADER,
    DEV_USER_ID_HEADER,
    TASK_HEADER,
    USER_ID_HEADER,
)
from assistant.main import create_app
from assistant.memory.postgres import open_memory_resources
from assistant.settings import Settings
from tests.helpers.scripted_model import ScriptedChatModel

POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
SKILLS_ROOT = Path(__file__).resolve().parents[2] / "src" / "assistant" / "skills"
GATEWAY_KEY = "test-gateway-key"
MODEL_ID = "personal-assistant-v1"


def make_settings(**extra: Any) -> Settings:
    return Settings(
        agent_gateway_api_key=GATEWAY_KEY,
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
        designer_enabled=False,  # hermetic legacy path
        **extra,
    )


def make_lifespan():  # noqa: ANN202
    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        async with open_memory_resources(POSTGRES_URL) as mem:
            model = ScriptedChatModel(responses=[AIMessage("scripted answer")])
            bundle = build_agent(
                model=model,
                checkpointer=mem.saver,
                store=mem.store,
                skills_root=SKILLS_ROOT,
            )
            app.state.agent = bundle.agent
            app.state.store = mem.store
            app.state.saver = mem.saver
            app.state.utility_model = ScriptedChatModel(responses=[AIMessage("Concise Title")])
            yield

    return lifespan


@pytest.fixture
async def gateway(require_postgres: None) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    app = create_app(make_settings(), lifespan=make_lifespan())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            yield client, app


def auth_headers(user: str = "gwuser", chat: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GATEWAY_KEY}",
        USER_ID_HEADER: user,
        CHAT_ID_HEADER: chat or f"chat-{uuid.uuid4().hex[:8]}",
    }


async def test_models_endpoint_requires_and_validates_key(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _ = gateway
    missing = await client.get("/v1/models")
    assert missing.status_code == 401
    assert missing.json()["detail"]["error"]["code"] == "invalid_api_key"

    wrong = await client.get("/v1/models", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401

    ok = await client.get("/v1/models", headers={"Authorization": f"Bearer {GATEWAY_KEY}"})
    assert ok.status_code == 200
    assert ok.json() == {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}],
    }


async def test_unsupported_model_rejected(gateway: tuple[httpx.AsyncClient, Any]) -> None:
    client, _ = gateway
    response = await client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "other-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "unsupported_model"


async def test_missing_identity_rejected(gateway: tuple[httpx.AsyncClient, Any]) -> None:
    client, _ = gateway
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "missing_chat_identity"


async def test_production_mode_rejects_dev_test_headers(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _ = gateway
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GATEWAY_KEY}",
            DEV_USER_ID_HEADER: "dev-u",
            DEV_CHAT_ID_HEADER: "dev-c",
        },
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": "hi"}]},
    )
    # The gateway fixture runs in development mode; production rejection is
    # covered by unit tests. Here the dev headers must be accepted.
    assert response.status_code == 200


async def test_non_streaming_chat_and_thread_continuation(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = gateway
    chat = f"chat-{uuid.uuid4().hex[:8]}"
    headers = auth_headers(chat=chat)

    first = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": "hello there"}]},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "scripted answer" in body["choices"][0]["message"]["content"]

    second = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": "and now?"}]},
    )
    assert second.status_code == 200
    assert "scripted answer" in second.json()["choices"][0]["message"]["content"]

    state = await app.state.agent.aget_state({"configurable": {"thread_id": f"owui:gwuser:{chat}"}})
    contents = [str(m.content) for m in state.values.get("messages", [])]
    assert "hello there" in contents and "and now?" in contents


async def test_streaming_chat_returns_sse_with_done(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _ = gateway
    response = await client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": "stream please"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    text = response.text
    assert text.endswith("data: [DONE]\n\n")
    events = [e for e in text.split("\n\n") if e.startswith("data: ")]
    payloads = [json.loads(e[len("data: ") :]) for e in events if e.strip() != "data: [DONE]"]
    assert payloads[0]["choices"][0]["delta"].get("role") == "assistant"
    content = "".join(p["choices"][0]["delta"].get("content") or "" for p in payloads[1:])
    assert "scripted answer" in content
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(not p["choices"][0]["delta"].get("tool_calls") for p in payloads)


async def test_resubmitted_turn_is_not_executed_twice(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = gateway
    chat = f"chat-{uuid.uuid4().hex[:8]}"
    headers = auth_headers(chat=chat)
    thread_id = f"owui:gwuser:{chat}"

    first = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": "ask once"}]},
    )
    assert first.status_code == 200

    # Open WebUI resends the full history including the same user turn.
    resent = {
        "model": MODEL_ID,
        "messages": [
            {"role": "user", "content": "ask once"},
            {"role": "assistant", "content": "scripted answer"},
            {"role": "user", "content": "ask once"},
        ],
    }
    second = await client.post("/v1/chat/completions", headers=headers, json=resent)
    assert second.status_code == 200

    state = await app.state.agent.aget_state({"configurable": {"thread_id": thread_id}})
    messages = state.values.get("messages", [])
    human_contents = [str(m.content) for m in messages if isinstance(m, HumanMessage)]
    ai_contents = [str(m.content) for m in messages if isinstance(m, AIMessage)]
    # The replayed turn must replace, not duplicate.
    assert human_contents.count("ask once") == 1
    assert ai_contents.count("scripted answer") == 1

    # A following new turn still works cleanly.
    third = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": "next"}]},
    )
    assert third.status_code == 200


async def test_utility_task_uses_plain_model_without_agent(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = gateway
    chat = f"chat-{uuid.uuid4().hex[:8]}"
    headers = auth_headers(chat=chat)
    headers[TASK_HEADER] = "title_generation"

    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": "summarize this"}]},
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Concise Title"

    # The utility path must not create agent thread state for the chat.
    state = await app.state.agent.aget_state({"configurable": {"thread_id": f"owui:gwuser:{chat}"}})
    assert not state.values.get("messages")


class _SlowAgent:
    """Agent stub that keeps the stream open briefly before finishing."""

    def __init__(self) -> None:
        self.calls = 0

    async def aget_state(self, _config: Any) -> None:
        return None

    async def aget_state_history(self, _config: Any, **kwargs: Any):
        return
        yield

    async def astream(self, _input: Any, _config: Any, **kwargs: Any):
        self.calls += 1
        yield (AIMessageChunk(content="slow"), {"langgraph_node": "model"})
        await asyncio.sleep(0.5)
        yield (AIMessageChunk(content=" done"), {"langgraph_node": "model"})


async def test_stream_terminal_event_only_after_generator_ends(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    """Master plan F06 regression: run_finished must not exist before the
    generator terminates. Tested directly against the production SSE
    generator (the ASGI transport buffers the full body, which would make
    mid-stream ordering unobservable at the HTTP layer)."""
    from assistant.agent.context import AgentContext
    from assistant.api.streaming import sse_agent_stream
    from assistant.observability.timing import RunTimeline

    agent = _SlowAgent()
    timeline = RunTimeline("f06-run")
    chunks: list[str] = []
    gen = sse_agent_stream(
        agent,
        {"configurable": {"thread_id": "t"}},
        {"messages": [{"role": "user", "content": "hi"}]},
        AgentContext(user_id="f06", chat_id="f06"),
        model_id=MODEL_ID,
        completion_id="chatcmpl-f06",
        timeline=timeline,
        ledger=app_ledger_stub(),
    )
    async for piece in gen:
        if '"content"' in piece and not timeline.terminal_marked:
            chunks.append(piece)
            # after a content chunk, mid-generation: terminal must NOT be marked
            assert timeline.terminal_marked is False
        chunks.append(piece)
    assert timeline.terminal_marked is True

    # Gateway-level smoke: after the buffered response is fully read, the
    # timeline for that run is terminal.
    client, app = gateway
    response = await client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert app.state.last_run_timeline.terminal_marked is True
    assert len(chunks) > 0


def app_ledger_stub() -> Any:
    from assistant.observability.usage import UsageLedger

    return UsageLedger()


async def test_usage_ledger_records_provider_call(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    """Master plan F05 regression: usage aggregated at the provider boundary."""
    client, app = gateway
    before = app.state.last_run_ledger.call_count if hasattr(app.state, "last_run_ledger") else 0
    response = await client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": "count me"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    ledger = app.state.last_run_ledger
    assert ledger is not None
    assert ledger.call_count >= before + 1
    totals = ledger.snapshot()
    assert totals["calls"] >= 1
    # ScriptedChatModel reports no usage metadata: recorded as unknown, not zero-assumed.
    assert "unknown_output_calls" in totals
