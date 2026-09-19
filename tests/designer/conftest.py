"""P1 fixtures: authenticated Designer API clients over a real database.

Fixtures follow the implementation-plan contract (tests/designer section):

- ``designer_db``      -- DesignerStore over the compose Postgres.
- ``designer_app``     -- FastAPI app with Designer routes mounted and a
                          fake upstream auth endpoint (no real Open WebUI).
- ``api`` / ``api_b``  -- authenticated httpx.AsyncClients for two users.
- ``anonymous_api``    -- same app, no session cookie.
- ``operator_api``     -- admin-role client for connector administration.
- ``valid_graph``      -- minimal activatable graph (formalized in P2).
- ``upstream_denied``  -- configures the fake upstream to reject source
                          access (used from P3 onward).
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from assistant.designer.auth import (
    LoginRateLimiter,
    SessionManager,
    UpstreamAuthAdapter,
)
from assistant.designer.credentials import CredentialStore
from assistant.designer.store import DesignerStore
from assistant.settings import Settings

POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "designer"

TEST_KEY = base64.b64encode(os.urandom(32)).decode("ascii")

USER_A = "user-a-designer"
USER_B = "user-b-designer"


def designer_settings(**overrides: Any) -> Settings:
    return Settings(
        agent_gateway_api_key="test-gateway-key",
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
        designer_enabled=True,
        designer_credentials_key=TEST_KEY,
        **overrides,
    )


@pytest.fixture
async def designer_db() -> AsyncIterator[DesignerStore]:
    from scripts.migrate_designer import apply_migrations

    await apply_migrations(POSTGRES_URL, MIGRATIONS_DIR)
    store = await DesignerStore.connect(POSTGRES_URL)
    yield store
    await store.close()


class FakeUpstream(UpstreamAuthAdapter):
    """Records verification calls; never contacts a real Open WebUI."""

    def __init__(self) -> None:
        super().__init__("http://upstream.test")
        self.verify_calls: list[dict[str, Any]] = []
        self.signout_calls: list[str] = []
        self.deny = False
        self._users = {
            "a@local": {"user_id": USER_A, "role": "user"},
            "b@local": {"user_id": USER_B, "role": "user"},
            "admin@local": {"user_id": "user-admin-designer", "role": "admin"},
        }

    async def verify(
        self, *, mode: str, email: str = "", password: str = "", api_key: str = ""
    ) -> dict[str, Any]:
        self.verify_calls.append(
            {"mode": mode, "email": email, "password_present": bool(password),
             "api_key_present": bool(api_key)}
        )
        from assistant.designer.errors import DesignerError

        if mode not in ("local_password", "api_key"):
            raise DesignerError("unsupported_auth_mode", "mode not supported")
        if self.deny:
            raise DesignerError("upstream_unavailable", "upstream denied")
        user = self._users.get(email)
        # Any password other than the fixture value is rejected, mirroring
        # the real upstream behavior for wrong credentials.
        if user is None or password != "pw":
            raise DesignerError("invalid_credentials", "rejected")
        return {
            **user,
            "upstream_token": f"upstream-token-for-{user['user_id']}",
            "credential_kind": "api_key" if mode == "api_key" else "session_token",
        }

    async def signout_session_token(self, upstream_token: str) -> bool:
        self.signout_calls.append("redacted")
        return True


def build_designer_app(
    designer_db: DesignerStore, *, upstream: FakeUpstream | None = None
) -> Any:
    """App with Designer routes mounted, Designer state pre-assembled."""

    from assistant.designer.routes import router as designer_router

    upstream = upstream or FakeUpstream()
    app = FastAPI()
    app.include_router(designer_router)
    from assistant.designer.routes import install_error_handler

    install_error_handler(app)
    # Designer state assembled directly (equivalent to the flag-on lifespan).
    app.state.settings = designer_settings()
    from assistant.designer.events import EventHub

    app.state.designer = {
        "store": designer_db,
        "credentials": CredentialStore(designer_db, base64.b64decode(TEST_KEY), "test-key-1"),
        "sessions": SessionManager(designer_db),
        "upstream": upstream,
        "limiter": LoginRateLimiter(),
        "cookie_secure": False,
        "event_hub": EventHub(),
    }
    return app


async def _login(client: httpx.AsyncClient, email: str, password: str = "pw") -> dict[str, Any]:
    response = await client.post(
        "/designer/api/v1/session",
        json={"mode": "local_password", "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def api(designer_db: DesignerStore) -> AsyncIterator[httpx.AsyncClient]:
    app = build_designer_app(designer_db)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://designer") as client:
        payload = await _login(client, "a@local")
        client.headers["X-Designer-CSRF"] = payload["csrf_token"]
        yield client


@pytest.fixture
async def api_b(designer_db: DesignerStore) -> AsyncIterator[httpx.AsyncClient]:
    app = build_designer_app(designer_db)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://designer") as client:
        payload = await _login(client, "b@local")
        client.headers["X-Designer-CSRF"] = payload["csrf_token"]
        yield client


@pytest.fixture
async def anonymous_api(designer_db: DesignerStore) -> AsyncIterator[httpx.AsyncClient]:
    app = build_designer_app(designer_db)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://designer") as client:
        yield client


@pytest.fixture
def valid_graph() -> dict[str, Any]:
    """Minimal graph; full semantics arrive with P2 (schemas/validation)."""
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "agent-root", "type": "agent", "position": {"x": 0, "y": 0},
             "data": {"enabled": True}},
            {"id": "model-1", "type": "model", "position": {"x": 300, "y": 0},
             "data": {"enabled": True}},
        ],
        "edges": [
            {"id": "e1", "source": "agent-root", "target": "model-1",
             "sourceHandle": "model", "targetHandle": "in"},
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
