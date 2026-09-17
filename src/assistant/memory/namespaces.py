"""Deterministic thread IDs and user-scoped store namespaces (spec 14.1-14.2).

Thread memory: one Open WebUI chat maps to one LangGraph thread
(``owui:<user_id>:<chat_id>``) so user scope stays explicit.

Long-term memory: a per-user store namespace keeps users isolated.
"""

from __future__ import annotations


def _require_non_empty(value: str, name: str) -> str:
    cleaned = value.strip()
    if not value or not value.strip():
        msg = f"{name} must be a non-empty string"
        raise ValueError(msg)
    return cleaned


def thread_id_for(user_id: str, chat_id: str) -> str:
    """Map an Open WebUI chat to one LangGraph thread (spec 14.1)."""
    return f"owui:{_require_non_empty(user_id, 'user_id')}:{_require_non_empty(chat_id, 'chat_id')}"


def split_thread_id(thread_id: str) -> tuple[str, str] | None:
    """Return ``(user_id, chat_id)`` for a valid ``owui:`` thread id, else None."""
    prefix, sep, rest = thread_id.partition(":")
    if not sep or prefix != "owui":
        return None
    user_id, sep2, chat_id = rest.partition(":")
    if not sep2 or not user_id or not chat_id:
        return None
    return user_id, chat_id


def user_memory_namespace(user_id: str) -> tuple[str, ...]:
    """User-scoped long-term memory namespace (spec 14.2)."""
    return ("users", _require_non_empty(user_id, "user_id"), "assistant-memory")


def user_id_from_namespace(namespace: tuple[str, ...]) -> str | None:
    """Extract the user id from a ``("users", user_id, ...)`` namespace."""
    if len(namespace) >= 2 and namespace[0] == "users" and namespace[1]:
        return namespace[1]
    return None
