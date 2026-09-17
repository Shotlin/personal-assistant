"""Generic OpenAI-compatible adapter (spec section 10.4).

Uses ``ChatOpenAI`` only for providers that implement the OpenAI Chat
Completions schema closely enough. ``use_responses_api=False`` prevents a
model name from accidentally routing requests to ``/v1/responses`` when a
third-party service only implements Chat Completions.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from assistant.settings import Settings


def create_model(settings: Settings) -> BaseChatModel:
    """Build the generic OpenAI-compatible chat model."""
    return ChatOpenAI(
        model=settings.model_name,
        api_key=SecretStr(settings.model_api_key),
        base_url=settings.model_base_url,
        timeout=settings.model_timeout_seconds,
        max_tokens=settings.model_max_tokens,
        max_retries=settings.model_max_retries,
        use_responses_api=False,
    )
