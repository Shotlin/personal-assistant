"""E2E: the same chat survives a full application restart (Task 8).

Two complete application wirings (the shape of a process restart) share
the compose Postgres; the second wiring must see the first turn.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from langchain_core.messages import AIMessage

from assistant.agent.build import build_agent
from assistant.api.identity import DEV_CHAT_ID_HEADER, DEV_USER_ID_HEADER
from assistant.main import create_app
from assistant.memory.postgres import open_memory_resources
from assistant.settings import Settings
from tests.helpers.scripted_model import ScriptedChatModel

POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
SKILLS_ROOT = Path(__file__).resolve().parents[2] / "src" / "assistant" / "skills"
GATEWAY_KEY = "e2e-gateway-key"
MODEL_ID = "personal-assistant-v1"


def make_lifespan(responses: list[AIMessage]):  # noqa: ANN202
    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        async with open_memory_resources(POSTGRES_URL) as mem:
            model = ScriptedChatModel(responses=responses)
            bundle = build_agent(
                model=model,
                checkpointer=mem.saver,
                store=mem.store,
                skills_root=SKILLS_ROOT,
            )
            app.state.agent = bundle.agent
            app.state.store = mem.store
            app.state.saver = mem.saver
            app.state.utility_model = model
            yield

    return lifespan


def make_settings() -> Settings:
    return Settings(
        agent_gateway_api_key=GATEWAY_KEY,
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
        designer_enabled=False,  # hermetic legacy path
    )


async def call_chat(client: httpx.AsyncClient, chat: str, content: str) -> dict[str, Any]:
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GATEWAY_KEY}",
            DEV_USER_ID_HEADER: "e2e-user",
            DEV_CHAT_ID_HEADER: chat,
        },
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": content}]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_chat_survives_application_restart(require_postgres: None) -> None:
    chat = f"chat-{uuid.uuid4().hex[:8]}"

    # "Process 1": first turn.
    app1 = create_app(make_settings(), lifespan=make_lifespan([AIMessage("restart answer one")]))
    async with app1.router.lifespan_context(app1):
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            body = await call_chat(client, chat, "remember across restart")
            assert "restart answer one" in body["choices"][0]["message"]["content"]

    # "Process 2": fresh app wiring; the same chat continues with history.
    app2 = create_app(make_settings(), lifespan=make_lifespan([AIMessage("restart answer two")]))
    async with app2.router.lifespan_context(app2):
        transport = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            body = await call_chat(client, chat, "and a second turn")
            assert "restart answer two" in body["choices"][0]["message"]["content"]

            state = await app2.state.agent.aget_state(
                {"configurable": {"thread_id": f"owui:e2e-user:{chat}"}}
            )
            contents = [str(m.content) for m in state.values.get("messages", [])]
            assert "remember across restart" in contents
            assert "restart answer one" in contents
            assert "and a second turn" in contents
            assert "restart answer two" in contents
