"""Provider-independent model layer.

Public contract: :func:`assistant.models.build_chat_model`.
"""

from assistant.models.base import ModelProviderError, build_chat_model

__all__ = ["ModelProviderError", "build_chat_model"]
