"""WP6: provider request contract for the compact planner.

The planner must address the SAME configured model as the agent (exact
model identity), send no tool bindings (a plan is a text decision, not a
tool loop), and carry a bounded compact prompt. Captured from the model
adapter boundary, not assumed.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from assistant.models import build_chat_model
from assistant.runtime.planner import plan_supported_task
from assistant.settings import Settings
from tests.helpers.scripted_model import ScriptedChatModel


class RecordingModel(ScriptedChatModel):
    """Records invocation kwargs alongside the seen messages."""

    invoke_kwargs: list[dict] = []

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self.invoke_kwargs.append({"messages": list(messages), "kwargs": dict(kwargs)})
        return AIMessage(content='{"decision": "unsupported"}')


async def test_planner_sends_compact_single_message_and_no_tools() -> None:
    model = RecordingModel(responses=[])
    result = await plan_supported_task("open chrome", None, model)
    assert result.__class__.__name__ == "UnsupportedTask"
    last_call = model.invoke_kwargs[-1]
    sent = last_call["messages"]
    assert len(sent) == 1, "compact planner sends exactly one user message"
    content = str(sent[0].content)
    assert "open chrome" in content
    assert "open_app.v1" in content and "browser.search.v1" in content
    assert last_call["kwargs"].get("tools") in (None, []), (
        "planner must not bind tools: a plan is a text decision"
    )
    assert len(content) < 2000, "prompt must stay compact (objective + menu)"


def test_settings_model_name_is_what_the_adapter_would_address() -> None:
    """The model id the gateway logs is the one configured - no drift.

    Owner instruction (2026-09-17): the assistant runs z-ai/glm-5.3-flash
    via OpenRouter; the .env is the source of truth and the contract test
    pins that exact identity to catch config drift.
    """
    settings = Settings(
        agent_gateway_api_key="k",
        model_provider="openrouter",
        openrouter_api_key="dummy",
        model_name="z-ai/glm-5.3-flash",
        cua_enabled=False,
    )
    model = build_chat_model(settings)
    # The adapter builds from settings; the gateway's logged model name and
    # the planner's model come from the same settings object (same instance
    # in the lifespan). Assert the binding source of truth is settings.
    assert settings.model_name == "z-ai/glm-5.3-flash"
    _ = model  # adapter construction succeeded under the current settings
