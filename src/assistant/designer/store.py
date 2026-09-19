"""Designer persistence over PostgreSQL (raw psycopg, repo style).

All queries are parameterized and owner-scoped. A pooled connection is
acquired per operation (no overlapping transaction contexts on one global
connection). Tables are created by the checksummed migrations in
``migrations/designer/`` -- never by this module.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
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
    async def connect(cls, database_url: str) -> "DesignerStore":
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

    # --- agents & revisions (P2) ---
    # row_version is the optimistic-concurrency token (ETag, R12).

    async def insert_agent(
        self, *, owner_user_id: str, slug: str, display_name: str, description: str = ""
    ) -> dict[str, Any]:
        agent_id = uuid.uuid4()
        final_slug = slug
        async with self.connection() as conn:
            for _attempt in range(1, 6):
                try:
                    # A nested transaction per attempt: a slug collision
                    # rolls back only this INSERT, not the outer work.
                    async with conn.transaction():
                        await conn.execute(
                            "INSERT INTO designer_agents "
                            "(agent_id, owner_user_id, slug, display_name, description) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (agent_id, owner_user_id, final_slug, display_name, description),
                        )
                    break
                except psycopg.errors.UniqueViolation:
                    # Same-owner slug collision (e.g. two "Test Agent"s):
                    # disambiguate with a random suffix (collision-proof
                    # even after many prior creations).
                    final_slug = f"{slug}-{uuid.uuid4().hex[:8]}"
            else:
                raise psycopg.errors.UniqueViolation(
                    "could not allocate a unique agent slug after 5 attempts"
                )
        return {
            "agent_id": str(agent_id),
            "owner_user_id": owner_user_id,
            "slug": final_slug,
            "display_name": display_name,
            "description": description,
            "enabled": True,
            "archived": False,
            "active_revision_id": None,
            "row_version": 1,
        }

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT agent_id, owner_user_id, slug, display_name, description, "
                "enabled, archived, active_revision_id, row_version "
                "FROM designer_agents WHERE agent_id = %s",
                (uuid.UUID(agent_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        keys = (
            "agent_id", "owner_user_id", "slug", "display_name", "description",
            "enabled", "archived", "active_revision_id", "row_version",
        )
        agent = dict(zip(keys, row, strict=True))
        agent["agent_id"] = str(agent["agent_id"])
        return agent

    async def list_agents(self, owner_user_id: str) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT agent_id, owner_user_id, slug, display_name, description, "
                "enabled, archived, active_revision_id, row_version "
                "FROM designer_agents WHERE owner_user_id = %s AND archived = FALSE "
                "ORDER BY created_at",
                (owner_user_id,),
            )
            rows = await cursor.fetchall()
        agents = []
        for row in rows:
            agent = dict(zip(
                ("agent_id", "owner_user_id", "slug", "display_name", "description",
                 "enabled", "archived", "active_revision_id", "row_version"),
                row, strict=True,
            ))
            agent["agent_id"] = str(agent["agent_id"])
            agents.append(agent)
        return agents

    async def archive_agent(self, agent_id: str) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE designer_agents SET archived = TRUE, enabled = FALSE, "
                "updated_at = now() WHERE agent_id = %s",
                (uuid.UUID(agent_id),),
            )

    async def set_agent_config(self, agent_id: str, config: dict[str, Any]) -> None:
        """Merge bootstrap/runtime metadata (never graph content)."""
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE designer_agents SET config = config || %s, updated_at = now() "
                "WHERE agent_id = %s",
                (Jsonb(config), uuid.UUID(agent_id)),
            )

    async def get_agent_config(self, agent_id: str) -> dict[str, Any]:
        agent = await self.get_agent(agent_id)
        if agent is None:
            return {}
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT config FROM designer_agents WHERE agent_id = %s",
                (uuid.UUID(agent_id),),
            )
            row = await cursor.fetchone()
        return dict(row[0]) if row and row[0] else {}

    async def set_active_revision(self, agent_id: str, revision_id: str) -> None:
        """The mutable active pointer (Clar 3). Activation CAS lives in the
        service layer (P7); this is the unconditional setter used by
        bootstrap."""
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE designer_agents SET active_revision_id = %s, updated_at = now() "
                "WHERE agent_id = %s",
                (revision_id, uuid.UUID(agent_id)),
            )

    async def cas_set_active_revision(
        self, agent_id: str, revision_id: str, expected_version: int
    ) -> int | None:
        """Atomic Compare-And-Swap activation (P7, Clar 3).

        Updates active_revision_id and increments row_version only if
        row_version matches expected_version. Returns the new row_version,
        or None if stale / conflict.
        """
        async with self.connection() as conn:
            cursor = await conn.execute(
                "UPDATE designer_agents "
                "SET active_revision_id = %s, row_version = row_version + 1, updated_at = now() "
                "WHERE agent_id = %s AND row_version = %s "
                "RETURNING row_version",
                (revision_id, uuid.UUID(agent_id), expected_version),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else None

    async def clear_active_revision(
        self, agent_id: str, expected_version: int | None = None
    ) -> int | None:
        """Atomic revocation (P7, R14).

        Sets active_revision_id to NULL and increments row_version. If
        expected_version is specified, enforces CAS. Returns the new
        row_version or None on conflict.
        """
        async with self.connection() as conn:
            if expected_version is not None:
                cursor = await conn.execute(
                    "UPDATE designer_agents "
                    "SET active_revision_id = NULL, "
                    "    row_version = row_version + 1, "
                    "    updated_at = now() "
                    "WHERE agent_id = %s AND row_version = %s "
                    "RETURNING row_version",
                    (uuid.UUID(agent_id), expected_version),
                )
            else:
                cursor = await conn.execute(
                    "UPDATE designer_agents "
                    "SET active_revision_id = NULL, "
                    "    row_version = row_version + 1, "
                    "    updated_at = now() "
                    "WHERE agent_id = %s "
                    "RETURNING row_version",
                    (uuid.UUID(agent_id),),
                )
            row = await cursor.fetchone()
        return int(row[0]) if row else None

    async def upsert_agent_access(
        self,
        *,
        agent_id: str,
        user_id: str,
        can_use: bool,
        can_edit: bool,
        can_activate: bool,
        granted_by: str,
    ) -> None:
        """Per-agent access policy row (C1). user_id '*' = any
        authenticated actor (used only by the Vion bootstrap to preserve
        Phase-1 open access)."""
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO designer_agent_access "
                "(agent_id, user_id, can_use, can_edit, can_activate, granted_by) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (agent_id, user_id) DO UPDATE SET "
                "can_use = EXCLUDED.can_use, can_edit = EXCLUDED.can_edit, "
                "can_activate = EXCLUDED.can_activate",
                (uuid.UUID(agent_id), user_id, can_use, can_edit, can_activate,
                 granted_by),
            )

    async def get_agent_access(
        self, agent_id: str, user_id: str
    ) -> dict[str, Any] | None:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT can_use, can_edit, can_activate FROM designer_agent_access "
                "WHERE agent_id = %s AND user_id = %s",
                (uuid.UUID(agent_id), user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {"can_use": bool(row[0]), "can_edit": bool(row[1]),
                "can_activate": bool(row[2])}

    async def find_agent_by_slug(self, slug: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT agent_id, owner_user_id, slug, display_name, description, "
                "enabled, archived, active_revision_id, row_version "
                "FROM designer_agents WHERE slug = %s",
                (slug,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        keys = (
            "agent_id", "owner_user_id", "slug", "display_name", "description",
            "enabled", "archived", "active_revision_id", "row_version",
        )
        agent = dict(zip(keys, row, strict=True))
        agent["agent_id"] = str(agent["agent_id"])
        return agent

    async def list_all_agents(self) -> list[dict[str, Any]]:
        """Every agent row (catalog/cross-owner views; authorization is
        the caller's job — this is store-level, not policy)."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT agent_id, owner_user_id, slug, display_name, description, "
                "enabled, archived, active_revision_id, row_version "
                "FROM designer_agents ORDER BY created_at",
            )
            rows = await cursor.fetchall()
        agents = []
        for row in rows:
            agent = dict(zip(
                ("agent_id", "owner_user_id", "slug", "display_name", "description",
                 "enabled", "archived", "active_revision_id", "row_version"),
                row, strict=True,
            ))
            agent["agent_id"] = str(agent["agent_id"])
            agents.append(agent)
        return agents

    async def insert_revision(
        self,
        *,
        revision_id: str,
        agent_id: str,
        revision_number: int,
        schema_version: int,
        graph_json: dict[str, Any],
        semantic_hash: str,
        layout_hash: str,
        dependency_lock: dict[str, Any],
        parent_revision_id: str | None,
        created_by: str,
    ) -> int:
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO designer_revisions "
                "(revision_id, agent_id, revision_number, schema_version, graph_json, "
                " semantic_hash, layout_hash, dependency_lock, parent_revision_id, "
                " created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    revision_id, uuid.UUID(agent_id), revision_number, schema_version,
                    Jsonb(graph_json), semantic_hash, layout_hash, Jsonb(dependency_lock),
                    parent_revision_id, created_by,
                ),
            )
        # Bump the agent's row_version so concurrent editors see a new ETag.
        async with self.connection() as conn:
            cursor = await conn.execute(
                "UPDATE designer_agents SET row_version = row_version + 1, "
                "updated_at = now() WHERE agent_id = %s RETURNING row_version",
                (uuid.UUID(agent_id),),
            )
            record = await cursor.fetchone()
        return int(record[0])

    async def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT revision_id, agent_id, revision_number, schema_version, "
                "graph_json, semantic_hash, layout_hash, dependency_lock, validation, "
                "parent_revision_id, created_by, created_at "
                "FROM designer_revisions WHERE revision_id = %s",
                (revision_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        keys = (
            "revision_id", "agent_id", "revision_number", "schema_version",
            "graph_json", "semantic_hash", "layout_hash", "dependency_lock",
            "validation", "parent_revision_id", "created_by", "created_at",
        )
        return dict(zip(keys, row, strict=True))

    async def list_revisions(self, agent_id: str) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT revision_id, agent_id, revision_number, schema_version, "
                "semantic_hash, parent_revision_id, created_by, created_at "
                "FROM designer_revisions WHERE agent_id = %s "
                "ORDER BY revision_number DESC",
                (uuid.UUID(agent_id),),
            )
            rows = await cursor.fetchall()
        return [
            dict(zip(
                ("revision_id", "agent_id", "revision_number", "schema_version",
                 "semantic_hash", "parent_revision_id", "created_by", "created_at"),
                row, strict=True,
            ))
            for row in rows
        ]

    async def next_revision_number(self, agent_id: str) -> int:
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM designer_revisions "
                "WHERE agent_id = %s",
                (uuid.UUID(agent_id),),
            )
            record = await cursor.fetchone()
        return int(record[0])

    async def record_revision_event(
        self,
        *,
        revision_id: str,
        agent_id: str,
        event: str,
        actor_user_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        async with self.connection() as conn:
            await conn.execute(
                "INSERT INTO designer_revision_events "
                "(revision_id, agent_id, event, actor_user_id, data) "
                "VALUES (%s, %s, %s, %s, %s)",
                (revision_id, agent_id, event, actor_user_id, Jsonb(data or {})),
            )

    # --- run events (P8, R04, R17-18) ---

    async def record_run_event(
        self,
        *,
        run_id: str,
        agent_id: str,
        revision_id: str,
        sequence_number: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert a monotonic run event into designer_run_events. Returns the stored event."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO designer_run_events "
                "(run_id, agent_id, revision_id, sequence_number, event_type, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "RETURNING event_id, at",
                (run_id, agent_id, revision_id, sequence_number, event_type, Jsonb(payload)),
            )
            row = await cursor.fetchone()
        assert row is not None
        return {
            "event_id": int(row[0]),
            "run_id": run_id,
            "agent_id": agent_id,
            "revision_id": revision_id,
            "sequence_number": sequence_number,
            "event_type": event_type,
            "payload": payload,
            "at": row[1],
        }

    async def list_run_events(
        self,
        run_id: str,
        *,
        after_event_id: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetch monotonic run events for run_id, ordered by event_id ascending.

        If after_event_id is specified (e.g. from SSE Last-Event-ID), returns
        only events with event_id > after_event_id.
        """
        async with self.connection() as conn:
            if after_event_id is not None and after_event_id > 0:
                cursor = await conn.execute(
                    "SELECT event_id, run_id, agent_id, revision_id, "
                    "sequence_number, event_type, payload, at "
                    "FROM designer_run_events "
                    "WHERE run_id = %s AND event_id > %s "
                    "ORDER BY event_id ASC LIMIT %s",
                    (run_id, after_event_id, limit),
                )
            else:
                cursor = await conn.execute(
                    "SELECT event_id, run_id, agent_id, revision_id, "
                    "sequence_number, event_type, payload, at "
                    "FROM designer_run_events "
                    "WHERE run_id = %s "
                    "ORDER BY event_id ASC LIMIT %s",
                    (run_id, limit),
                )
            rows = await cursor.fetchall()
        return [
            dict(
                zip(
                    (
                        "event_id",
                        "run_id",
                        "agent_id",
                        "revision_id",
                        "sequence_number",
                        "event_type",
                        "payload",
                        "at",
                    ),
                    (
                        int(row[0]),
                        str(row[1]),
                        str(row[2]),
                        str(row[3]),
                        int(row[4]),
                        str(row[5]),
                        dict(row[6]),
                        row[7],
                    ),
                    strict=True,
                )
            )
            for row in rows
        ]

    async def get_run_registry_entry(self, run_id: str) -> dict[str, Any] | None:
        """Lookup a run in run_registry by run_id for authorization / ownership checks."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT run_id, user_id, chat_id, user_message_id, "
                "status, agent_id, revision_id, attempt, failure_reason, "
                "created_at, updated_at "
                "FROM run_registry WHERE run_id = %s",
                (run_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return dict(
            zip(
                (
                    "run_id",
                    "user_id",
                    "chat_id",
                    "user_message_id",
                    "status",
                    "agent_id",
                    "revision_id",
                    "attempt",
                    "failure_reason",
                    "created_at",
                    "updated_at",
                ),
                row,
                strict=True,
            )
        )

    async def list_agent_runs(
        self,
        agent_id: str,
        *,
        user_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent runs for an agent, newest-first."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT run_id, user_id, chat_id, user_message_id, "
                "status, agent_id, revision_id, attempt, failure_reason, "
                "created_at, updated_at "
                "FROM run_registry "
                "WHERE agent_id = %s AND user_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (agent_id, user_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            dict(
                zip(
                    (
                        "run_id",
                        "user_id",
                        "chat_id",
                        "user_message_id",
                        "status",
                        "agent_id",
                        "revision_id",
                        "attempt",
                        "failure_reason",
                        "created_at",
                        "updated_at",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in rows
        ]

