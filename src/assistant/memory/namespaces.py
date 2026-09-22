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


# --- Sani local identity (Sani master doc section 25) ---
# The local desktop product drops the Open WebUI identity namespace. The
# ``sani:`` ids are additive now so sani-core can adopt them from day one;
# ``owui:`` ids keep resolving read-only for history until the gateway
# retires (explicit migration in docs/sani-storage-migration.md).


def thread_id_for_sani(conversation_id: str) -> str:
    """Local Sani conversation thread id (``sani:<conversation_id>``)."""
    return f"sani:{_require_non_empty(conversation_id, 'conversation_id')}"


def split_sani_thread_id(thread_id: str) -> str | None:
    """Return the conversation id for a valid ``sani:`` thread id, else None."""
    prefix, sep, rest = thread_id.partition(":")
    if sep and prefix == "sani" and rest:
        return rest
    return None


# --- Designer agent scoping (R15/R21, plan P5) ---
# Only the BOOTSTRAPPED Vion resolves legacy namespaces; every Designer
# agent gets an agent-scoped thread that carries the execution epoch, so a
# behavior-changing activation starts a fresh context lineage (R15).


def thread_id_for_agent(
    agent_id: str, execution_epoch: int, user_id: str, chat_id: str
) -> str:
    """Agent-scoped thread id: one lineage per (agent, epoch, chat).

    Old epochs keep their history as history; new epochs never see stale
    hidden prompts, skill bodies or revoked knowledge from before.
    """
    return (
        f"agent:{_require_non_empty(agent_id, 'agent_id')}"
        f":e{_require_non_empty(str(execution_epoch), 'execution_epoch')}"
        f":owui:{_require_non_empty(user_id, 'user_id')}:{_require_non_empty(chat_id, 'chat_id')}"
    )


def is_legacy_thread(thread_id: str) -> bool:
    """True for pre-Designer threads (bootstrapped Vion only)."""
    return thread_id.startswith("owui:")


def agent_memory_namespace(agent_id: str, user_id: str) -> tuple[str, ...]:
    """Server-derived (owner, agent) memory scope — R15: no anonymous
    fallback, no client-path strings."""
    return ("agents", _require_non_empty(agent_id, "agent_id"), "users",
            _require_non_empty(user_id, "user_id"))
