"""Scoped runtime pool: acquire/release, staleness, drain (P5, Fix 3, Safety 2).

The pool caches CompiledAgentRuntime objects keyed by the frozen-plan
``RuntimeCacheKey``:

    (agent_id, active_revision_id, execution_epoch, credential_scope_key)

with credential **generations** verified separately (Safety 2): a runtime
bound to credential generation N is marked stale the moment that
credential rotates to N+1 or is revoked — drain closes its connector
sessions and the next acquisition rebuilds. Checks are local and O(1);
no network call precedes a tool dispatch.

One lease per user at a time per key; single-flight init per key; idle
runtimes close after a bounded TTL. User-scoped MCP sessions are never
reused across users because the scope key is part of the cache key.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from assistant.designer.compiler import ExecutionConfig
from assistant.designer.connectors import ScopeKind

logger = logging.getLogger("assistant.designer.runtimes")

IDLE_CLOSE_SECONDS = 300.0


@dataclass(frozen=True)
class RuntimeCacheKey:
    agent_id: str
    revision_id: str
    execution_epoch: int
    credential_scope_key: str  # "shared" | "user:<id>" | "run:<run_id>"


@dataclass
class RuntimeLease:
    """Reference-counted handle over one cached runtime."""

    key: RuntimeCacheKey
    config: ExecutionConfig
    runtime: Any  # CompiledAgentRuntime (P5: agent graph handle)
    _pool: RuntimePool
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._pool._release_lease(self)


@dataclass
class _PoolEntry:
    key: RuntimeCacheKey
    config: ExecutionConfig
    runtime: Any
    credential_generations: dict[str, int] = field(default_factory=dict)
    refcount: int = 0
    last_used: float = field(default_factory=time.monotonic)
    draining: bool = False


class RuntimePool:
    """Owns compiled runtimes. ``acquire`` is single-flight per key."""

    def __init__(
        self,
        builder: Any,
        *,
        idle_close_seconds: float = IDLE_CLOSE_SECONDS,
    ) -> None:
        # builder(key, config) -> (runtime, credential_generations)
        self._builder = builder
        self._entries: dict[RuntimeCacheKey, _PoolEntry] = {}
        self._locks: dict[RuntimeCacheKey, asyncio.Lock] = {}
        self._stale_credentials: set[str] = set()
        self._idle_close_seconds = idle_close_seconds

    def _lock_for(self, key: RuntimeCacheKey) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def scope_key(self, scope: ScopeKind, actor_id: str | None, run_id: str | None) -> str:
        """Compute the credential-scope component of the cache key (Fix 3)."""
        if scope is ScopeKind.SHARED:
            return "shared"
        if scope is ScopeKind.USER_SCOPED:
            return f"user:{actor_id}"
        return f"run:{run_id}"

    async def acquire(
        self,
        *,
        agent_id: str,
        revision_id: str,
        execution_epoch: int,
        scope: ScopeKind,
        actor_id: str | None,
        run_id: str | None,
        config: ExecutionConfig,
    ) -> RuntimeLease:
        key = RuntimeCacheKey(
            agent_id=agent_id,
            revision_id=revision_id,
            execution_epoch=execution_epoch,
            credential_scope_key=self.scope_key(scope, actor_id, run_id),
        )
        async with self._lock_for(key):
            entry = self._entries.get(key)
            if entry is not None and not self._is_stale(entry):
                entry.refcount += 1
                entry.last_used = time.monotonic()
                return RuntimeLease(key=key, config=config, runtime=entry.runtime,
                                    _pool=self)
            if entry is not None:
                # Stale (generation changed / revoked): drain before rebuild.
                await self._drain_entry(entry)
                self._entries.pop(key, None)
            runtime, credential_generations = await self._builder(key, config)
            entry = _PoolEntry(
                key=key, config=config, runtime=runtime,
                credential_generations=credential_generations,
            )
            self._entries[key] = entry
            entry.refcount = 1
            # A rebuild bound fresh material: clear explicit stale marks
            # for credentials whose live generation now matches the bind.
            for credential_id, generation in credential_generations.items():
                current = self._current_generation(credential_id)
                if current is not None and current == generation:
                    self._stale_credentials.discard(credential_id)
            return RuntimeLease(key=key, config=config, runtime=entry.runtime, _pool=self)

    def _is_stale(self, entry: _PoolEntry) -> bool:
        """Safety 2: any bound credential rotated or revoked since bind."""
        for credential_id, generation in entry.credential_generations.items():
            if credential_id in self._stale_credentials:
                return True
            current = self._current_generation(credential_id)
            if current is not None and current != generation:
                return True
        return False

    def _current_generation(self, credential_id: str) -> int | None:
        # Wired to the credential store by build_designer_state; None means
        # no live registry (tests) and staleness rests on explicit marks.
        getter = getattr(self, "_generation_lookup", None)
        return getter(credential_id) if getter else None

    def set_generation_lookup(self, lookup: Any) -> None:
        self._generation_lookup = lookup

    def mark_credential_stale(self, credential_id: str) -> list[RuntimeCacheKey]:
        """Safety 2: rotation/revocation marks every runtime bound to the
        credential stale. Returns the keys drained (or draining)."""
        self._stale_credentials.add(credential_id)
        return [
            entry.key for entry in self._entries.values()
            if credential_id in entry.credential_generations
        ]

    async def drain_stale(self, credential_id: str) -> int:
        """Drain/close connector sessions of runtimes bound to a stale
        credential; next acquisition rebuilds (Safety 2). Never waits for
        an idle timeout."""
        keys = self.mark_credential_stale(credential_id)
        drained = 0
        for key in keys:
            entry = self._entries.get(key)
            if entry is None:
                continue
            if entry.refcount == 0:
                await self._drain_entry(entry)
                self._entries.pop(key, None)
            else:
                entry.draining = True  # closed on release
            drained += 1
        return drained

    async def _release_lease(self, lease: RuntimeLease) -> None:
        entry = self._entries.get(lease.key)
        if entry is None:
            return
        entry.refcount = max(0, entry.refcount - 1)
        entry.last_used = time.monotonic()
        if entry.refcount == 0 and entry.draining:
            await self._drain_entry(entry)
            self._entries.pop(lease.key, None)

    async def _drain_entry(self, entry: _PoolEntry) -> None:
        closer = getattr(entry.runtime, "aclose", None)
        entry.draining = True
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001 -- drain must not raise
                logger.warning("runtime_drain_failed", extra={
                    "event": "runtime_drain_failed", "agent_id": entry.key.agent_id,
                })

    async def close_idle(self, *, now: float | None = None) -> int:
        """Close runtimes idle beyond the TTL with zero leases (P5)."""
        now_mono = time.monotonic() if now is None else now
        closed = 0
        for key, entry in list(self._entries.items()):
            if entry.refcount == 0 and (
                now_mono - entry.last_used >= self._idle_close_seconds
            ):
                await self._drain_entry(entry)
                self._entries.pop(key, None)
                closed += 1
        return closed

    async def aclose(self) -> None:
        for key, entry in list(self._entries.items()):
            await self._drain_entry(entry)
            self._entries.pop(key, None)
