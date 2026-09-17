"""Live provider smoke (spec Task 2 acceptance, gated by RUN_LIVE_MODEL=1).

One real, cheap, tool-capable model call: bind a trivial tool and assert
the model actually emits a tool call. Skipped unless opted in:

    RUN_LIVE_MODEL=1 uv run pytest tests/integration/test_live_model.py

Requires OPENROUTER_API_KEY (or the configured provider key) in .env.
"""

import os

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from assistant.models import build_chat_model
from assistant.settings import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_MODEL") != "1",
    reason="live model test; set RUN_LIVE_MODEL=1 with a real provider key in .env",
)


class WeatherArgs(BaseModel):
    city: str = Field(description="City name")


@tool("get_weather", args_schema=WeatherArgs)
def get_weather(city: str) -> str:
    """Return the weather for a city (stub)."""
    return f"sunny in {city}"


async def test_configured_model_performs_tool_call() -> None:
    settings = Settings()  # real .env configuration
    model = build_chat_model(settings)
    bound = model.bind_tools([get_weather])
    result = await bound.ainvoke(
        [HumanMessage("What is the weather in Tokyo? Use the get_weather tool.")]
    )
    tool_calls = getattr(result, "tool_calls", None) or []
    assert tool_calls, f"model returned no tool calls: {result!r}"
    assert any(call.get("name") == "get_weather" for call in tool_calls)
