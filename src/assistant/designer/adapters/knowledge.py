"""Knowledge retrieval adapter (Fix 2 — a hard execution contract).

Rules enforced by this module:

- A Knowledge node is only meaningful with a **verified** retrieval
  contract. Until the P0 live probe records the actual Open WebUI
  retrieval route, the adapter ships BLOCKED ("Runtime adapter
  unverified"): ``retrieve`` raises, catalog entries carry
  capability_status=BLOCKED, and graph validation fails any graph that
  CONNECTS a knowledge node (activation therefore fails for that node).
- Disconnected knowledge nodes are inert by construction: the compiler
  (P5) mounts the retrieval tool only for connected+executable nodes, so
  disconnection means zero retrieval calls and zero injected context on
  every path.
- ``retrieve`` is the ONLY retrieval surface; it always carries the
  acting user's identity so upstream authorization applies (a shared
  admin token must never turn private Workspace content into a global
  catalog, R09).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from assistant.designer.errors import DesignerError


@dataclass(frozen=True)
class KnowledgeChunk:
    text: str
    source_id: str
    location: str
    score: float | None


class KnowledgeSource(Protocol):
    """The Designer-wide retrieval contract (frozen plan Fix 2)."""

    capability_status: str  # EXECUTABLE | BLOCKED | ...
    health_status: str

    async def retrieve(
        self,
        *,
        knowledge_id: str,
        query: str,
        user_id: str,
        top_k: int,
        score_threshold: float | None,
    ) -> list[KnowledgeChunk]: ...


class UnverifiedKnowledgeSource:
    """The BLOCKED placeholder used until the live contract is verified."""

    capability_status = "BLOCKED"
    health_status = "UNKNOWN"
    reason = "Runtime adapter unverified"

    async def retrieve(
        self,
        *,
        knowledge_id: str,
        query: str,
        user_id: str,
        top_k: int,
        score_threshold: float | None,
    ) -> list[KnowledgeChunk]:
        raise DesignerError(
            "permission_denied",
            "Knowledge retrieval is BLOCKED: the Open WebUI retrieval contract "
            "is not verified for this deployment (run scripts/probe_openwebui_contract.py)",
        )


class OpenWebUIKnowledgeSource:
    """Contract-first retrieval implementation.

    Instantiated ONLY once ``docs/designer/upstream-contracts.json`` marks
    ``knowledge_retrieval_adapter.status == "EXECUTABLE"`` (set by the P0
    probe + contract test). The retrieval route below is the pinned-source
    candidate and MUST be confirmed by the probe before this class is
    ever constructed with ``verified=True``.
    """

    capability_status = "BLOCKED"
    health_status = "UNKNOWN"

    def __init__(self, client_factory: Any, *, verified: bool) -> None:
        self._client_factory = client_factory
        self._verified = verified
        if verified:
            self.capability_status = "EXECUTABLE"
            self.health_status = "UNKNOWN"  # until first live probe

    async def retrieve(
        self,
        *,
        knowledge_id: str,
        query: str,
        user_id: str,
        top_k: int,
        score_threshold: float | None,
    ) -> list[KnowledgeChunk]:
        if not self._verified:
            return await UnverifiedKnowledgeSource().retrieve(
                knowledge_id=knowledge_id, query=query, user_id=user_id,
                top_k=top_k, score_threshold=score_threshold,
            )
        client = self._client_factory()
        response = await client.request(
            "POST",
            f"/api/v1/knowledge/{knowledge_id}/query",
            json={
                "query": query,
                "top_k": top_k,
                "score_threshold": score_threshold,
                "user_id": user_id,
            },
        )
        if response.status_code == 403:
            raise DesignerError("permission_denied", "upstream denied knowledge access")
        if response.status_code != 200:
            raise DesignerError(
                "upstream_unavailable",
                f"knowledge retrieval returned {response.status_code}",
            )
        payload: list[dict[str, Any]] = response.json()
        return [
            KnowledgeChunk(
                text=str(item.get("text", "")),
                source_id=str(item.get("source_id", knowledge_id)),
                location=str(item.get("location", "")),
                score=item.get("score"),
            )
            for item in payload
        ]
