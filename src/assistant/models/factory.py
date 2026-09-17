"""Model provider factory -- the only module allowed to choose a provider.

Supported values (spec section 10.2): ``openrouter``, ``openai``,
``generic_openai_compatible``. Unknown values fail with a clear
configuration error; Settings already rejects them at startup.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel

from assistant.models.base import ModelProviderError
from assistant.models.openai_compatible import create_model as _create_openai_compatible_model
from assistant.models.openai_provider import create_model as _create_openai_model
from assistant.models.openrouter import create_model as _create_openrouter_model
from assistant.settings import Settings

_ADAPTERS: dict[str, Callable[[Settings], BaseChatModel]] = {
    "openrouter": _create_openrouter_model,
    "openai": _create_openai_model,
    "generic_openai_compatible": _create_openai_compatible_model,
}


def supported_providers() -> frozenset[str]:
    """Return the provider keys this factory can build."""
    return frozenset(_ADAPTERS)


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Build the chat model for ``settings.model_provider``."""
    adapter = _ADAPTERS.get(settings.model_provider)
    if adapter is None:
        msg = (
            f"Unsupported MODEL_PROVIDER {settings.model_provider!r}; "
            f"expected one of {sorted(_ADAPTERS)}"
        )
        raise ModelProviderError(msg)
    return adapter(settings)
