"""Unit tests for the OpenAI-compatible schema and error contract (spec 16)."""

from typing import Any

import pytest
from pydantic import ValidationError

from assistant.api.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatMessage,
    GatewayError,
    ModelEntry,
    ModelListResponse,
)

MODEL_ID = "personal-assistant-v1"


def test_chat_completion_request_accepts_required_fields() -> None:
    request = ChatCompletionRequest(
        model=MODEL_ID, messages=[ChatMessage(role="user", content="hi")]
    )
    assert request.model == MODEL_ID
    assert request.stream is False
    assert request.messages[0].content == "hi"


def test_chat_completion_request_ignores_unknown_extensions() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "some_future_field": {"x": 1},
        }
    )
    assert request.temperature == 0.2


def test_chat_completion_request_rejects_missing_model() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate({"messages": [{"role": "user", "content": "hi"}]})


def test_chat_completion_request_rejects_missing_messages() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate({"model": MODEL_ID})


def test_model_list_response_matches_spec_shape() -> None:
    response = ModelListResponse(data=[ModelEntry(id=MODEL_ID)])
    payload = response.model_dump()
    assert payload == {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}],
    }


def test_gateway_error_codes_map_to_expected_statuses() -> None:
    expected = {
        "invalid_api_key": 401,
        "unsupported_model": 404,
        "missing_chat_identity": 400,
        "provider_unavailable": 503,
        "provider_timeout": 504,
        "cua_unavailable": 503,
        "agent_execution_failed": 500,
    }
    for code, status in expected.items():
        error = GatewayError(code, "boom")  # type: ignore[arg-type]
        assert error.status_code == status
        detail = error.detail
        assert isinstance(detail, dict)
        body = detail["error"]
        assert body["code"] == code
        assert body["type"] == "assistant_gateway_error"


def test_chat_completion_chunk_serializes_like_openai_sse() -> None:
    chunk = ChatCompletionChunk(
        id="chatcmpl-1",
        created=123,
        model=MODEL_ID,
        choices=[{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}],
    )
    payload: dict[str, Any] = chunk.model_dump()
    assert payload["object"] == "chat.completion.chunk"
    assert payload["choices"][0]["delta"]["content"] == "hello"
