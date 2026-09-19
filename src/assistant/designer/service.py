"""Designer service assembly (flag-on only, Safety note 1 + C2).

``build_designer_state`` runs exclusively when ``DESIGNER_ENABLED=true``:
it applies Designer migrations, opens the Designer store/credential
service, and assembles the auth adapter + session manager + rate limiter
into ``app.state.designer``. With the flag off none of this executes —
the attribute never exists and routes are never mounted.
"""

from __future__ import annotations

import logging
from typing import Any

from assistant.designer.adapters.openwebui import OpenWebUIClient
from assistant.designer.auth import (
    LoginRateLimiter,
    SessionManager,
    UpstreamAuthAdapter,
)
from assistant.designer.credentials import CredentialKeyMissing, CredentialStore, load_key
from assistant.designer.errors import DesignerError
from assistant.designer.store import DesignerStore
from assistant.settings import Settings

logger = logging.getLogger("assistant.designer")

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "designer"

# Key id stored alongside credentials so key rotation can be detected.
_DEFAULT_KEY_ID = "designer-credentials-1"


async def build_designer_state(settings: Settings, app: Any) -> dict[str, Any]:
    """Initialize Designer services. Validates Designer security settings
    BEFORE mounting anything (Safety note 1)."""
    # 1. Validate security-critical settings first (fail-fast, flag-on only).
    if not settings.designer_credentials_key:
        raise CredentialKeyMissing(
            "DESIGNER_ENABLED=true requires DESIGNER_CREDENTIALS_KEY"
        )
    key_material = load_key(settings.designer_credentials_key)

    # 2. Apply Designer migrations (flag-on startup is an authorized moment).
    from scripts.migrate_designer import apply_migrations

    applied = await apply_migrations(settings.database_url, MIGRATIONS_DIR)
    if applied:
        logger.info(
            "designer_migrations_applied",
            extra={"event": "designer_migrations_applied", "count": len(applied)},
        )

    # 3. Open the store and services.
    store = await DesignerStore.connect(settings.database_url)
    credential_store = CredentialStore(store, key_material, _DEFAULT_KEY_ID)
    sessions = SessionManager(store, cookie_secure=settings.designer_cookie_secure)
    adapter = UpstreamAuthAdapter(settings.designer_openwebui_base_url)
    limiter = LoginRateLimiter(
        max_attempts=settings.designer_login_max_attempts,
    )

    # 4. Source adapters (P3): Open WebUI client per actor (credential
    # resolved server-side), Knowledge BLOCKED until the probe verifies
    # the retrieval contract (Fix 2).
    from assistant.designer.adapters.knowledge import UnverifiedKnowledgeSource
    from assistant.designer.adapters.sources import SourceService

    async def client_for_actor(actor: Any) -> Any:
        session = await store.load_session(actor.session_id_hash)
        credential_id = (
            str(session["credential_id"])
            if session and session.get("credential_id")
            else ""
        )
        if not credential_id:
            raise DesignerError("session_required", "session has no upstream credential")
        ref = await store.load_credential(credential_id)
        if ref is None or str(ref["status"]) != "active":
            raise DesignerError("session_required", "upstream credential unavailable")
        from assistant.designer.credentials import CredentialRef

        plaintext = await credential_store.resolve_plaintext(
            actor_user_id=actor.user_id,
            ref=CredentialRef(
                credential_id=credential_id,
                generation=int(ref["generation"]),
                purpose=str(ref["purpose"]),
            ),
        )
        return OpenWebUIClient(settings.designer_openwebui_base_url, plaintext)

    sources = SourceService(client_for_actor, UnverifiedKnowledgeSource())
    from assistant.designer.events import EventHub

    return {
        "store": store,
        "credentials": credential_store,
        "sessions": sessions,
        "upstream": adapter,
        "limiter": limiter,
        "sources": sources,
        "migrations_applied": applied,
        "event_hub": EventHub(),
    }
