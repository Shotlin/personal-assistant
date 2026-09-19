"""Bootstrap Vion into the Agent Designer registry (R21, Fix 1).

Imports the ACTUAL current configuration (settings + system prompt +
built-in skills + CUA posture) as the ``vion`` agent's revision 1 and
marks it active, preserving the ``personal-assistant-v1`` alias. Runs
only when explicitly invoked or at flag-on startup (Safety 1: the
flag-off gateway never bootstraps anything).

Idempotent: if the ``vion`` agent exists, the bootstrap verifies its
active pointer (repairs only a missing pointer) and never rewrites
existing revisions — graph content is immutable (Clar 3).

The bootstrapped agent carries ``config.legacy_namespace = true``: only
it may resolve legacy ``owui:`` thread ids and legacy memory namespaces
(R21); agents created in the Designer cannot.

Usage::

    uv run python scripts/bootstrap_designer.py [--database-url ...]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from assistant.designer.schemas import parse_graph_document  # noqa: E402
from assistant.designer.validation import (  # noqa: E402
    layout_hash,
    semantic_hash,
    validate_graph,
)

VION_SLUG = "vion"
VION_ALIAS = "personal-assistant-v1"


def build_vion_graph(*, cua_enabled: bool, model_provider: str, model_name: str) -> dict:
    """The bootstrapped Vion graph mirrors the Phase-1 singleton exactly:
    operator-env model, code-owned safety prompt, all built-in skills,
    user+thread memory, default context, CUA per current posture."""
    nodes: list[dict] = [
        {"id": "vion-agent", "type": "agent", "position": {"x": 0, "y": 0},
         "data": {"enabled": True, "config": {}}},
        {
            "id": "vion-model", "type": "model", "position": {"x": 280, "y": 0},
            "data": {
                "enabled": True,
                "config": {
                    "provider": model_provider,
                    "model_id": model_name,
                    "credential_ref": "operator-env",
                },
            },
        },
        {
            "id": "vion-prompt", "type": "prompt", "position": {"x": 280, "y": 140},
            "data": {"enabled": True,
                     "config": {"resource_ref": "gateway:vion-system-prompt"}},
        },
    ]
    for skill in ("general-assistant", "computer-use", "software-delegation"):
        nodes.append(
            {"id": f"vion-skill-{skill}", "type": "skill",
             "position": {"x": 560, "y": 0},
             "data": {"enabled": True,
                      "config": {"source": "gateway", "id": f"gateway:{skill}",
                                 "revision_or_hash": "bootstrap"}}}
        )
    nodes.append(
        {"id": "vion-memory", "type": "memory", "position": {"x": 560, "y": 140},
         "data": {"enabled": True,
                  "config": {"kind": "user", "thread_recall": True, "user_memory": True}}}
    )
    nodes.append(
        {"id": "vion-context", "type": "context", "position": {"x": 560, "y": 280},
         "data": {"enabled": True, "config": {}}}
    )
    if cua_enabled:
        nodes.append(
            {"id": "vion-cua", "type": "cua", "position": {"x": 840, "y": 0},
             "data": {"enabled": True,
                      "config": {"host_profile": "bounded", "connector_id": "cua-local"}}}
        )
    edges = [
        {"id": "ve-model", "source": "vion-agent", "target": "vion-model",
         "sourceHandle": "root", "targetHandle": "model"},
        {"id": "ve-prompt", "source": "vion-agent", "target": "vion-prompt",
         "sourceHandle": "root", "targetHandle": "prompt"},
        {"id": "ve-memory", "source": "vion-agent", "target": "vion-memory",
         "sourceHandle": "root", "targetHandle": "memory"},
        {"id": "ve-context", "source": "vion-agent", "target": "vion-context",
         "sourceHandle": "root", "targetHandle": "context"},
    ]
    for skill in ("general-assistant", "computer-use", "software-delegation"):
        edges.append(
            {"id": f"ve-skill-{skill}", "source": "vion-agent",
             "target": f"vion-skill-{skill}", "sourceHandle": "root",
             "targetHandle": "skill"}
        )
    if cua_enabled:
        edges.append(
            {"id": "ve-cua", "source": "vion-agent", "target": "vion-cua",
             "sourceHandle": "root", "targetHandle": "cua"}
        )
    return {
        "schema_version": 1,
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


async def bootstrap(
    database_url: str,
    *,
    cua_enabled: bool,
    model_provider: str,
    model_name: str,
    owner_user_id: str = "local-owner",
) -> dict:
    """Create/verify the Vion registry row + active revision 1."""
    from scripts.migrate_designer import apply_migrations

    await apply_migrations(database_url, REPO_ROOT / "migrations" / "designer")

    from assistant.designer.store import DesignerStore

    store = await DesignerStore.connect(database_url)
    try:
        graph_payload = build_vion_graph(
            cua_enabled=cua_enabled,
            model_provider=model_provider,
            model_name=model_name,
        )
        graph = parse_graph_document(graph_payload)
        report = validate_graph(graph)
        if not report.ok:
            raise RuntimeError(f"bootstrap graph invalid: {report.to_dict()}")

        # Find or create the vion agent row.
        agents = await store.list_agents(owner_user_id)
        agent = next((a for a in agents if a["slug"] == VION_SLUG), None)
        if agent is None:
            agent = await store.insert_agent(
                owner_user_id=owner_user_id,
                slug=VION_SLUG,
                display_name="Vion",
                description="Bootstrapped Phase-1 assistant (legacy namespace)",
            )
        await store.set_agent_config(agent["agent_id"], {"legacy": True, "alias": VION_ALIAS})

        # Open to every authenticated actor, exactly like Phase 1 (R19):
        # the Designer grant table can restrict later.
        await store.upsert_agent_access(
            agent_id=agent["agent_id"], user_id="*", can_use=True,
            can_edit=False, can_activate=False, granted_by="bootstrap",
        )

        existing = await store.list_revisions(agent["agent_id"])
        if not existing:
            revision_number = 1
            revision_id = "revision-vion-1"
            await store.insert_revision(
                revision_id=revision_id,
                agent_id=agent["agent_id"],
                revision_number=revision_number,
                schema_version=1,
                graph_json=graph_payload,
                semantic_hash=semantic_hash(graph),
                layout_hash=layout_hash(graph),
                dependency_lock={},
                parent_revision_id=None,
                created_by="bootstrap",
            )
            await store.record_revision_event(
                revision_id=revision_id,
                agent_id=agent["agent_id"],
                event="revision.activated",
                actor_user_id="bootstrap",
                data={"reason": "bootstrap", "alias": VION_ALIAS},
            )
        else:
            revision_id = existing[0]["revision_id"]

        if not agent.get("active_revision_id"):
            await store.set_active_revision(agent["agent_id"], revision_id)
        refreshed = await store.get_agent(agent["agent_id"])
        if refreshed is None:
            raise RuntimeError("Failed to reload bootstrapped agent")
        return {
            "agent_id": refreshed["agent_id"],
            "revision_id": refreshed["active_revision_id"],
            "alias": VION_ALIAS,
        }
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="")
    args = parser.parse_args()
    database_url = args.database_url
    if not database_url:
        from dotenv import dotenv_values

        env = dotenv_values(REPO_ROOT / ".env")
        database_url = str(env.get("DATABASE_URL") or "")

    from assistant.settings import Settings

    settings = Settings()
    if not settings.designer_enabled:
        print("DESIGNER_ENABLED=false: bootstrap refused (Safety note 1). "
              "Flip the flag or run migrations explicitly.")
        return 1

    result = asyncio.run(
        bootstrap(
            database_url,
            cua_enabled=settings.cua_enabled,
            model_provider=settings.model_provider,
            model_name=settings.model_name,
        )
    )
    print(f"vion bootstrapped: agent={result['agent_id']} "
          f"revision={result['revision_id']} alias={result['alias']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
