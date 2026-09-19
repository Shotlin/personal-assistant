"""Automated acceptance-scenario coverage that needs no live model (spec 22).

Scenario A -- knowledge chat with no computer use, thread persists.
Scenario B -- personal memory recalled in a new chat after a restart,
without copying the previous transcript.
Scenarios C/D/E are live (tests/e2e/test_cua_calculator.py); Scenario F is
covered by tests/unit/test_model_factory.py (provider swap with zero
agent/API changes).
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage

from assistant.agent.build import build_agent
from assistant.agent.context import AgentContext, RunBudget
from assistant.api.identity import DEV_CHAT_ID_HEADER, DEV_USER_ID_HEADER
from assistant.main import create_app
from assistant.memory.namespaces import thread_id_for, user_memory_namespace
from assistant.memory.postgres import open_memory_resources
from assistant.settings import Settings
from assistant.tools.policy import cua_run_budget
from tests.helpers.scripted_model import ScriptedChatModel

POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
SKILLS_ROOT = Path(__file__).resolve().parents[2] / "src" / "assistant" / "skills"
GATEWAY_KEY = "scenario-gateway-key"
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
        # These scenarios script the GENERAL AGENT; the compact planner
        # (default on) would consume their responses as plan attempts.
        compact_planner_enabled=False,
    )


async def test_scenario_a_knowledge_chat_without_cua(require_postgres: None) -> None:
    answer = AIMessage(
        "A reverse proxy sits in front of servers and forwards client requests to them."
    )
    app = create_app(make_settings(), lifespan=make_lifespan([answer]))
    async with app.router.lifespan_context(app):
        budget = RunBudget()
        cua_run_budget.set(budget)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            chat = f"chat-{uuid.uuid4().hex[:8]}"
            response = await client.post(
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GATEWAY_KEY}",
                    DEV_USER_ID_HEADER: "scenario-user",
                    DEV_CHAT_ID_HEADER: chat,
                },
                json={
                    "model": MODEL_ID,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Explain what a reverse proxy is in simple language.",
                        }
                    ],
                },
            )
        assert response.status_code == 200
        answer = response.json()["choices"][0]["message"]["content"]
        assert "reverse proxy" in answer.lower()

        # No computer use: zero mutating actions were consumed.
        assert budget.used == 0

        # The thread persisted.
        state = await app.state.agent.aget_state(
            {"configurable": {"thread_id": f"owui:scenario-user:{chat}"}}
        )
        contents = [str(m.content) for m in state.values.get("messages", [])]
        assert any("reverse proxy" in c.lower() for c in contents)


async def test_scenario_b_memory_across_restart_and_chats(require_postgres: None) -> None:
    user_id = f"b-user-{uuid.uuid4().hex[:8]}"
    preference = (
        "When you prepare prompts for my coding tools, "
        "keep them concise but technically complete."
    )

    # Chat 1: the user states the preference; the agent saves it.
    app1 = create_app(
        make_settings(),
        lifespan=make_lifespan(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {
                                "file_path": "/memories/preferences.md",
                                "content": preference,
                            },
                            "id": "b1",
                        }
                    ],
                ),
                AIMessage("Got it: concise but technically complete coding prompts."),
            ]
        ),
    )
    async with app1.router.lifespan_context(app1):
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GATEWAY_KEY}",
                    DEV_USER_ID_HEADER: user_id,
                    DEV_CHAT_ID_HEADER: f"chat-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": preference}],
                },
            )
        assert response.status_code == 200
        items = await app1.state.store.asearch(user_memory_namespace(user_id))
        assert any("preferences" in i.key for i in items)

    # "Backend restart": a fresh application wiring.
    app2 = create_app(
        make_settings(),
        lifespan=make_lifespan([AIMessage("Prepared a concise coding prompt.")]),
    )
    async with app2.router.lifespan_context(app2):
        model3 = ScriptedChatModel(responses=[AIMessage("done")])
        bundle3 = build_agent(
            model=model3,
            checkpointer=app2.state.saver,
            store=app2.state.store,
            skills_root=SKILLS_ROOT,
        )
        chat2 = f"chat-{uuid.uuid4().hex[:8]}"
        await bundle3.agent.ainvoke(
            {"messages": [HumanMessage("Prepare a coding prompt for a broken checkout button.")]},
            {"configurable": {"thread_id": thread_id_for(user_id, chat2)}},
            context=AgentContext(user_id=user_id, chat_id=chat2),
        )
        from assistant.api.turns import message_text

        seen_text = " ".join(message_text(m) for m in model3.seen[-1])
        # The preference is recalled for the same user...
        assert "concise but technically complete" in seen_text

        # ...and the new chat's thread does not contain the previous transcript.
        state = await bundle3.agent.aget_state(
            {"configurable": {"thread_id": thread_id_for(user_id, chat2)}}
        )
        contents = [str(m.content) for m in state.values.get("messages", [])]
        assert not any("Prepare a coding prompt for my coding tools" in c for c in contents)
        assert len([c for c in contents if "technically complete" in c]) <= 1


def test_scenario_f_provider_swap_leaves_gateway_untouched() -> None:
    # The gateway contract depends only on Settings + factory: swapping the
    # provider is proven not to require agent/API changes by the factory
    # unit tests; this marker documents the mapping to spec Scenario F.
    from assistant.models.factory import supported_providers

    assert supported_providers() == frozenset(
        {"openrouter", "openai", "generic_openai_compatible"}
    )
