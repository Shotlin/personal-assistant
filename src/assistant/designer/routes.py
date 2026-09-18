"""/designer/api/v1 session routes (P1).

Authentication surface only. Mounted exclusively when
``DESIGNER_ENABLED=true`` (C2): the flag-off gateway never serves these
routes, never validates Designer-only settings, never starts Designer
services (Safety note 1).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from assistant.designer import audit
from assistant.designer.auth import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    Actor,
    LoginRateLimiter,
    SessionManager,
    UpstreamAuthAdapter,
    permissions_for_role,
)
from assistant.designer.credentials import CredentialRef, CredentialStore
from assistant.designer.errors import DesignerError

router = APIRouter(prefix="/designer/api/v1")


def install_error_handler(app: FastAPI) -> None:
    """Translate DesignerError into the stable Designer error envelope."""

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(DesignerError)
    async def _designer_error_handler(request: Request, exc: DesignerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"error": {"code": exc.code, "message": exc.message}}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": {"error": {"code": "invalid_request",
                                           "message": "malformed request payload"}}},
        )


def _designer_state(request: Request) -> dict[str, Any]:
    designer = getattr(request.app.state, "designer", None)
    if designer is None:
        raise DesignerError("session_required", "Designer is not enabled on this gateway")
    return designer


async def resolve_actor(request: Request) -> Actor:
    """Resolve the Designer Actor from the session cookie (server-verified)."""
    designer = _designer_state(request)
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    return await designer["sessions"].resolve(raw_cookie)


async def require_actor_and_csrf(request: Request) -> Actor:
    """Dependency for every mutating route: valid session + CSRF match."""
    actor = await resolve_actor(request)
    designer = _designer_state(request)
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    await designer["sessions"].check_csrf(
        actor, raw_cookie, request.headers.get(CSRF_HEADER_NAME, "")
    )
    return actor


class SessionConnectRequest(BaseModel):
    """One-time upstream credential exchange (R09). The plaintext secret
    is accepted once, verified server-side, stored encrypted, and never
    returned nor persisted in plaintext."""

    model_config = {"extra": "ignore"}

    mode: str
    email: str = ""
    password: str = ""
    api_key: str = ""


@router.post("/session")
async def connect(request: Request, body: SessionConnectRequest) -> Any:
    designer = _designer_state(request)

    # Rate limiting keys off the presented identity, not its secret.
    identity_key = body.email or f"key:{hash(body.api_key) & 0xFFFFFFFF:08x}"
    limiter: LoginRateLimiter = designer["limiter"]
    limiter.check(identity_key)
    limiter.record(identity_key)

    adapter: UpstreamAuthAdapter = designer["upstream"]
    verified = await adapter.verify(
        mode=body.mode, email=body.email, password=body.password, api_key=body.api_key
    )
    if not verified.get("user_id"):
        raise DesignerError("upstream_unavailable", "Open WebUI did not verify identity")

    credential_store: CredentialStore = designer["credentials"]
    ref = await credential_store.store(
        owner_user_id=str(verified["user_id"]),
        purpose="openwebui-designer-session",
        kind=str(verified["credential_kind"]),
        plaintext=str(verified.pop("upstream_token")),
    )
    explicit = await designer["store"].list_grants(str(verified["user_id"]))
    permissions = permissions_for_role(str(verified["role"]), explicit)
    sessions: SessionManager = designer["sessions"]
    tokens = await sessions.create(
        user_id=str(verified["user_id"]),
        role=str(verified["role"]),
        auth_mode=body.mode,
        credential_id=ref.credential_id,
    )
    await audit.record(
        designer["store"],
        actor_user_id=str(verified["user_id"]),
        event="session.created",
        subject={"auth_mode": body.mode, "credential_id": ref.credential_id},
    )
    # The upstream token and the submitted password never appear in any
    # response; only the session cookie (real Set-Cookie) + CSRF token.
    response = JSONResponse(
        status_code=200,
        content={
            "user_id": str(verified["user_id"]),
            "role": str(verified["role"]),
            "permissions": sorted(permissions),
            "csrf_token": tokens["csrf_token"],
            "session_ttl_seconds": SESSION_TTL_SECONDS,
        },
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=tokens["session_token"],
        httponly=True,
        samesite="lax",
        secure=designer["sessions"].cookie_secure,
        path="/",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


@router.get("/session")
async def inspect_session(request: Request) -> dict[str, Any]:
    actor = await resolve_actor(request)
    return {
        "user_id": actor.user_id,
        "role": actor.role,
        "permissions": sorted(actor.permissions),
    }


@router.delete("/session")
async def disconnect(request: Request) -> Any:
    """Revoke the Designer session and delete the local encrypted upstream
    credential reference (Clarification 2).

    - session token: best-effort verified upstream signout first.
    - api_key / unverified kinds: local removal only; the upstream key is
      NEVER touched by logout.
    """
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    # Logout is a mutation: CSRF applies here too.
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    await designer["sessions"].check_csrf(
        actor, raw_cookie, request.headers.get(CSRF_HEADER_NAME, "")
    )
    session = await designer["store"].load_session(actor.session_id_hash)

    upstream_revoked = False
    credential_kind = "none"
    credential_id = (
        str(session["credential_id"])
        if session and session.get("credential_id")
        else ""
    )
    if credential_id:
        credential_store: CredentialStore = designer["credentials"]
        row = await designer["store"].load_credential(credential_id)
        if row is not None and str(row["status"]) == "active":
            credential_kind = str(row["kind"])
            ref = CredentialRef(
                credential_id=credential_id,
                generation=int(row["generation"]),
                purpose=str(row["purpose"]),
            )
            if credential_kind == "session_token":
                try:
                    plaintext = await credential_store.resolve_plaintext(
                        actor_user_id=actor.user_id, ref=ref
                    )
                    adapter: UpstreamAuthAdapter = designer["upstream"]
                    upstream_revoked = await adapter.signout_session_token(plaintext)
                except (LookupError, PermissionError):
                    upstream_revoked = False
            # api_key + unknown kinds fall through: local invalidation only.
            await credential_store.revoke(actor_user_id=actor.user_id, ref=ref)

    await designer["store"].revoke_session(actor.session_id_hash)
    await audit.record(
        designer["store"],
        actor_user_id=actor.user_id,
        event="session.revoked",
        subject={"upstream_revoked": upstream_revoked, "kind": credential_kind},
    )
    response = JSONResponse(
        status_code=200,
        content={
            "revoked": True,
            "upstream_revocation": (
                "revoked"
                if upstream_revoked
                else "not supported/not verified for this credential kind"
            ),
        },
    )
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
