"""OpenRouter adapter using the first-party ChatOpenRouter integration.

Spec section 10.3: use the native integration rather than a
``ChatOpenAI(base_url=...)`` workaround so provider-specific behavior
(reasoning content, routing, structured output) is preserved.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openrouter import ChatOpenRouter
from openrouter.utils import BackoffStrategy, RetryConfig
from pydantic import SecretStr

from assistant.settings import Settings


def create_model(settings: Settings) -> BaseChatModel:
    """Build the configured OpenRouter chat model."""
    model = ChatOpenRouter(
        model_name=settings.model_name,
        openrouter_api_key=SecretStr(settings.openrouter_api_key),
        # request_timeout maps to the SDK timeout_ms: seconds -> milliseconds.
        request_timeout=settings.model_timeout_seconds * 1000,
        max_tokens=settings.model_max_tokens,
        # D11: retries inside the OpenRouter SDK are invisible to the
        # admission wrapper around a LangChain invocation.  Keep the chosen
        # provider, but disable SDK-owned retries so every admitted request
        # maps to exactly one outgoing HTTP attempt.  Controller recovery
        # owns any later retry as a fresh, durably admitted invocation.
        max_retries=0,
        # Keep GUI-automation steps fast and cheap: minimal thinking budget.
        reasoning=(
            {"effort": settings.model_reasoning_effort, "exclude": True}
            if settings.model_reasoning_effort not in {"", "default"}
            else None
        ),
    )
    # ``max_retries=0`` causes the integration to omit a retry config, which
    # lets the SDK retain its own default retry policy.  Install an explicit
    # no-retry policy instead.  This is intentionally startup-fatal if the
    # selected SDK cannot expose its client: silently falling back would make
    # an unmetered second HTTP attempt possible.
    try:
        model.client.sdk_configuration.retry_config = RetryConfig(
            strategy="none",
            backoff=BackoffStrategy(
                initial_interval=0, max_interval=0, exponent=1,
                max_elapsed_time=0, jitter_ms=0,
            ),
            retry_connection_errors=False,
        )
    except (AttributeError, TypeError) as exc:
        raise RuntimeError("OpenRouter SDK retry policy could not be disabled") from exc
    return model
