"""Provider-independent model contract (spec section 10.1).

The Deep Agent must never know which vendor serves the model; everything
downstream consumes a ``BaseChatModel`` produced by :func:`build_chat_model`.
Provider selection lives exclusively in the factory.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from assistant.settings import Settings


class ModelProviderError(RuntimeError):
    """Raised when the configured model provider cannot be built."""


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Return the configured tool-capable chat model.

    Delegates to the factory, which is the only module allowed to choose a
    provider (spec section 10.2).
    """
    from assistant.models.factory import build_chat_model as _build

    return _build(settings)
