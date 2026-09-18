"""Hard cap on the newest observation (master plan 6.3 / audit F03).

The trim middleware must bound even the LATEST observation: a model that
requests a huge accessibility tree must never receive an unbounded
payload. Measured in model-visible characters (proxy for input tokens).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from assistant.agent.observation_trim import (
    NEWEST_HARD_CAP_CHARS,
    ObservationTrimMiddleware,
)


class _Req:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = messages

    def override(self, *, messages: list[Any]) -> _Req:
        return _Req(messages)


def _messages(big_text: str) -> list[Any]:
    return [
        HumanMessage("check the window"),
        ToolMessage(content=big_text, tool_call_id="t1", name="get_window_state"),
    ]


async def test_newest_observation_is_hard_capped() -> None:
    big = "x" * 40_000
    request = _Req(_messages(big))
    captured: dict[str, Any] = {}

    async def handler(req: Any) -> Any:
        captured["request"] = req
        return "ok"

    middleware = ObservationTrimMiddleware()
    await middleware.awrap_model_call(request, handler)

    observed = captured["request"].messages[1].content
    assert len(str(observed)) < NEWEST_HARD_CAP_CHARS + 200
    assert "[observation truncated" in str(observed)
    # window identity head must survive truncation
    assert str(observed).startswith("x")


async def test_small_newest_observation_passes_through_unchanged() -> None:
    request = _Req(_messages("Display is 42"))
    captured: dict[str, Any] = {}

    async def handler(req: Any) -> Any:
        captured["request"] = req
        return "ok"

    await ObservationTrimMiddleware().awrap_model_call(request, handler)
    assert captured["request"].messages[1].content == "Display is 42"


async def test_older_observations_collapse_and_newest_is_capped() -> None:
    old_big = "o" * 5_000
    new_big = "n" * 40_000
    messages = [
        HumanMessage("hi"),
        ToolMessage(content=old_big, tool_call_id="t1", name="get_window_state"),
        AIMessage(content="observing"),
        ToolMessage(content=new_big, tool_call_id="t2", name="get_window_state"),
    ]
    captured: dict[str, Any] = {}

    async def handler(req: Any) -> Any:
        captured["request"] = req
        return "ok"

    await ObservationTrimMiddleware().awrap_model_call(_Req(messages), handler)
    trimmed = captured["request"].messages
    assert "[Earlier observation removed" in str(trimmed[1].content)
    assert len(str(trimmed[3].content)) < NEWEST_HARD_CAP_CHARS + 200
