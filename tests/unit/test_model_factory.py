"""Unit tests for provider selection with model constructors mocked.

No network calls and no inference spend: adapter modules have their model
classes replaced with recording stubs (Phase 1 spec, Task 2).
"""

from typing import Any, ClassVar

import pytest
from pydantic import SecretStr

from assistant.models import build_chat_model, openai_compatible, openai_provider, openrouter
from assistant.models.base import ModelProviderError
from assistant.settings import Settings


class RecordingStub:
    """Minimal stand-in for a chat model that records constructor kwargs."""

    last_kwargs: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs


class StubOpenRouter(RecordingStub):
    pass


class StubOpenAICompatible(RecordingStub):
    pass


class StubOpenAI(RecordingStub):
    pass


def make_settings(provider: str, **extra: Any) -> Settings:
    return Settings(
        model_provider=provider,
        model_name="test-model-x",
        model_timeout_seconds=30,
        model_max_retries=1,
        model_api_key="generic-key",
        model_base_url="http://127.0.0.1:9999/v1",
        openrouter_api_key="or-key",
        openai_api_key="oa-key",
        cua_enabled=False,
        **extra,
    )


def test_default_provider_is_openrouter() -> None:
    settings = Settings(cua_enabled=False, openrouter_api_key="or-key")
    assert settings.model_provider == "openrouter"


def test_openrouter_adapter_uses_native_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openrouter, "ChatOpenRouter", StubOpenRouter)
    model = build_chat_model(make_settings("openrouter"))

    assert isinstance(model, StubOpenRouter)
    kwargs = StubOpenRouter.last_kwargs
    assert kwargs["model_name"] == "test-model-x"
    assert kwargs["openrouter_api_key"] == SecretStr("or-key")
    # request_timeout is SDK timeout_ms: MODEL_TIMEOUT_SECONDS=30 -> 30_000 ms.
    assert kwargs["request_timeout"] == 30_000
    assert kwargs["max_retries"] == 1


def test_generic_compatible_adapter_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_compatible, "ChatOpenAI", StubOpenAICompatible)
    model = build_chat_model(make_settings("generic_openai_compatible"))

    assert isinstance(model, StubOpenAICompatible)
    kwargs = StubOpenAICompatible.last_kwargs
    assert kwargs["model"] == "test-model-x"
    assert kwargs["api_key"] == SecretStr("generic-key")
    assert kwargs["base_url"] == "http://127.0.0.1:9999/v1"
    assert kwargs["timeout"] == 30
    assert kwargs["max_retries"] == 1
    # A model name must not route the client to /v1/responses (spec 10.4).
    assert kwargs["use_responses_api"] is False


def test_openai_adapter_present_but_not_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_provider, "ChatOpenAI", StubOpenAI)
    model = build_chat_model(make_settings("openai"))

    assert isinstance(model, StubOpenAI)
    kwargs = StubOpenAI.last_kwargs
    assert kwargs["model"] == "test-model-x"
    assert kwargs["api_key"] == SecretStr("oa-key")
    assert make_settings("openai").model_provider != "openrouter"


def test_provider_switch_changes_model_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openrouter, "ChatOpenRouter", StubOpenRouter)
    monkeypatch.setattr(openai_compatible, "ChatOpenAI", StubOpenAICompatible)

    first = build_chat_model(make_settings("openrouter"))
    second = build_chat_model(make_settings("generic_openai_compatible"))

    assert type(first) is not type(second)


def test_unknown_provider_fails_with_clear_error() -> None:
    # model_construct bypasses Settings validation so the factory's own
    # rejection path is exercised (defense in depth, spec 10.2).
    settings = Settings.model_construct(model_provider="bogus_provider")
    with pytest.raises(ModelProviderError, match="Unsupported MODEL_PROVIDER 'bogus_provider'"):
        build_chat_model(settings)
