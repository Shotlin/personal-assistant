"""Local stop endpoint and gateway-level desktop lifecycle (WP3).

Pure chat through the real app must not create a driver session; a stop
request against an unknown run is a 404, not a crash; the endpoint requires
the gateway key. Fakes only -- no driver, no model spend.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from assistant.api.identity import CHAT_ID_HEADER, USER_ID_HEADER
from assistant.runtime.session import DesktopSessionManager
from tests.helpers.fake_driver import FakeDriver
from tests.helpers.gateway_app import build_test_app

GATEWAY_KEY = "test-gateway-key"


@pytest.fixture
async def desktop_gateway(require_postgres: None) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    app = build_test_app(desktop_driver=FakeDriver())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            yield client, app


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-key",
        USER_ID_HEADER: "gwuser",
        CHAT_ID_HEADER: f"chat-{uuid.uuid4().hex[:8]}",
    }


async def test_stop_unknown_run_returns_404(
    desktop_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _ = desktop_gateway
    response = await client.post(
        "/v1/runs/does-not-exist/stop", headers={"Authorization": "Bearer test-gateway-key"}
    )
    assert response.status_code == 404


async def test_stop_requires_gateway_key(
    desktop_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _ = desktop_gateway
    response = await client.post("/v1/runs/whatever/stop")
    assert response.status_code == 401


async def test_stop_marks_active_run_cancelled(
    desktop_gateway: tuple[httpx.AsyncClient, Any],
) -> None:
    client, app = desktop_gateway
    manager: DesktopSessionManager = app.state.desktop_sessions
    async with manager.open("live-run") as run:
        response = await client.post(
            "/v1/runs/live-run/stop", headers={"Authorization": "Bearer test-gateway-key"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "cancelling"
        assert run.cancelled is True
