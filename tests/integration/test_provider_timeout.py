"""Provider failure mapping tests (Task 8): timeout and unavailable errors.

The openai SDK (v2, used by ChatOpenAI) speaks its own native HTTP stack,
so network-level interception with respx is not meaningful here. What the
gateway must guarantee is the error MAPPING: stable OpenAI-style codes
(spec sections 16.7 and 23). The mapping is tested directly, and the
gateway routing is tested with stub utility models raising the real
provider exception types.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2  # vendored by openai for its exception types
import openai
import pytest

from assistant.api.chat_route import _provider_error
from assistant.api.identity import DEV_CHAT_ID_HEADER, DEV_USER_ID_HEADER, TASK_HEADER
from assistant.main import create_app
from assistant.settings import Settings

GATEWAY_KEY = "prov-gateway-key"
MODEL_ID = "personal-assistant-v1"


def make_settings() -> Settings:
    return Settings(
        agent_gateway_api_key=GATEWAY_KEY,
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
        designer_enabled=False,  # hermetic legacy path
        model_timeout_seconds=1,
    )


def utility_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GATEWAY_KEY}",
        DEV_USER_ID_HEADER: "prov-user",
        DEV_CHAT_ID_HEADER: "prov-chat",
        TASK_HEADER: "title_generation",
    }


class _RaisingModel:
    """Utility-model stub that raises the given exception on ainvoke."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        raise self._exc


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "http://provider.test/v1/chat/completions")


@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_status"),
    [
        (openai.APITimeoutError(_request()), "provider_timeout", 504),
        (openai.APIConnectionError(request=_request()), "provider_unavailable", 503),
        (
            openai.APIStatusError(
                "boom", response=httpx2.Response(500, request=_request()), body=None
            ),
            "provider_unavailable",
            503,
        ),
        (httpx.TimeoutException("timed out"), "provider_timeout", 504),
        (httpx.ConnectError("refused"), "provider_unavailable", 503),
        (RuntimeError("agent blew up"), "agent_execution_failed", 500),
    ],
)
def test_provider_error_mapping(exc: Exception, expected_code: str, expected_status: int) -> None:
    error = _provider_error(exc)
    assert error.code == expected_code  # type: ignore[comparison-overlap]
    assert error.status_code == expected_status
    assert error.message  # human-readable


def make_lifespan(exc: Exception):  # noqa: ANN202
    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        app.state.utility_model = _RaisingModel(exc)
        yield

    return lifespan


@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_status"),
    [
        (openai.APITimeoutError(_request()), "provider_timeout", 504),
        (openai.APIConnectionError(request=_request()), "provider_unavailable", 503),
    ],
)
async def test_gateway_maps_provider_failures(
    require_postgres: None, exc: Exception, expected_code: str, expected_status: int
) -> None:
    app = create_app(make_settings(), lifespan=make_lifespan(exc))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            response = await client.post(
                "/v1/chat/completions",
                headers=utility_headers(),
                json={
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": "title this"}],
                },
            )
    assert response.status_code == expected_status
    assert response.json()["detail"]["error"]["code"] == expected_code
