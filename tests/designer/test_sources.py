"""P3 tests: Open WebUI source adapters (R03, R07, R08 + Fix 2 + Clar 4).

All upstream interactions run through recorded contract fixtures (respx)
— no real Open WebUI is contacted. The live probe (P0, owner account
pending) verifies these contracts; until then live behavior is explicitly
unverified.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest

from assistant.designer.adapters.knowledge import UnverifiedKnowledgeSource
from assistant.designer.adapters.sources import (
    SourceService,
    content_hash,
    list_builtin_skills,
    read_builtin_skill,
)
from assistant.designer.errors import DesignerError


class FakeActor:
    def __init__(self, user_id: str, session_id_hash: str = "hash") -> None:
        self.user_id = user_id
        self.session_id_hash = session_id_hash
        self.permissions = frozenset(
            {"designer.view", "designer.edit", "designer.credentials"}
        )

    def has(self, permission: str) -> bool:
        return permission in self.permissions


# ---------------------------------------------------------------------------
# Upstream fixtures (contract-first; probe verifies later)
# ---------------------------------------------------------------------------

UPSTREAM_SKILLS = [
    {"id": "skill-ext-1", "name": "Deploy Helper", "description": "deploys"},
]
UPSTREAM_PROMPTS = [
    {"id": "prompt-ext-1", "title": "Summarize", "description": "summarizes"},
]
UPSTREAM_KNOWLEDGE = [
    {"id": "knowledge-ext-1", "name": "Team Handbook", "description": "handbook"},
]
UPSTREAM_SKILL_DETAIL = {
    "id": "skill-ext-1",
    "name": "Deploy Helper",
    "description": "deploys",
    "meta": {"content": "deploy steps"},
}


@contextmanager
def upstream(
    denied: bool = False,
) -> Iterator[tuple[SourceService, dict[str, Any]]]:
    """Yield (service, named routes) against a respx-mocked upstream.

    Contract shapes follow the pinned v0.11.3 source; the P0 probe
    verifies them live before any live gate is claimed.
    """
    import respx

    from assistant.designer.adapters.openwebui import OpenWebUIClient

    router = respx.mock(base_url="http://upstream.test", assert_all_called=False)

    def deny(request: Any) -> httpx.Response:
        return httpx.Response(403, json={"detail": "denied"})

    routes: dict[str, Any] = {}
    routes["skills_list"] = router.get("http://upstream.test/api/v1/skills/").mock(
        side_effect=(
            deny if denied else lambda r: httpx.Response(200, json=UPSTREAM_SKILLS)
        )
    )
    routes["prompts_list"] = router.get("http://upstream.test/api/v1/prompts/").mock(
        side_effect=(
            deny if denied else lambda r: httpx.Response(200, json=UPSTREAM_PROMPTS)
        )
    )
    routes["knowledge_list"] = router.get(
        "http://upstream.test/api/v1/knowledge/"
    ).mock(
        side_effect=(
            deny if denied else lambda r: httpx.Response(200, json=UPSTREAM_KNOWLEDGE)
        )
    )
    routes["skill_get"] = router.get(
        "http://upstream.test/api/v1/skills/skill-ext-1"
    ).mock(
        side_effect=(
            deny if denied else lambda r: httpx.Response(200, json=UPSTREAM_SKILL_DETAIL)
        )
    )
    routes["create"] = router.post("http://upstream.test/api/v1/skills/create").mock(
        side_effect=deny if denied else lambda r: httpx.Response(
            200, json={"id": "skill-new-1"}
        )
    )
    routes["update"] = router.post(
        "http://upstream.test/api/v1/skills/skill-ext-1/update"
    ).mock(
        side_effect=deny if denied else lambda r: httpx.Response(
            200, json={"id": "skill-ext-1"}
        )
    )
    router.start()
    try:

        def client_for_actor(actor: FakeActor) -> OpenWebUIClient:
            # The acting user's own credential is resolved server-side;
            # the fixture token stands in for the decrypted upstream token.
            return OpenWebUIClient("http://upstream.test", "token-for-user")

        service = SourceService(client_for_actor, UnverifiedKnowledgeSource())
        yield service, routes
    finally:
        router.stop()


# ---------------------------------------------------------------------------
# Catalog with dual status dimensions (Clar 4)
# ---------------------------------------------------------------------------


async def test_skill_catalog_lists_builtin_and_upstream() -> None:
    with upstream() as (service, _routes):
        entries = await service.catalog(FakeActor("u1"), kind="skill")
    gateway_ids = [e["id"] for e in entries if e["source"] == "gateway"]
    upstream_ids = [e["id"] for e in entries if e["source"] == "openwebui"]
    assert "gateway:general-assistant" in gateway_ids
    assert "openwebui:skill-ext-1" in upstream_ids
    # Built-ins are read-only; upstream skills are editable in place (R07).
    builtin = next(e for e in entries if e["id"] == "gateway:general-assistant")
    assert builtin["can_write"] is False
    editable = next(e for e in entries if e["id"] == "openwebui:skill-ext-1")
    assert editable["can_write"] is True


async def test_knowledge_catalog_is_blocked_until_verified() -> None:
    with upstream() as (service, _routes):
        entries = await service.catalog(FakeActor("u1"), kind="knowledge")
    assert entries, "metadata listing still works"
    for entry in entries:
        assert entry["capability_status"] == "BLOCKED"
        assert "unverified" in entry["provenance"]["blocked_reason"]


async def test_prompt_catalog_normalizes_upstream() -> None:
    with upstream() as (service, _routes):
        entries = await service.catalog(FakeActor("u1"), kind="prompt")
    assert entries[0]["id"] == "openwebui:prompt-ext-1"
    assert entries[0]["name"] == "Summarize"


async def test_model_presets_are_catalog_only() -> None:
    with upstream() as (service, _routes):
        entries = await service.catalog(FakeActor("u1"), kind="model")
    assert entries[0]["capability_status"] == "CATALOG_ONLY"


# ---------------------------------------------------------------------------
# Upstream denial is surfaced, never hidden (plan P3 gate)
# ---------------------------------------------------------------------------


async def test_upstream_denial_is_not_hidden() -> None:
    with upstream(denied=True) as (service, _routes):
        with pytest.raises(DesignerError):
            await service.catalog(FakeActor("u1"), kind="skill")
        with pytest.raises(DesignerError):
            await service.catalog(FakeActor("u1"), kind="knowledge")


# ---------------------------------------------------------------------------
# Skill authoring stays in Open WebUI (R07, acceptance A5 direction)
# ---------------------------------------------------------------------------


async def test_create_skill_writes_to_openwebui() -> None:
    with upstream() as (service, routes):
        result = await service.create_skill(
            FakeActor("u1"), name="My Skill", description="d", content="steps"
        )
        assert result["source"] == "openwebui"
        assert result["id"] == "openwebui:skill-new-1"
        assert routes["create"].called


async def test_update_skill_conflict_detection() -> None:
    current_hash = content_hash(UPSTREAM_SKILL_DETAIL)
    with upstream() as (service, routes):
        # Correct hash: update goes through.
        updated = await service.update_skill(
            FakeActor("u1"),
            skill_id="openwebui:skill-ext-1",
            name="Deploy Helper",
            description="deploys",
            content="new content",
            expected_hash=current_hash,
        )
        assert updated["updated"] is True
        assert routes["update"].called
        # Stale hash: explicit 409, no blind overwrite (R07).
        with pytest.raises(DesignerError) as excinfo:
            await service.update_skill(
                FakeActor("u1"),
                skill_id="openwebui:skill-ext-1",
                name="Deploy Helper",
                description="deploys",
                content="new content",
                expected_hash="stale-hash",
            )
    assert excinfo.value.code == "conflict"


async def test_builtin_gateway_skill_is_readonly_and_copyable() -> None:
    entries = list_builtin_skills()
    assert entries, "gateway ships read-only skills"
    content = read_builtin_skill("gateway:general-assistant")
    assert content and content.startswith("---")
    # Copy creates a NEW Open WebUI skill after explicit action (R07).
    with upstream() as (service, routes):
        copied = await service.copy_builtin_skill(
            FakeActor("u1"), builtin_id="gateway:general-assistant", name="My Copy"
        )
        assert copied["source"] == "openwebui"
        assert routes["create"].called


# ---------------------------------------------------------------------------
# Knowledge adapter: BLOCKED means zero retrieval, ever (Fix 2)
# ---------------------------------------------------------------------------


async def test_knowledge_retrieval_blocked_until_verified() -> None:
    source = UnverifiedKnowledgeSource()
    with pytest.raises(DesignerError) as excinfo:
        await source.retrieve(
            knowledge_id="knowledge-ext-1", query="q", user_id="u1",
            top_k=5, score_threshold=None,
        )
    assert "BLOCKED" in excinfo.value.message


async def test_connected_knowledge_node_fails_validation() -> None:
    from tests.designer.test_graph import edge, make_graph, node

    from assistant.designer.schemas import parse_graph_document
    from assistant.designer.validation import validate_graph

    graph = make_graph()
    graph["nodes"].append(node("knowledge", "1"))
    graph["edges"].append(edge("agent-root", "knowledge-1", "knowledge"))
    report = validate_graph(parse_graph_document(graph))
    assert not report.ok
    assert any(i.code == "capability_blocked" for i in report.issues)


async def test_disconnected_knowledge_node_is_allowed_but_inert() -> None:
    from tests.designer.test_graph import make_graph, node

    from assistant.designer.schemas import parse_graph_document
    from assistant.designer.validation import validate_graph

    graph = make_graph()
    graph["nodes"].append(node("knowledge", "1"))
    report = validate_graph(parse_graph_document(graph))
    assert report.ok, report.to_dict()  # allowed on canvas
    # and the adapter refuses retrieval regardless (Fix 2).
    source = UnverifiedKnowledgeSource()
    with pytest.raises(DesignerError):
        await source.retrieve(
            knowledge_id="x", query="q", user_id="u1", top_k=1, score_threshold=None
        )
