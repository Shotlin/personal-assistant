"""Designer persistence over PostgreSQL (raw psycopg, repo style).

All queries are parameterized and owner-scoped. A pooled connection is
acquired per operation (no overlapping transaction contexts on one global
connection). Tables are created by the checksummed migrations in
``migrations/designer/`` -- never by this module.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

SESSION_TTL_MINUTES = 12 * 60  # 12 h designer session lifetime


def hash_token(token: str) -> str:
    """Session/CSRF tokens are stored only as SHA-256 digests."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


class DesignerStore:
    """All Designer SQL lives here so routes stay thin and testable."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> DesignerStore:
        from psycopg_pool import AsyncConnectionPool as _Pool

        pool = _Pool(
            database_url,
            min_size=1,
            max_size=8,
            open=True,
        )
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    def connection(self) -> Any:
        return self._pool.connection()

    # --- audit (audit.py writes through here) ---

    async def record_audit(
        self, *, actor_user_id: str, event: str, subject: dict[str, Any]
    ) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO designer_audit_events (actor_user_id, event, subject) "
                "VALUES (%s, %s, %s)",
                (actor_user_id, event, Jsonb(subject)),
            )

    # --- credentials ---

    async def insert_credential(
        self,
        *,
        owner_user_id: str,
        purpose: str,
        kind: str,
        key_id: str,
        label: str,
        credential_id: str,
        nonce: bytes,
        ciphertext: bytes,
    ) -> dict[str, Any]:
        """Insert a complete credential row (material included) so no
        intermediate state with missing encrypted material ever exists."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO designer_credentials "
                "(credential_id, owner_user_id, purpose, kind, key_id, label, "
                " nonce, ciphertext) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING generation",
                (
                    uuid.UUID(credential_id), owner_user_id, purpose, kind,
                    key_id, label, nonce, ciphertext,
                ),
            )
            record = await cursor.fetchone()
        return {"credential_id": credential_id, "generation": int(record[0])}

    async def load_credential(self, credential_id: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT credential_id, owner_user_id, purpose, kind, nonce, ciphertext, "
                "generation, scope, status FROM designer_credentials "
                "WHERE credential_id = %s",
                (uuid.UUID(credential_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        keys = (
            "credential_id", "owner_user_id", "purpose", "kind",
            "nonce", "ciphertext", "generation", "scope", "status",
        )
        return dict(zip(keys, row, strict=True))

    async def rotate_credential(
        self, *, credential_id: str, nonce: bytes, ciphertext: bytes, new_generation: int
    ) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE designer_credentials SET nonce = %s, ciphertext = %s, "
                "generation = %s, rotated_at = now() WHERE credential_id = %s",
                (nonce, ciphertext, new_generation, uuid.UUID(credential_id)),
            )

    async def revoke_credential(self, credential_id: str) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE designer_credentials SET status = 'revoked', revoked_at = now() "
                "WHERE credential_id = %s",
                (uuid.UUID(credential_id),),
            )

    # --- sessions ---

    async def insert_session(
        self,
        *,
        session_id_hash: str,
        user_id: str,
        role: str,
        auth_mode: str,
        credential_id: str | None,
        csrf_token_hash: str,
        ttl_minutes: int = SESSION_TTL_MINUTES,
    ) -> datetime:
        expires_at = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO designer_sessions "
                "(session_id_hash, user_id, role, auth_mode, credential_id, "
                " csrf_token_hash, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    session_id_hash, user_id, role, auth_mode,
                    uuid.UUID(credential_id) if credential_id else None,
                    csrf_token_hash, expires_at,
                ),
            )
        return expires_at

    async def load_session(self, session_id_hash: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT session_id_hash, user_id, role, auth_mode, credential_id, "
                "csrf_token_hash, created_at, expires_at, revoked_at "
                "FROM designer_sessions WHERE session_id_hash = %s",
                (session_id_hash,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        keys = (
            "session_id_hash", "user_id", "role", "auth_mode", "credential_id",
            "csrf_token_hash", "created_at", "expires_at", "revoked_at",
        )
        return dict(zip(keys, row, strict=True))

    async def revoke_session(self, session_id_hash: str) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE designer_sessions SET revoked_at = now() "
                "WHERE session_id_hash = %s AND revoked_at IS NULL",
                (session_id_hash,),
            )

    # --- grants (C1) ---

    async def upsert_grant(self, *, user_id: str, permission: str, granted_by: str) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO designer_grants (user_id, permission, granted_by) "
                "VALUES (%s, %s, %s) ON CONFLICT (user_id, permission) DO NOTHING",
                (user_id, permission, granted_by),
            )

    async def list_grants(self, user_id: str) -> set[str]:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT permission FROM designer_grants WHERE user_id = %s", (user_id,)
            )
            rows = await cursor.fetchall()
        return {str(row[0]) for row in rows}

    async def delete_grant(self, *, user_id: str, permission: str) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "DELETE FROM designer_grants WHERE user_id = %s AND permission = %s",
                (user_id, permission),
            )
