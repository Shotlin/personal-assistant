"""Embedded SQLite persistence for the Sani desktop runtime (Sani doc 2-5, 14).

Replaces the PostgreSQL memory dependency with ONE local database file
(``sani.db``):

- checkpoints: LangGraph ``AsyncSqliteSaver`` tables (agent_checkpoints);
- long-term memory: the ``store_items`` table through :class:`SqliteStore`,
  the small local ``BaseStore`` adapter the master doc explicitly permits
  (the langgraph SQLite package ships a saver but no store).

No server, no port, no credentials, no Docker. WAL journaling + NORMAL
synchronous give crash-safe writes at desktop scale. The secret-screening
policy (``assistant.memory.policy``) is backend-agnostic -- it wraps any
``BaseStore`` -- so moving from Postgres to this store preserves it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    PutOp,
    SearchItem,
    SearchOp,
)

logger = logging.getLogger("assistant.memory.local")

_SCHEMA_VERSION = 1

_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS store_items (
    namespace  TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE INDEX IF NOT EXISTS store_items_updated_idx ON store_items (updated_at);
"""

_MIGRATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

_NS_SEPARATOR = "\x1f"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ns_to_text(namespace: tuple[str, ...]) -> str:
    return _NS_SEPARATOR.join(namespace)


def _ns_from_text(text: str) -> tuple[str, ...]:
    return tuple(text.split(_NS_SEPARATOR))


class SqliteStore(BaseStore):
    """``BaseStore`` over one embedded SQLite file.

    Implements the LangGraph batch primitive (``batch``/``abatch``); the
    convenience methods (``aput``/``aget``/``asearch``/``adelete``) build on
    it, which is exactly the surface the deepagents memory backend and the
    secret-screening policy use.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, db_path: str | Path) -> SqliteStore:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        cls._setup(conn)
        return cls(conn)

    @staticmethod
    def _setup(conn: sqlite3.Connection) -> None:
        with conn:
            conn.executescript(_STORE_SCHEMA)
            conn.executescript(_MIGRATIONS_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (_SCHEMA_VERSION, _now_iso()),
            )

    def close(self) -> None:
        self._conn.close()

    # -- BaseStore primitive -------------------------------------------------

    def batch(self, ops: Iterable[Op]) -> list[Any]:
        results: list[Any] = []
        try:
            for op in ops:
                results.append(self._handle(op))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return results

    async def abatch(self, ops: Iterable[Op]) -> list[Any]:
        return await asyncio.to_thread(self.batch, ops)

    # -- op handlers ----------------------------------------------------------

    def _handle(self, op: Op) -> Any:
        if isinstance(op, GetOp):
            return self._get(op)
        if isinstance(op, PutOp):
            return self._put(op)
        if isinstance(op, SearchOp):
            return self._search(op)
        if isinstance(op, ListNamespacesOp):
            return self._list_namespaces(op)
        raise TypeError(f"unsupported store op: {type(op).__name__}")

    def _get(self, op: GetOp) -> Item | None:
        row = self._conn.execute(
            "SELECT value, created_at, updated_at FROM store_items WHERE namespace = ? AND key = ?",
            (_ns_to_text(op.namespace), op.key),
        ).fetchone()
        if row is None:
            return None
        return Item(
            value=json.loads(row[0]),
            key=op.key,
            namespace=op.namespace,
            created_at=datetime.fromisoformat(row[1]),
            updated_at=datetime.fromisoformat(row[2]),
        )

    def _put(self, op: PutOp) -> None:
        if op.value is None:
            self._conn.execute(
                "DELETE FROM store_items WHERE namespace = ? AND key = ?",
                (_ns_to_text(op.namespace), op.key),
            )
            return None
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO store_items (namespace, key, value, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (_ns_to_text(op.namespace), op.key, json.dumps(op.value), now, now),
        )
        return None

    def _search(self, op: SearchOp) -> list[SearchItem]:
        if op.query is not None:
            raise NotImplementedError(
                "semantic search requires an embeddings model; not used by Sani"
            )
        prefix_text = _ns_to_text(op.namespace_prefix)
        if op.namespace_prefix:
            rows = self._conn.execute(
                "SELECT namespace, key, value, created_at, updated_at "
                "FROM store_items WHERE namespace = ? OR namespace LIKE ?",
                (prefix_text, prefix_text + _NS_SEPARATOR + "%"),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT namespace, key, value, created_at, updated_at FROM store_items"
            ).fetchall()
        items: list[SearchItem] = []
        for namespace_text, key, value, created_at, updated_at in rows:
            value_dict = json.loads(value)
            if op.filter and not _matches_filter(value_dict, op.filter):
                continue
            items.append(
                SearchItem(
                    value=value_dict,
                    key=key,
                    namespace=_ns_from_text(namespace_text),
                    created_at=datetime.fromisoformat(created_at),
                    updated_at=datetime.fromisoformat(updated_at),
                    score=None,
                )
            )
        items.sort(key=lambda item: (item.updated_at, item.key), reverse=True)
        return items[op.offset : op.offset + op.limit]

    def _list_namespaces(self, op: ListNamespacesOp) -> list[tuple[str, ...]]:
        rows = self._conn.execute(
            "SELECT DISTINCT namespace FROM store_items ORDER BY namespace"
        ).fetchall()
        namespaces = [_ns_from_text(row[0]) for row in rows]
        if op.match_conditions:
            namespaces = [
                namespace
                for namespace in namespaces
                if _matches_conditions(namespace, op.match_conditions)
            ]
        if op.max_depth is not None:
            namespaces = [namespace[: op.max_depth] for namespace in namespaces]
            namespaces = sorted(set(namespaces))
        return namespaces[op.offset : op.offset + op.limit]


def _matches_filter(value: dict[str, Any], filter_: dict[str, Any]) -> bool:
    return all(value.get(key) == expected for key, expected in filter_.items())


def _matches_conditions(namespace: tuple[str, ...], conditions: Sequence[Any]) -> bool:
    for condition in conditions:
        match_type, path = condition
        if match_type == "prefix":
            if namespace[: len(path)] != tuple(path):
                return False
        elif match_type == "suffix":
            if not path or namespace[-len(path) :] != tuple(path):
                return False
    return True


@dataclass
class LocalMemoryResources:
    """Async checkpointer and store sharing one embedded ``sani.db``."""

    saver: AsyncSqliteSaver
    store: SqliteStore


@asynccontextmanager
async def open_local_memory_resources(
    db_path: str | Path,
) -> AsyncIterator[LocalMemoryResources]:
    """Open the embedded checkpointer and store with schema setup, then close.

    Local replacement for :func:`assistant.memory.postgres.open_memory_resources`
    (Sani master doc section 4): same contract, no server.
    """
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await saver.setup()
        store = SqliteStore.open(path)
        try:
            yield LocalMemoryResources(saver=saver, store=store)
        finally:
            store.close()
