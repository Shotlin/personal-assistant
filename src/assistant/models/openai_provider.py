"""Native OpenAI adapter (spec section 10.5).

Present but optional: enabled later purely through configuration
(``MODEL_PROVIDER=openai`` + ``OPENAI_API_KEY``) without any changes to
Open WebUI, memory, CUA, or Deep Agent code. Not the Phase 1 default.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from assistant.settings import Settings


def create_model(settings: Settings) -> BaseChatModel:
    """Build the native OpenAI chat model."""
    return ChatOpenAI(
        model=settings.model_name,
        api_key=SecretStr(settings.openai_api_key),
        timeout=settings.model_timeout_seconds,
        max_retries=settings.model_max_retries,
    )
