"""OpenRouter adapter using the first-party ChatOpenRouter integration.

Spec section 10.3: use the native integration rather than a
``ChatOpenAI(base_url=...)`` workaround so provider-specific behavior
(reasoning content, routing, structured output) is preserved.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openrouter import ChatOpenRouter
from pydantic import SecretStr

from assistant.settings import Settings


def create_model(settings: Settings) -> BaseChatModel:
    """Build the configured OpenRouter chat model."""
    return ChatOpenRouter(
        model_name=settings.model_name,
        openrouter_api_key=SecretStr(settings.openrouter_api_key),
        # request_timeout maps to the SDK timeout_ms: seconds -> milliseconds.
        request_timeout=settings.model_timeout_seconds * 1000,
        max_retries=settings.model_max_retries,
    )
