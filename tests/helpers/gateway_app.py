"""Shared gateway test app builders (no driver, no model spend)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.messages import AIMessage

from assistant.agent.build import build_agent
from assistant.main import create_app
from assistant.memory.postgres import open_memory_resources
from assistant.runtime.session import DesktopSessionConfig, DesktopSessionManager
from assistant.settings import Settings
from tests.helpers.scripted_model import ScriptedChatModel

POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
SKILLS_ROOT = (
    __import__("pathlib").Path(__file__).resolve().parents[2] / "src" / "assistant" / "skills"
)


def build_test_app(
    *,
    desktop_driver: Any | None = None,
    model_responses: list[Any] | None = None,
) -> Any:
    """Full app with scripted model and an optional fake desktop driver."""
    driver: Any = desktop_driver

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        async with open_memory_resources(POSTGRES_URL) as mem:
            model = ScriptedChatModel(
                responses=model_responses or [AIMessage("scripted answer")]
            )
            bundle = build_agent(
                model=model,
                checkpointer=mem.saver,
                store=mem.store,
                skills_root=SKILLS_ROOT,
            )
            app.state.agent = bundle.agent
            app.state.scripted_model = model  # invocation counter for tests
            app.state.store = mem.store
            app.state.saver = mem.saver
            app.state.utility_model = ScriptedChatModel(responses=[AIMessage("Concise Title")])
            app.state.desktop_sessions = DesktopSessionManager(
                driver, config=DesktopSessionConfig(), enabled=desktop_driver is not None
            )
            from assistant.runtime.runs import RunStore

            run_store = await RunStore.connect(POSTGRES_URL)
            await run_store.setup()
            app.state.run_store = run_store
            try:
                yield
            finally:
                await run_store.close()

    settings = Settings(
        agent_gateway_api_key="test-gateway-key",
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
    )
    return create_app(settings, lifespan=lifespan)
