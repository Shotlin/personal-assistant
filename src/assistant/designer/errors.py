"""Designer API errors.

Distinct from the OpenAI-style ``/v1`` envelope: the Designer SPA receives
a stable ``{"error": {"code", "message"}}`` body so the frontend can
classify failures without parsing prose.
"""

from __future__ import annotations

from fastapi import HTTPException

DesignerErrorCode = str  # stable machine code; values in STATUS_BY_CODE keys

#: Stable code -> HTTP status. The frontend branches on the code.
STATUS_BY_CODE: dict[str, int] = {
    "session_required": 401,      # no/invalid/expired Designer session
    "invalid_credentials": 401,   # upstream rejected the credential
    "unsupported_auth_mode": 400, # mode outside {local_password, api_key}
    "csrf_failure": 403,          # CSRF token missing/mismatched
    "permission_denied": 403,     # authenticated but not permitted (audited)
    "invalid_request": 400,       # malformed payload
    "missing": 404,
    "conflict": 409,              # stale write / compare-and-swap failure
    "credential_error": 400,      # credential lifecycle failure
    "rate_limited": 429,
    "upstream_unavailable": 502,  # Open WebUI unreachable/invalid response
    "activation_failed": 400,     # preparation/validation failure during activation
    "revocation_failed": 400,     # agent has no active revision to revoke
}


class DesignerError(Exception):
    """Designer API failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        if code not in STATUS_BY_CODE:
            raise ValueError(f"unknown designer error code: {code!r}")
        self.code = code
        self.message = message
        super().__init__(message)

    @property
    def status_code(self) -> int:
        return STATUS_BY_CODE[self.code]


def to_http_exception(exc: DesignerError) -> HTTPException:
    """Translate a DesignerError into the FastAPI error envelope."""
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": {"code": exc.code, "message": exc.message}},
    )
