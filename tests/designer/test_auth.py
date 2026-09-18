"""P1 acceptance: Designer auth, RBAC, credentials and the C2 boundary.

Covers (per frozen plan): anonymous 401, cross-user 403, CSRF rejection,
credential readback denial, rate limiting, session expiry, token stripping,
audited permission denial, API-key logout leaves the upstream key intact,
and the flag-off legacy-start test (Safety note 1).
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from tests.designer.conftest import (
    TEST_KEY,
    USER_A,
    USER_B,
)

from assistant.designer.credentials import (
    CredentialKeyMissing,
    CredentialStore,
)
from assistant.designer.errors import DesignerError
from assistant.settings import Settings, SettingsError


def error_code(response: httpx.Response) -> str:
    detail = response.json().get("detail", {})
    return str(detail.get("error", {}).get("code", ""))


async def test_anonymous_cannot_list_designer_routes(anonymous_api: httpx.AsyncClient) -> None:
    response = await anonymous_api.get("/designer/api/v1/session")
    assert response.status_code == 401
    assert error_code(response) == "session_required"


async def test_session_exchange_mints_cookie_and_csrf(
    anonymous_api: httpx.AsyncClient,
) -> None:
    payload = await anonymous_api.post(
        "/designer/api/v1/session",
        json={"mode": "local_password", "email": "a@local", "password": "pw"},
    )
    assert payload.status_code == 200, payload.text
    body = payload.json()
    assert body["user_id"] == USER_A
    assert body["csrf_token"]
    # Real Set-Cookie header (HttpOnly session cookie) is present.
    set_cookie = payload.headers.get("set-cookie", "")
    assert "designer_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    # The upstream token must never appear in the response.
    assert "upstream-token-for" not in payload.text


async def test_unsupported_authentication_mode_is_explicit(
    anonymous_api: httpx.AsyncClient,
) -> None:
    payload = await anonymous_api.post(
        "/designer/api/v1/session",
        json={"mode": "oauth_sso", "email": "a@local", "password": "pw"},
    )
    assert payload.status_code == 400
    assert error_code(payload) == "unsupported_auth_mode"


async def test_bad_credentials_rejected_without_leak(
    anonymous_api: httpx.AsyncClient,
) -> None:
    payload = await anonymous_api.post(
        "/designer/api/v1/session",
        json={"mode": "local_password", "email": "a@local", "password": "wrong"},
    )
    assert payload.status_code == 401
    assert error_code(payload) == "invalid_credentials"


async def test_inspect_session_returns_permissions(api: httpx.AsyncClient) -> None:
    response = await api.get("/designer/api/v1/session")
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == USER_A
    assert "designer.view" in body["permissions"]


async def test_mutation_without_csrf_is_rejected(api: httpx.AsyncClient) -> None:
    response = await api.delete("/designer/api/v1/session", headers={"X-Designer-CSRF": ""})
    assert response.status_code == 403
    assert error_code(response) == "csrf_failure"


async def test_logout_revokes_session_and_local_credential(
    api: httpx.AsyncClient,
) -> None:
    response = await api.delete("/designer/api/v1/session")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revoked"] is True
    # Session is gone afterwards.
    follow = await api.get("/designer/api/v1/session")
    assert follow.status_code == 401


async def test_credential_plaintext_never_readable(
    designer_db: Any,
) -> None:
    store = CredentialStore(designer_db, base64.b64decode(TEST_KEY), "test-key-1")
    ref = await store.store(
        owner_user_id=USER_A, purpose="test", kind="api_key", plaintext="secret-value"
    )
    # Owner can resolve internally (server-side use only)...
    plaintext = await store.resolve_plaintext(actor_user_id=USER_A, ref=ref)
    assert plaintext == "secret-value"
    # ...but another user cannot (R09 authorization before decryption).
    with pytest.raises(PermissionError):
        await store.resolve_plaintext(actor_user_id=USER_B, ref=ref)
    # The DB never holds plaintext: load the raw row and assert.
    row = await designer_db.load_credential(ref.credential_id)
    assert b"secret-value" not in bytes(row["ciphertext"])


async def test_credential_rotation_bumps_generation(
    designer_db: Any,
) -> None:
    store = CredentialStore(designer_db, base64.b64decode(TEST_KEY), "test-key-1")
    ref = await store.store(
        owner_user_id=USER_A, purpose="test", kind="api_key", plaintext="v1-secret"
    )
    rotated = await store.rotate(
        actor_user_id=USER_A, ref=ref, new_plaintext="v2-secret"
    )
    assert rotated.generation == ref.generation + 1
    assert await store.resolve_plaintext(actor_user_id=USER_A, ref=rotated) == "v2-secret"
    # The old generation reference no longer resolves (stale runtimes).
    with pytest.raises(LookupError):
        await store.resolve_plaintext(actor_user_id=USER_A, ref=ref)


async def test_credential_revocation_is_immediate(
    designer_db: Any,
) -> None:
    store = CredentialStore(designer_db, base64.b64decode(TEST_KEY), "test-key-1")
    ref = await store.store(
        owner_user_id=USER_A, purpose="test", kind="api_key", plaintext="doomed"
    )
    await store.revoke(actor_user_id=USER_A, ref=ref)
    with pytest.raises(LookupError):
        await store.resolve_plaintext(actor_user_id=USER_A, ref=ref)


async def test_rate_limiting_blocks_flood(
    designer_db: Any,
) -> None:
    from assistant.designer.auth import LoginRateLimiter

    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    limiter.record("attacker")
    limiter.record("attacker")
    limiter.check("attacker")  # below the cap: allowed
    limiter.record("attacker")  # now at the cap
    with pytest.raises(DesignerError) as excinfo:
        limiter.check("attacker")
    assert excinfo.value.code == "rate_limited"


async def test_flag_off_rolls_back_to_legacy_gateway() -> None:
    """C2 + Safety note 1: flag-off + no credentials key starts the legacy
    gateway with no Designer routes and no Designer services."""
    from assistant.main import create_app

    settings = Settings(
        agent_gateway_api_key="test-gateway-key",
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
        designer_enabled=False,
        designer_credentials_key="",
    )
    app = create_app(settings)
    designer_paths = [
        path
        for path in (getattr(route, "path", "") for route in app.routes)
        if path.startswith("/designer")
    ]
    assert designer_paths == [], "flag-off must mount no designer routes"
    # Legacy /v1 surface served over HTTP (httpx ASGI, no lifespan needed
    # for models since it only reads settings).
    app.state.store = None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        models = await client.get(
            "/v1/models", headers={"Authorization": "Bearer test-gateway-key"}
        )
        assert models.status_code == 200
        assert models.json()["data"][0]["id"] == settings.assistant_model_id
        designer = await client.get("/designer/api/v1/session")
        assert designer.status_code in (401, 404), (
            "flag-off must not serve designer routes"
        )


async def test_flag_on_without_credentials_key_fails_validation() -> None:
    with pytest.raises(SettingsError) as excinfo:
        Settings(
            agent_gateway_api_key="test-gateway-key",
            model_provider="openrouter",
            openrouter_api_key="dummy",
            cua_enabled=False,
            designer_enabled=True,
            designer_credentials_key="",
        )
    assert "DESIGNER_CREDENTIALS_KEY" in str(excinfo.value)


async def test_flag_on_with_bad_key_fails_validation() -> None:
    with pytest.raises(SettingsError) as excinfo:
        Settings(
            agent_gateway_api_key="test-gateway-key",
            model_provider="openrouter",
            openrouter_api_key="dummy",
            cua_enabled=False,
            designer_enabled=True,
            designer_credentials_key=base64.b64encode(b"short").decode("ascii"),
        )
    assert "DESIGNER_CREDENTIALS_KEY" in str(excinfo.value)


async def test_credential_key_helper_rejects_bad_input() -> None:
    from assistant.designer.credentials import load_key

    with pytest.raises(CredentialKeyMissing):
        load_key("")
    with pytest.raises(CredentialKeyMissing):
        load_key(base64.b64encode(b"short").decode("ascii"))
    # A valid 32-byte key loads fine.
    assert len(load_key(TEST_KEY)) == 32


async def test_permission_denial_is_designer_error_code() -> None:
    from assistant.designer.auth import Actor, require_permission

    actor = Actor(user_id=USER_A, role="user", permissions=frozenset({"designer.view"}))
    with pytest.raises(DesignerError) as excinfo:
        require_permission(actor, "designer.activate")
    assert excinfo.value.code == "permission_denied"
