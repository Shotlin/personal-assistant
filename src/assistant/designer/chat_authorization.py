"""Chat-path authorization for Designer-managed agents (C5, Fix 1).

Consulted by ``/v1/chat/completions`` and ``/v1/models`` ONLY when
``DESIGNER_ENABLED=true``. Flag-off callers never import this module's
behavior: the entry points return the legacy result untouched (C2).

Hard rules (C5):
- The authenticated chat actor's permission to use ``body.model`` is
  verified BEFORE claiming a run, invoking a recipe, the planner, a
  runtime, or any provider request.
- Model discovery (listing) is never an authorization decision.
- ``/v1/models`` may filter per actor when verified identity is present;
  otherwise it returns the safe enabled catalog — never a grant.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from assistant.designer.compiler import ExecutionConfig
from assistant.settings import Settings

logger = logging.getLogger("assistant.designer.chat_auth")


@dataclass(frozen=True)
class ChatAgentContext:
    """Resolved serving context for one chat request (flag-on only)."""

    agent_id: str
    revision_id: str
    execution_config: ExecutionConfig
    legacy: bool = False  # bootstrapped Vion (legacy namespaces allowed)


async def resolve_chat_agent(
    app: Any,
    settings: Settings,
    *,
    model_id: str,
    user_id: str,
    model_alias: str,
) -> ChatAgentContext | None:
    """Resolve a model alias to its active agent + compiled config.

    Returns None when the flag is off (caller keeps legacy behavior).
    Raises GatewayError(unsupported_model / 404-style) for unknown,
    disabled, unpublished or unauthorized agents — BEFORE any external
    side effect (C5).
    """
    designer = getattr(app.state, "designer", None)
    if designer is None or not settings.designer_enabled:
        return None
    from assistant.api.schemas import GatewayError

    store = designer["store"]

    # Alias resolution: the legacy alias maps to the bootstrapped Vion
    # agent; Designer-created agents register their own alias on creation
    # (slug-based). Unknown ids never resolve.
    agent = None
    if model_id == model_alias:
        agent = await store.find_agent_by_slug("vion")
    else:
        agent = await store.find_agent_by_slug(model_id)
    if agent is None:
        raise GatewayError("unsupported_model", f"Unknown model {model_id!r}")
    if agent.get("archived") or not agent.get("enabled"):
        raise GatewayError("unsupported_model", f"Model {model_id!r} is disabled")

    # Authorization BEFORE any external work (C5): owner, explicit access
    # row, or the bootstrap '*' row (Vion preserves Phase-1 access).
    actor_user_id = user_id or ""
    if agent["owner_user_id"] != actor_user_id and actor_user_id:
        access = await store.get_agent_access(agent["agent_id"], actor_user_id)
        wildcard = await store.get_agent_access(agent["agent_id"], "*")
        granted = (access and access["can_use"]) or (wildcard and wildcard["can_use"])
        if not granted:
            user_hash = hashlib.sha256(actor_user_id.encode()).hexdigest()[:12]
            logger.info(
                "designer_chat_use_denied",
                extra={
                    "event": "designer_chat_use_denied",
                    "agent_id": agent["agent_id"],
                    "user_hash": f"sha256:{user_hash}",
                },
            )
            from assistant.api.schemas import GatewayError as _GE

            raise _GE("unsupported_model", f"Model {model_id!r} is not available")

    revision_id = agent.get("active_revision_id")
    if not revision_id:
        raise GatewayError("unsupported_model", f"Model {model_id!r} has no active version")

    revision = await store.get_revision(str(revision_id))
    if revision is None:
        raise GatewayError("unsupported_model", "active revision missing")

    from assistant.designer.compiler import compile_execution_config
    from assistant.designer.schemas import parse_graph_document

    graph = parse_graph_document(dict(revision["graph_json"]))
    config = compile_execution_config(
        agent_id=str(agent["agent_id"]), revision_id=str(revision_id), graph=graph
    )
    agent_config = await store.get_agent_config(agent["agent_id"])
    return ChatAgentContext(
        agent_id=str(agent["agent_id"]),
        revision_id=str(revision_id),
        execution_config=config,
        legacy=bool(agent_config.get("legacy")),
    )


def native_dispatch_allowed(context: ChatAgentContext | None) -> bool:
    """Capability gate for the recipe/planner fast paths (A2).

    Flag-off (context None): legacy behavior — native dispatch follows the
    existing bounded posture. Flag-on: the active revision's effective
    capability set decides; a disconnected CUA node blocks every route.
    """
    if context is None:
        return True
    return context.execution_config.allows_native_dispatch()


def denied_apps_for(context: ChatAgentContext | None) -> set[str]:
    """Fast-path router context: an agent without CUA denies desktop apps
    so 'open chrome' never dispatches natively (A2)."""
    if native_dispatch_allowed(context):
        return set()
    # All router apps are desktop actions; without the CUA capability
    # every one of them is denied at the router itself.
    from assistant.runtime.router import APP_IDS

    return set(APP_IDS)


async def list_models_for_actor(
    app: Any,
    settings: Settings,
    *,
    user_id: str,
) -> list[dict[str, str]] | None:
    """/v1/models entries for the actor (flag-on only; None = legacy).

    Model discovery is presentation, never authorization (C5): the list
    contains only agents usable by this actor, and chat still re-checks.
    """
    designer = getattr(app.state, "designer", None)
    if designer is None or not settings.designer_enabled:
        return None
    store = designer["store"]
    entries: list[dict[str, str]] = []
    agents = await store.list_all_agents()
    for agent in agents:
        if agent.get("archived") or not agent.get("enabled"):
            continue
        if not agent.get("active_revision_id"):
            continue
        if user_id and agent["owner_user_id"] != user_id:
            access = await store.get_agent_access(agent["agent_id"], user_id)
            wildcard = await store.get_agent_access(agent["agent_id"], "*")
            if not ((access and access["can_use"]) or (wildcard and wildcard["can_use"])):
                continue
        alias = agent["slug"] if agent["slug"] != "vion" else settings.assistant_model_id
        entries.append({"id": alias, "owned_by": "local"})
    if not entries:
        # Never return an empty catalog: the legacy alias stays visible.
        entries.append({"id": settings.assistant_model_id, "owned_by": "local"})
    return entries
