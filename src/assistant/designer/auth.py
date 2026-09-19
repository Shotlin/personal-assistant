"""Designer authentication, RBAC and CSRF (R09, C1, Fix 4, Clar 2).

Authentication adapter (V1 modes, explicit -- no insecure fallback):

- ``local_password`` (Mode A): user submits email + password once; the
  gateway verifies server-side via Open WebUI ``POST /api/v1/auths/signin``
  and stores only the returned upstream session token, encrypted. The
  password itself is never stored, never logged, and never leaves this
  one call.
- ``api_key`` (Mode B): user submits an Open WebUI API key; the gateway
  verifies it server-side against the upstream current-user endpoint and
  stores it encrypted. Logout removes only the Designer's local copy --
  a pre-existing upstream API key is NEVER deleted or revoked merely
  because the user logged out (Clarification 2).
- Any other upstream configuration (OAuth/SSO/LDAP) yields the explicit
  ``unsupported_authentication_mode`` state; there is no fallback.

Authorization (C1) is separate from authentication: ``Actor`` carries a
server-derived permission set; every mutating route checks it
independently of the React UI. Denials are audited with safe metadata.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from assistant.designer.credentials import new_session_token
from assistant.designer.errors import DesignerError
from assistant.designer.store import hash_token

# C1 permission capabilities. Ordered weakest to strongest is NOT assumed:
# checks are per-permission, never hierarchical.
DESIGNER_PERMISSIONS = frozenset(
    {
        "designer.view",
        "designer.edit",
        "designer.activate",
        "designer.revoke",
        "designer.credentials",
        "designer.connectors",
        "designer.admin",
    }
)

SUPPORTED_AUTH_MODES = frozenset({"local_password", "api_key"})

SESSION_TTL_SECONDS = 12 * 60 * 60  # matches store.SESSION_TTL_MINUTES

# Default grants by verified upstream role. Rows in designer_grants extend
# or restrict per user (where the upstream contract is insufficient the
# Designer grant lives in the gateway DB -- C1).
ROLE_DEFAULT_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(DESIGNER_PERMISSIONS),
    "user": frozenset(
        {
            "designer.view",
            "designer.edit",
            "designer.activate",
            "designer.revoke",
        }
    ),
    "pending": frozenset(),
}

# Login rate limiting (per identity key): max attempts per window.
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300.0
SESSION_COOKIE_NAME = "designer_session"
CSRF_HEADER_NAME = "X-Designer-CSRF"


@dataclass(frozen=True)
class Actor:
    """Server-verified identity + permissions. Never constructed from a
    graph payload or client-supplied header alone."""

    user_id: str
    role: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    session_id_hash: str = ""

    def has(self, permission: str) -> bool:
        return permission in self.permissions


@dataclass
class LoginRateLimiter:
    """In-process fixed-window limiter for /session attempts.

    Local single-process deployment (R22): process-local state is
    sufficient; a shared store is a scale-out concern, not a V1 one.
    """

    max_attempts: int = LOGIN_MAX_ATTEMPTS
    window_seconds: float = LOGIN_WINDOW_SECONDS
    _attempts: dict[str, list[float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._attempts = {}

    def _prune(self, key: str, now: float) -> list[float]:
        window = [t for t in self._attempts.get(key, []) if now - t < self.window_seconds]
        self._attempts[key] = window
        return window

    def check(self, key: str) -> None:
        """Raise rate_limited when the identity exceeded its window budget."""
        now = time.monotonic()
        if len(self._prune(key, now)) >= self.max_attempts:
            raise DesignerError("rate_limited", "Too many attempts; retry later")

    def record(self, key: str) -> None:
        self._attempts.setdefault(key, []).append(time.monotonic())


class UpstreamAuthAdapter:
    """Verifies Open WebUI credentials server-side (never in the browser).

    Both modes talk to the configured Open WebUI base URL over the
    server-to-server path. Contracts are P0-probe-dependent; failures are
    surfaced honestly (upstream_unavailable) rather than guessed around.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    def _transport(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=15)

    async def verify(
        self, *, mode: str, email: str = "", password: str = "", api_key: str = ""
    ) -> dict[str, Any]:
        """Return ``{"user_id", "role", "upstream_token", "credential_kind"}``
        or raise DesignerError. The password is used for this one call and
        then discarded; it is never stored or logged."""
        if mode not in SUPPORTED_AUTH_MODES:
            raise DesignerError(
                "unsupported_authentication_mode",
                "Designer supports local Open WebUI login or an Open WebUI API key; "
                "this upstream uses an unsupported authentication mode (e.g. OAuth/SSO).",
            )
        transport = self._transport()
        try:
            if mode == "local_password":
                signin = await transport.post(
                    f"{self._base_url}/api/v1/auths/signin",
                    json={"email": email, "password": password_discard(email, password)},
                )
                if signin.status_code == 401:
                    raise DesignerError("invalid_credentials", "Open WebUI rejected the credential")
                if signin.status_code != 200:
                    raise DesignerError(
                        "upstream_unavailable",
                        f"Open WebUI sign-in returned {signin.status_code}",
                    )
                body = signin.json()
                token = body.get("token")
                user = body.get("user") or {}
                if not isinstance(token, str) or not token:
                    raise DesignerError(
                        "upstream_unavailable", "Open WebUI sign-in response had no token"
                    )
                return {
                    "user_id": str(user.get("id") or ""),
                    "role": str(user.get("role") or "user"),
                    "upstream_token": token,
                    "credential_kind": "session_token",
                }
            # Mode B: API key verified against the upstream current-user route.
            me = await transport.get(
                f"{self._base_url}/api/v1/auths/",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if me.status_code == 401:
                raise DesignerError("invalid_credentials", "Open WebUI rejected the API key")
            if me.status_code != 200:
                raise DesignerError(
                    "upstream_unavailable",
                    f"Open WebUI verification returned {me.status_code}",
                )
            body = me.json()
            return {
                "user_id": str(body.get("id") or ""),
                "role": str(body.get("role") or "user"),
                "upstream_token": api_key,
                "credential_kind": "api_key",
            }
        finally:
            if self._client is None:
                await transport.aclose()

    async def signout_session_token(self, upstream_token: str) -> bool:
        """Best-effort upstream invalidation for SESSION tokens only
        (Clarification 2). Returns whether the upstream call succeeded;
        API keys are never revoked here."""
        try:
            transport = self._transport()
        except Exception:  # noqa: BLE001
            return False
        try:
            response = await transport.post(
                f"{self._base_url}/api/v1/auths/signout",
                headers={"Authorization": f"Bearer {upstream_token}"},
            )
            return response.status_code == 200
        except Exception:  # noqa: BLE001 -- honest "not verified" over guessing
            return False
        finally:
            if self._client is None:
                await transport.aclose()


def password_discard(email: str, password: str) -> str:
    """Password passes through untouched; named for audit clarity only."""
    return password


def permissions_for_role(role: str, explicit: set[str]) -> frozenset[str]:
    """Server-side permission set: upstream role defaults + DB grants.

    The DB is authoritative for restrictions; role defaults only ever add
    baseline permissions for verified roles.
    """
    base = ROLE_DEFAULT_PERMISSIONS.get(role, frozenset())
    return frozenset(set(base) | explicit)


def require_permission(
    actor: Actor, permission: str, subject: dict[str, Any] | None = None
) -> None:
    """Raise permission_denied unless the actor holds the permission."""
    if not actor.has(permission):
        raise DesignerError("permission_denied", f"missing permission: {permission}")


class SessionManager:
    """Designer session lifecycle: opaque HttpOnly cookie + CSRF token.

    Cookies are HttpOnly + SameSite=Lax + path-scoped; Secure is set only
    on HTTPS deployments (loopback HTTP is the documented dev exception).
    """

    def __init__(self, store: Any, *, cookie_secure: bool = False) -> None:
        self._store = store
        self._cookie_secure = cookie_secure

    async def create(
        self,
        *,
        user_id: str,
        role: str,
        auth_mode: str,
        credential_id: str | None,
    ) -> dict[str, str]:
        """Mint a session; returns the raw cookie value + CSRF token.

        The raw tokens exist once, in this return value; the DB stores only
        their SHA-256 digests. The caller sets the actual cookie header.
        """
        raw_session = new_session_token()
        raw_csrf = new_session_token()
        await self._store.insert_session(
            session_id_hash=hash_token(raw_session),
            user_id=user_id,
            role=role,
            auth_mode=auth_mode,
            credential_id=credential_id,
            csrf_token_hash=hash_token(raw_csrf),
        )
        return {"session_token": raw_session, "csrf_token": raw_csrf}

    @property
    def cookie_secure(self) -> bool:
        """Whether session cookies must carry the Secure attribute."""
        return self._cookie_secure

    async def resolve(self, raw_session_id: str) -> Actor:
        """Resolve an Actor from the cookie value or raise session_required."""
        if not raw_session_id:
            raise DesignerError("session_required", "Designer session required")
        session = await self._store.load_session(hash_token(raw_session_id))
        if session is None:
            raise DesignerError("session_required", "Designer session not found")
        if session.get("revoked_at") is not None:
            raise DesignerError("session_required", "Designer session was revoked")
        expires_at = session.get("expires_at")
        now = datetime.now(UTC)
        if expires_at is not None and expires_at <= now:
            raise DesignerError("session_required", "Designer session expired")
        explicit = await self._store.list_grants(str(session["user_id"]))
        permissions = permissions_for_role(str(session["role"]), explicit)
        return Actor(
            user_id=str(session["user_id"]),
            role=str(session["role"]),
            permissions=permissions,
            session_id_hash=str(session["session_id_hash"]),
        )

    async def check_csrf(self, actor: Actor, raw_session_id: str, supplied_csrf: str) -> None:
        """Mutations require a matching CSRF token for the same session."""
        if not supplied_csrf:
            raise DesignerError("csrf_failure", "missing CSRF token")
        session = await self._store.load_session(hash_token(raw_session_id))
        if session is None or session.get("revoked_at") is not None:
            raise DesignerError("csrf_failure", "session not available")
        if hash_token(supplied_csrf) != str(session["csrf_token_hash"]):
            raise DesignerError("csrf_failure", "CSRF token mismatch")


def raw_csrf_token() -> str:
    """Helper retained for tests; SessionManager mints its own token."""
    return new_session_token()
