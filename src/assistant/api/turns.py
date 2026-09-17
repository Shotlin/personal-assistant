"""Turn resolution: map an incoming submission onto the persisted thread.

Implements spec section 16.5: never duplicate history into an existing
thread, and never execute the same user turn twice merely because Open
WebUI resubmits history (retries and regeneration).

Dedup key: the user turn's content (and, when available, the
``X-OpenWebUI-User-Message-Id`` lineage header for logging/bookkeeping).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from assistant.api.schemas import ChatMessage

TurnMode = Literal["initialize", "new_turn", "regenerate", "resume"]


@dataclass(frozen=True)
class TurnDecision:
    """How to feed one gateway request into the agent graph."""

    mode: TurnMode
    #: Input messages for the agent invocation (empty list = continue state).
    messages: list[BaseMessage]
    #: Whether to fork from an earlier checkpoint before running.
    fork: bool


def normalize_history(messages: Sequence[ChatMessage]) -> list[BaseMessage]:
    """Map OpenAI roles to LangGraph messages; drop roles we do not persist."""
    normalized: list[BaseMessage] = []
    for message in messages:
        raw = message.content
        content = raw if isinstance(raw, str) else str(raw or "")
        if message.role == "system":
            normalized.append(SystemMessage(content=content))
        elif message.role == "user":
            normalized.append(HumanMessage(content=content))
        elif message.role == "assistant":
            normalized.append(AIMessage(content=content))
        # "tool" messages from clients are not part of visible history.
    return normalized


def message_text(message: BaseMessage) -> str:
    """Best-effort text of a LangChain message (handles ``text()`` methods)."""
    text_attr = getattr(message, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    if callable(text_attr):
        return str(text_attr())
    return str(message.content or "")


def last_user_content(messages: Sequence[BaseMessage]) -> str | None:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message_text(message)
    return None


def has_assistant_after_last_user(messages: Sequence[BaseMessage]) -> bool:
    """True when an assistant message follows the final user message."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return False
        if isinstance(message, AIMessage):
            return True
    return False


def decide_turn(
    persisted: Sequence[BaseMessage], incoming: Sequence[BaseMessage]
) -> TurnDecision:
    """Decide initialize / new_turn / regenerate / resume for one request."""
    incoming_user = last_user_content(incoming)
    if not persisted:
        return TurnDecision(mode="initialize", messages=list(incoming), fork=False)

    if incoming_user is None:
        # Submission without a user message: continue the persisted state.
        return TurnDecision(mode="resume", messages=[], fork=True)

    persisted_user = last_user_content(persisted)
    if persisted_user is None or incoming_user != persisted_user:
        return TurnDecision(
            mode="new_turn", messages=[HumanMessage(content=incoming_user)], fork=False
        )

    # Same user turn resubmitted.
    if has_assistant_after_last_user(persisted):
        # Regeneration: replay the turn from before it was first added.
        return TurnDecision(
            mode="regenerate", messages=[HumanMessage(content=incoming_user)], fork=True
        )
    # Retry after a failed turn: replay from before it as well (uniform,
    # avoids resuming an unknown mid-tool state).
    return TurnDecision(
        mode="regenerate", messages=[HumanMessage(content=incoming_user)], fork=True
    )
