"""Async Postgres checkpointer and store lifecycle (spec 14.1-14.2, Task 3).

Production persistence is always PostgreSQL; in-memory checkpointers are
test-only (global constraint). ``open_memory_resources`` owns the schema
setup (idempotent) and the connection lifecycle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore


@dataclass
class MemoryResources:
    """Async checkpointer and store sharing one validated database."""

    saver: AsyncPostgresSaver
    store: AsyncPostgresStore


@asynccontextmanager
async def open_memory_resources(database_url: str) -> AsyncIterator[MemoryResources]:
    """Open the async checkpointer and store with schema setup, then close them.

    Used by the FastAPI lifespan, the init script, and integration tests.
    """
    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        await saver.setup()
        async with AsyncPostgresStore.from_conn_string(database_url) as store:
            await store.setup()
            yield MemoryResources(saver=saver, store=store)
