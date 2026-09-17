"""Open WebUI identity header mapping (spec sections 15.2 and 16.4).

Header lineage (configured in Open WebUI's connection):
- user identity block forwarded via user-info forwarding
- turn lineage via custom headers (X-OpenWebUI-*-Id / X-OpenWebUI-Task)

Development mode additionally accepts explicit test headers so automated
tests can drive the gateway without Open WebUI.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from assistant.api.schemas import GatewayError
from assistant.memory.namespaces import thread_id_for

USER_ID_HEADER = "X-OpenWebUI-User-Id"
USER_NAME_HEADER = "X-OpenWebUI-User-Name"
USER_EMAIL_HEADER = "X-OpenWebUI-User-Email"
USER_ROLE_HEADER = "X-OpenWebUI-User-Role"
CHAT_ID_HEADER = "X-OpenWebUI-Chat-Id"
MESSAGE_ID_HEADER = "X-OpenWebUI-Message-Id"
USER_MESSAGE_ID_HEADER = "X-OpenWebUI-User-Message-Id"
USER_MESSAGE_PARENT_ID_HEADER = "X-OpenWebUI-User-Message-Parent-Id"
TASK_HEADER = "X-OpenWebUI-Task"

DEV_USER_ID_HEADER = "X-Assistant-Dev-User-Id"
DEV_CHAT_ID_HEADER = "X-Assistant-Dev-Chat-Id"

IdentitySource = Literal["openwebui", "dev-test"]


@dataclass(frozen=True)
class RequestIdentity:
    """Identity extracted from one gateway request."""

    user_id: str
    chat_id: str
    thread_id: str
    message_id: str | None
    user_message_id: str | None
    user_message_parent_id: str | None
    task: str | None
    is_utility: bool
    source: IdentitySource


def is_utility_task(task: str | None) -> bool:
    """True when the request is a background utility task (title/tags/...).

    Fail-safe: any declared task header value routes away from the agent
    run, so a utility request can never trigger computer control
    (spec section 15.3).
    """
    return bool(task and task.strip())


def extract_identity(
    headers: Mapping[str, str],
    *,
    is_production: bool,
) -> RequestIdentity:
    """Map request headers to a thread identity, or raise ``missing_chat_identity``."""
    user_id = (headers.get(USER_ID_HEADER) or "").strip()
    chat_id = (headers.get(CHAT_ID_HEADER) or "").strip()
    source: IdentitySource = "openwebui"

    if not user_id or not chat_id:
        dev_user = (headers.get(DEV_USER_ID_HEADER) or "").strip()
        dev_chat = (headers.get(DEV_CHAT_ID_HEADER) or "").strip()
        if not is_production and dev_user and dev_chat:
            user_id, chat_id = dev_user, dev_chat
            source = "dev-test"
        else:
            raise GatewayError(
                "missing_chat_identity",
                "Missing Open WebUI identity headers "
                f"({USER_ID_HEADER}, {CHAT_ID_HEADER}); stateful execution rejected.",
            )

    task = headers.get(TASK_HEADER)
    return RequestIdentity(
        user_id=user_id,
        chat_id=chat_id,
        thread_id=thread_id_for(user_id, chat_id),
        message_id=headers.get(MESSAGE_ID_HEADER),
        user_message_id=headers.get(USER_MESSAGE_ID_HEADER),
        user_message_parent_id=headers.get(USER_MESSAGE_PARENT_ID_HEADER),
        task=task,
        is_utility=is_utility_task(task),
        source=source,
    )
