"""Bearer-token authentication for the agent gateway (spec section 16.1).

The structured OpenAI-style error envelope is applied by the API layer in
Task 6; this module provides parsing, constant-time comparison, and the
FastAPI dependency used by protected routes.
"""

from __future__ import annotations

import secrets
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status

from assistant.settings import Settings

WWW_AUTHENTICATE_HEADER = "WWW-Authenticate"


class BearerToken:
    """A parsed bearer token from an Authorization header."""

    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token


def parse_bearer_token(authorization: str | None) -> BearerToken | None:
    """Parse an ``Authorization: Bearer <token>`` header.

    Returns ``None`` when the header is missing, uses another scheme,
    or carries an empty token.
    """
    if not authorization:
        return None
    scheme, _, rest = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = rest.strip()
    if not token:
        return None
    return BearerToken(token)


def tokens_match(expected: str, provided: str | None) -> bool:
    """Constant-time comparison of a provided key against the expected key."""
    if not provided or not expected:
        return False
    return secrets.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def get_settings(request: Request) -> Settings:
    """FastAPI dependency exposing the validated application settings."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise RuntimeError("Application settings are not attached to app.state")
    return cast(Settings, settings)


SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_gateway_key(
    request: Request,
    settings: SettingsDep,
) -> None:
    """Reject requests without the exact gateway API key (HTTP 401)."""
    token = parse_bearer_token(request.headers.get("Authorization"))
    if token is None or not tokens_match(settings.agent_gateway_api_key, token.token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid or missing API key",
                    "type": "assistant_gateway_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
            headers={WWW_AUTHENTICATE_HEADER: "Bearer"},
        )
