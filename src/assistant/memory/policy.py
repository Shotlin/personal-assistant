"""Memory write policy (spec 14.3-14.4).

The gate blocks durable storage of secrets and emits structured memory
events without persisting (or logging) secret-bearing content.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from deepagents.backends.protocol import EditResult, WriteResult
from deepagents.backends.store import StoreBackend

logger = logging.getLogger("assistant.memory.policy")

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|password|passwd|"
    r"secret|otp|one[- ]time (?:code|password|url))\b\s*[=:]\s*\S+"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{10,}")
_PROVIDER_KEY_RE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b")

_REJECTION_REASONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_SECRET_ASSIGNMENT_RE, "explicit secret assignment"),
    (_BEARER_TOKEN_RE, "bearer-style token"),
    (_PROVIDER_KEY_RE, "provider-style API key"),
)


def contains_secret(content: str) -> str | None:
    """Return a stable rejection reason when content looks secret-bearing."""
    for pattern, reason in _REJECTION_REASONS:
        if pattern.search(content):
            return reason
    return None


def _hashed_user_id(user_id: str) -> str:
    return f"sha256:{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:12]}"


class PolicyStoreBackend(StoreBackend):
    """StoreBackend that refuses secret-bearing memory writes.

    Wraps the LangGraph store-backed file backend used for the
    ``/memories/`` route. Rejected writes return an error result the agent
    can observe; the structured event (spec 14.4) never contains content.
    """

    def _log_event(self, operation: str, file_path: str, reason: str) -> None:
        try:
            namespace = self._get_namespace()
        except (RuntimeError, KeyError, IndexError):
            namespace = None
        user_id = namespace[1] if namespace and len(namespace) >= 2 else None
        event: dict[str, Any] = {
            "user_id_hash": _hashed_user_id(user_id) if user_id else None,
            "memory_path": file_path,
            "operation": operation,
            "source_thread_id": None,
            "created_at": datetime.now(UTC).isoformat(),
        }
        logger.info("memory_write_rejected", extra=event)

    # -- sync surface (used by sync tool execution) -------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        reason = contains_secret(content)
        if reason is not None:
            self._log_event("write", file_path, reason)
            return WriteResult(error=f"Memory write rejected by policy: {reason}")
        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        reason = contains_secret(new_string)
        if reason is not None:
            self._log_event("edit", file_path, reason)
            return EditResult(error=f"Memory edit rejected by policy: {reason}")
        return super().edit(file_path, old_string, new_string, replace_all)

    # -- async surface (native, avoids sync store calls in async context) --

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        reason = contains_secret(content)
        if reason is not None:
            self._log_event("write", file_path, reason)
            return WriteResult(error=f"Memory write rejected by policy: {reason}")
        return await super().awrite(file_path, content)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        reason = contains_secret(new_string)
        if reason is not None:
            self._log_event("edit", file_path, reason)
            return EditResult(error=f"Memory edit rejected by policy: {reason}")
        return await super().aedit(file_path, old_string, new_string, replace_all)
