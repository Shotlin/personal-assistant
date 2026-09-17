"""OpenAI-compatible API schemas and the stable error contract (spec section 16)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

ERROR_TYPE = "assistant_gateway_error"

GatewayErrorCode = Literal[
    "invalid_api_key",
    "unsupported_model",
    "missing_chat_identity",
    "provider_unavailable",
    "provider_timeout",
    "cua_unavailable",
    "agent_execution_failed",
]

_STATUS_BY_CODE: dict[str, int] = {
    "invalid_api_key": 401,
    "unsupported_model": 404,
    "missing_chat_identity": 400,
    "provider_unavailable": 503,
    "provider_timeout": 504,
    "cua_unavailable": 503,
    "agent_execution_failed": 500,
}


class GatewayError(HTTPException):
    """OpenAI-style error carrying a stable machine code (spec 16.7)."""

    def __init__(self, code: GatewayErrorCode, message: str) -> None:
        self.code: GatewayErrorCode = code
        self.message = message
        super().__init__(
            status_code=_STATUS_BY_CODE[code],
            detail={"error": {"message": message, "type": ERROR_TYPE, "param": None, "code": code}},
        )


class ErrorResponse(BaseModel):
    """Envelope shape of every gateway error (spec 16.7)."""

    message: str
    type: str = ERROR_TYPE
    param: str | None = None
    code: GatewayErrorCode


class ErrorEnvelope(BaseModel):
    error: ErrorResponse


class ModelEntry(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str = "local"


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelEntry]


class ChatMessage(BaseModel):
    """One message of an OpenAI-compatible conversation."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    """Subset of the OpenAI Chat Completions request we accept (spec 16.3)."""

    model_config = ConfigDict(extra="ignore")

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    user: str | None = None


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Literal["stop", "length", "error"] | None = "stop"


class UsageStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: UsageStats = Field(default_factory=UsageStats)


class DeltaMessage(BaseModel):
    role: Literal["assistant"] | None = None
    content: str | None = None


class ChatCompletionChunkChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Literal["stop", "length", "error"] | None = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]
