"""SSO Mode C tests: Open WebUI session verified server-side (R09, C1).

The Designer is reachable inside the Open WebUI origin through the
same-origin proxy. When a request arrives through that proxy (shared
secret header) carrying an Open WebUI session cookie, the gateway
verifies the token against the upstream current-user endpoint and
resolves the actor — no second login. Without the proxy secret, the
standalone flow is unchanged.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from tests.designer.conftest import (
    TEST_KEY,
    designer_settings,
)


def proxy_settings(**overrides: Any) -> Any:
    return designer_settings(designer_proxy_key="proxy-secret-key", **overrides)


class FakeUpstream:
    """Accepts exactly one session token; records verification calls."""

    def __init__(self) -> None:
        self.verified_tokens: list[str] = []

    async def verify_session_token(self, upstream_token: str) -> dict[str, Any] | None:
        self.verified_tokens.append("redacted")
        if upstream_token == "valid-owui-token":
            return {
                "user_id": "owui-user-1",
                "role": "user",
                "credential_kind": "session_token",
            }
        return None


def build_sso_app(upstream: FakeUpstream, designer_db: Any) -> Any:
    from fastapi import FastAPI

    from assistant.designer.auth import (
        LoginRateLimiter,
        SessionManager,
    )
    from assistant.designer.credentials import CredentialStore
    from assistant.designer.routes import install_error_handler
    from assistant.designer.routes import router as designer_router

    app = FastAPI()
    app.include_router(designer_router)
    install_error_handler(app)
    app.state.settings = proxy_settings()
    app.state.designer = {
        "store": designer_db,
        "credentials": CredentialStore(designer_db, base64.b64decode(TEST_KEY), "test-key-1"),
        "sessions": SessionManager(designer_db),
        "upstream": upstream,
        "limiter": LoginRateLimiter(),
        "cookie_secure": False,
    }
    return app


def client_for(
    app: Any,
    owui_token: str | None = None,
    proxy_key: str | None = "proxy-secret-key",
) -> httpx.AsyncClient:
    headers = {}
    if proxy_key is not None:
        headers["X-Designer-Proxy-Key"] = proxy_key
    cookies = {}
    if owui_token is not None:
        cookies["token"] = owui_token
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport, base_url="http://designer", headers=headers, cookies=cookies
    )


async def test_sso_resolves_actor_from_verified_owui_session(
    designer_db: Any,
) -> None:
    upstream = FakeUpstream()
    app = build_sso_app(upstream, designer_db)
    async with client_for(app, owui_token="valid-owui-token") as client:
        response = await client.get("/designer/api/v1/session")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == "owui-user-1"
    assert "designer.view" in body["permissions"]
    assert len(upstream.verified_tokens) == 1


async def test_sso_requires_proxy_secret(designer_db: Any) -> None:
    """Direct gateway access (no proxy) never uses SSO: the standalone
    login flow is unchanged."""
    upstream = FakeUpstream()
    app = build_sso_app(upstream, designer_db)
    async with client_for(app, owui_token="valid-owui-token", proxy_key=None) as client:
        response = await client.get("/designer/api/v1/session")
    assert response.status_code == 401
    assert upstream.verified_tokens == []


async def test_sso_rejects_wrong_proxy_secret(designer_db: Any) -> None:
    upstream = FakeUpstream()
    app = build_sso_app(upstream, designer_db)
    async with client_for(app, owui_token="valid-owui-token", proxy_key="forged") as client:
        response = await client.get("/designer/api/v1/session")
    assert response.status_code == 401
    assert upstream.verified_tokens == []


async def test_sso_rejects_invalid_owui_token(designer_db: Any) -> None:
    upstream = FakeUpstream()
    app = build_sso_app(upstream, designer_db)
    async with client_for(app, owui_token="expired-token") as client:
        response = await client.get("/designer/api/v1/session")
    assert response.status_code == 401


async def test_sso_actor_can_mutate_without_designer_csrf(
    designer_db: Any,
) -> None:
    """SSO actors carry the Open WebUI session cookie (SameSite=Lax), so
    the designer CSRF token does not apply; standalone sessions keep it."""
    upstream = FakeUpstream()
    app = build_sso_app(upstream, designer_db)
    async with client_for(app, owui_token="valid-owui-token") as client:
        response = await client.get("/designer/api/v1/session")
        assert response.status_code == 200
        # A mutating route under SSO proceeds without X-Designer-CSRF
        # (logout of an SSO actor without a designer session revokes
        # nothing but must not 403 on CSRF).
        logout = await client.delete("/designer/api/v1/session")
    assert logout.status_code in (200, 400), logout.text


async def test_sso_disabled_without_proxy_key_setting(designer_db: Any) -> None:
    """Empty designer_proxy_key disables SSO entirely (rollback)."""
    upstream = FakeUpstream()
    app = build_sso_app(upstream, designer_db)
    app.state.settings = designer_settings(designer_proxy_key="")
    async with client_for(app, owui_token="valid-owui-token") as client:
        response = await client.get("/designer/api/v1/session")
    assert response.status_code == 401
    assert upstream.verified_tokens == []
