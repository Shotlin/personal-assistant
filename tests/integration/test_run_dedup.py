"""Duplicate-delivery protection through the real gateway (WP4 / F10).

Same X-OpenWebUI-User-Message-Id + same content delivered twice must not
execute the agent twice: the first claims, the second observes. Same text
with a new message id is a new intentional command and executes again.
No driver; the scripted model counts invocations.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from assistant.api.identity import CHAT_ID_HEADER, USER_ID_HEADER, USER_MESSAGE_ID_HEADER
from tests.helpers.fake_driver import FakeDriver
from tests.helpers.gateway_app import build_test_app


@pytest.fixture
async def gateway(require_postgres: None) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    app = build_test_app(desktop_driver=FakeDriver())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            yield client, app


def _headers(message_id: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-key",
        USER_ID_HEADER: "dup-user",
        CHAT_ID_HEADER: f"chat-{uuid.uuid4().hex[:8]}",
        USER_MESSAGE_ID_HEADER: message_id,
    }


def _payload() -> dict[str, Any]:
    return {
        "model": "personal-assistant-v1",
        "messages": [{"role": "user", "content": "count me once"}],
        "stream": False,
    }


def _model_invocations(app: Any) -> int:
    """Count agent model invocations via the scripted model's call_index."""
    return int(getattr(app.state.scripted_model, "call_index", 0))


async def test_duplicate_message_id_executes_once(
    gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = gateway
    message_id = f"om-{uuid.uuid4().hex[:12]}"

    first = await client.post(
        "/v1/chat/completions", headers=_headers(message_id), json=_payload()
    )
    assert first.status_code == 200
    calls_after_first = _model_invocations(app)

    second = await client.post(
        "/v1/chat/completions", headers=_headers(message_id), json=_payload()
    )
    assert second.status_code == 200
    calls_after_second = _model_invocations(app)

    # The duplicate must observe the first run, not re-execute the agent:
    # model invocation count must not increase on the duplicate delivery.
    assert calls_after_second == calls_after_first, (
        f"duplicate delivery re-executed the run: {calls_after_first} -> {calls_after_second}"
    )


async def test_new_message_id_runs_again(gateway: tuple[httpx.AsyncClient, Any]) -> None:
    client, _ = gateway
    a = await client.post(
        "/v1/chat/completions", headers=_headers(f"om-{uuid.uuid4().hex[:12]}"), json=_payload()
    )
    b = await client.post(
        "/v1/chat/completions", headers=_headers(f"om-{uuid.uuid4().hex[:12]}"), json=_payload()
    )
    assert a.status_code == 200 and b.status_code == 200
    # both runs executed (scripted model answers both times)
    assert a.json()["choices"][0]["message"]["content"] == "scripted answer"
    assert b.json()["choices"][0]["message"]["content"] == "scripted answer"
