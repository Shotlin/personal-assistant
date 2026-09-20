"""Normalized source layer over the adapters (R03, R07, Clar 4).

Every catalog entry carries BOTH dimensions separately:
``capability_status`` (what the resource may ever do) and
``health_status`` (whether it can right now). Native Open WebUI Python
plugins are CATALOG_ONLY; Knowledge before contract verification is
BLOCKED; a transient outage never changes the capability class.

Open WebUI stays the authoring owner (R07): create/update write through
the user's authorized upstream API. The upstream has no transactional
conditional update, so update does explicit conflict detection
(read-compare-write) instead of pretending CAS exists.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.designer.adapters.knowledge import UnverifiedKnowledgeSource
from assistant.designer.adapters.openwebui import OpenWebUIClient
from assistant.designer.errors import DesignerError

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


def content_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class CatalogEntry:
    source: str  # "openwebui" | "gateway"
    kind: str
    id: str
    name: str
    description: str = ""
    capability_status: str = "EXECUTABLE"
    health_status: str = "UNKNOWN"
    can_read: bool = True
    can_write: bool = False
    provenance: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "kind": self.kind, "id": self.id,
            "name": self.name, "description": self.description,
            "capability_status": self.capability_status,
            "health_status": self.health_status,
            "can_read": self.can_read, "can_write": self.can_write,
            "provenance": self.provenance or {},
        }


def list_builtin_skills(skills_root: Path = SKILLS_ROOT) -> list[CatalogEntry]:
    """Developer-authored gateway skills: read-only, copyable (R07)."""
    entries: list[CatalogEntry] = []
    if not skills_root.exists():
        return entries
    for skill_dir in sorted(skills_root.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        text = skill_file.read_text(encoding="utf-8")
        name = skill_dir.name
        description = _frontmatter_field(text, "description") or ""
        entries.append(
            CatalogEntry(
                source="gateway",
                kind="skill",
                id=f"gateway:{name}",
                name=name,
                description=description,
                capability_status="EXECUTABLE",
                health_status="ONLINE",
                can_read=True,
                can_write=False,  # never write into application source
                provenance={"content_hash": content_hash(text)},
            )
        )
    return entries


def _frontmatter_field(text: str, field: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    for line in text[3:end].splitlines():
        key, _, value = line.partition(":")
        if key.strip() == field:
            return value.strip()
    return None


def read_builtin_skill(skill_id: str, skills_root: Path = SKILLS_ROOT) -> str | None:
    if not skill_id.startswith("gateway:"):
        return None
    name = skill_id.split(":", 1)[1]
    skill_file = skills_root / name / "SKILL.md"
    if not skill_file.is_file():
        return None
    return skill_file.read_text(encoding="utf-8")


class SourceService:
    """Catalog + authorized read/write against Open WebUI sources."""

    def __init__(self, client_for_actor: Any, knowledge: Any | None = None) -> None:
        # client_for_actor: callable(actor) -> OpenWebUIClient (credential
        # resolved server-side; never exposed to the browser).
        self._client_for_actor = client_for_actor
        self._knowledge = knowledge or UnverifiedKnowledgeSource()

    @property
    def knowledge_source(self) -> Any:
        return self._knowledge

    async def catalog(self, actor: Any, kind: str) -> list[dict[str, Any]]:
        if kind == "skill":
            return await self._skill_catalog(actor)
        if kind == "prompt":
            return await self._prompt_catalog(actor)
        if kind == "knowledge":
            return await self._knowledge_catalog(actor)
        if kind == "model":
            return self._model_catalog()
        if kind == "memory":
            return self._memory_catalog()
        if kind == "context":
            return self._context_catalog()
        if kind == "cua":
            return self._cua_catalog()
        if kind == "mcp":
            return await self._mcp_catalog(actor)
        if kind == "tool":
            return self._tool_catalog()
        raise DesignerError("invalid_request", f"unknown catalog kind {kind!r}")

    async def _skill_catalog(self, actor: Any) -> list[dict[str, Any]]:
        entries = [entry.to_dict() for entry in list_builtin_skills()]
        client: OpenWebUIClient = self._client_for_actor(actor)
        upstream = await client.list_skills()
        for item in upstream:
            entries.append(
                CatalogEntry(
                    source="openwebui",
                    kind="skill",
                    id=f"openwebui:{item.get('id', '')}",
                    name=str(item.get("name", "")),
                    description=str(item.get("description", "")),
                    capability_status="EXECUTABLE",
                    health_status="ONLINE",
                    can_read=True,
                    can_write=True,  # authoring stays in Open WebUI
                    provenance={"external_id": item.get("id")},
                ).to_dict()
            )
        return entries

    async def _prompt_catalog(self, actor: Any) -> list[dict[str, Any]]:
        client: OpenWebUIClient = self._client_for_actor(actor)
        upstream = await client.list_prompts()
        return [
            CatalogEntry(
                source="openwebui",
                kind="prompt",
                id=f"openwebui:{item.get('id', '')}",
                name=str(item.get("title", item.get("command", ""))),
                description=str(item.get("description", "")),
                capability_status="EXECUTABLE",
                health_status="ONLINE",
                can_read=True,
                can_write=True,
                provenance={"external_id": item.get("id")},
            ).to_dict()
            for item in upstream
        ]

    async def _knowledge_catalog(self, actor: Any) -> list[dict[str, Any]]:
        client: OpenWebUIClient = self._client_for_actor(actor)
        upstream = await client.list_knowledge()
        capability = self._knowledge.capability_status
        return [
            CatalogEntry(
                source="openwebui",
                kind="knowledge",
                id=f"openwebui:{item.get('id', '')}",
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
                capability_status=capability,
                health_status=self._knowledge.health_status,
                can_read=True,
                can_write=False,
                provenance={
                    "external_id": item.get("id"),
                    "blocked_reason": (
                        self._knowledge.reason
                        if capability == "BLOCKED"
                        else ""
                    ),
                },
            ).to_dict()
            for item in upstream
        ]

    async def create_skill(
        self, actor: Any, *, name: str, description: str, content: str
    ) -> dict[str, Any]:
        """Create an Open WebUI-owned custom skill (text only, R07)."""
        if not name.strip():
            raise DesignerError("invalid_request", "skill name must not be empty")
        client: OpenWebUIClient = self._client_for_actor(actor)
        created = await client.create_skill(
            {
                "name": name.strip(),
                "description": description.strip(),
                # Open WebUI v0.11.x skills carry markdown content; the
                # Designer never ships executable script bodies.
                "meta": {"content": content},
            }
        )
        return {
            "source": "openwebui",
            "kind": "skill",
            "id": f"openwebui:{created.get('id', '')}",
            "name": name.strip(),
            "provenance": {"external_id": created.get("id")},
        }

    async def update_skill(
        self,
        actor: Any,
        *,
        skill_id: str,
        name: str,
        description: str,
        content: str,
        expected_hash: str,
    ) -> dict[str, Any]:
        """Read-compare-write update with explicit conflict detection.

        The upstream API has no transactional conditional update, so we
        fetch, hash, compare and only then write — a concurrent change
        yields 409 instead of a blind overwrite (R07).
        """
        if not skill_id.startswith("openwebui:"):
            raise DesignerError(
                "permission_denied", "only Open WebUI-owned skills can be edited"
            )
        external_id = skill_id.split(":", 1)[1]
        client: OpenWebUIClient = self._client_for_actor(actor)
        current = await client.get_skill(external_id)
        if current is None:
            raise DesignerError("missing", "skill no longer exists upstream")
        current_hash = content_hash(current)
        if current_hash != expected_hash:
            raise DesignerError(
                "conflict",
                "skill changed upstream since it was loaded; refresh and retry",
            )
        await client.update_skill(
            external_id,
            {
                "name": name.strip(),
                "description": description.strip(),
                "meta": {"content": content},
            },
        )
        return {"source": "openwebui", "kind": "skill", "id": skill_id,
                "updated": True}

    async def copy_builtin_skill(
        self, actor: Any, *, builtin_id: str, name: str
    ) -> dict[str, Any]:
        """Copy a read-only gateway skill into an Open WebUI custom skill
        after the explicit user action (R07). Never writes app source."""
        content = read_builtin_skill(builtin_id)
        if content is None:
            raise DesignerError("missing", "built-in skill not found")
        return await self.create_skill(
            actor, name=name, description=f"Copied from {builtin_id}", content=content
        )

    # --- real-runtime catalogs (Fix: never demo content) ---

    def _model_catalog(self) -> list[dict[str, Any]]:
        """The ACTUAL configured model from the operator settings -- the
        only model that genuinely serves runtimes today. Custom
        providers stay CATALOG_ONLY until the credential/connection flow
        is verified (Fix 10)."""
        from assistant.settings import Settings

        settings = Settings()
        entries = [
            CatalogEntry(
                source="gateway",
                kind="model",
                id=f"operator-model:{settings.model_provider}:{settings.model_name}",
                name=f"{settings.model_name} (operator)",
                description=(
                    f"Actual configured model: {settings.model_name} via "
                    f"{settings.model_provider}, credential operator-env"
                ),
                capability_status="EXECUTABLE",
                health_status="ONLINE",
                can_read=True,
                can_write=False,
                provenance={
                    "provider": settings.model_provider,
                    "model_id": settings.model_name,
                    "credential_ref": "operator-env",
                },
            ).to_dict()
        ]
        entries.append(
            CatalogEntry(
                source="gateway",
                kind="model",
                id="custom-provider",
                name="Custom Provider / API endpoint",
                description=(
                    "Bring-your-own OpenAI-compatible endpoint; requires the "
                    "verified credential + connection flow (Fix 10)"
                ),
                capability_status="CATALOG_ONLY",
                health_status="UNKNOWN",
                can_read=True,
                can_write=False,
                provenance={"reason": "credential/connection flow unverified"},
            ).to_dict()
        )
        return entries

    def _memory_catalog(self) -> list[dict[str, Any]]:
        """The actual memory kinds the runtime implements (R15)."""
        return [
            CatalogEntry(
                source="gateway", kind="memory", id="memory-thread",
                name="Thread Memory",
                description="Per-chat conversation checkpoints (LangGraph thread)",
                capability_status="EXECUTABLE", health_status="ONLINE",
                provenance={"kind": "thread"},
            ).to_dict(),
            CatalogEntry(
                source="gateway", kind="memory", id="memory-user",
                name="User Memory",
                description="Long-term per-user memory (secret-screened store)",
                capability_status="EXECUTABLE", health_status="ONLINE",
                provenance={"kind": "user"},
            ).to_dict(),
        ]

    def _context_catalog(self) -> list[dict[str, Any]]:
        """The actual context policy the compiler enforces (R16 defaults)."""
        from assistant.designer.compiler import CONTEXT_DEFAULTS

        d = CONTEXT_DEFAULTS
        return [
            CatalogEntry(
                source="gateway", kind="context", id="context-default",
                name="Default Policy",
                description=(
                    f"{d['recent_turns']} turns, {d['estimated_input_tokens']} est input, "
                    f"{d['output_token_cap']} output cap, {d['max_tool_calls']} tool calls, "
                    f"{d['run_wall_clock_seconds'] // 60} min run"
                ),
                capability_status="EXECUTABLE", health_status="ONLINE",
                provenance={"policy": dict(d)},
            ).to_dict(),
        ]

    def _cua_catalog(self) -> list[dict[str, Any]]:
        """The real CUA Driver connector, reflecting the actual posture."""
        from assistant.settings import Settings

        settings = Settings()
        enabled = settings.cua_enabled
        return [
            CatalogEntry(
                source="gateway", kind="cua", id="cua-local",
                name="CUA Desktop Driver",
                description=(
                    "Bounded desktop automation (manifest-gated, driver "
                    f"v{settings.cua_command})"
                    if enabled
                    else "Bounded desktop automation; currently DISABLED in "
                    "operator settings (CUA_ENABLED=false)"
                ),
                capability_status="EXECUTABLE" if enabled else "CATALOG_ONLY",
                health_status="ONLINE" if enabled else "OFFLINE",
                can_read=True,
                can_write=False,
                provenance={
                    "connector_id": "cua-local",
                    "permission_mode": settings.cua_permission_mode,
                    "enabled": enabled,
                },
            ).to_dict()
        ]

    async def _mcp_catalog(self, actor: Any) -> list[dict[str, Any]]:
        """Registered MCP connectors: the CUA driver's persistent stdio
        connection plus any operator-registered connections. Empty when
        none are registered -- never a fabricated list."""
        entries: list[dict[str, Any]] = []
        rows = []
        getter = getattr(self, "_list_connections", None)
        if getter is not None:
            rows = await getter()
        for row in rows:
            entries.append(
                CatalogEntry(
                    source="gateway", kind="mcp",
                    id=f"mcp:{row['connector_id']}",
                    name=str(row.get("label") or row["connector_id"]),
                    description=str(row.get("purpose") or "registered connector"),
                    capability_status=(
                        "EXECUTABLE" if row.get("status") == "active" else "BLOCKED"
                    ),
                    health_status="ONLINE" if row.get("status") == "active" else "OFFLINE",
                    can_read=True, can_write=False,
                    provenance={"connector_id": row["connector_id"]},
                ).to_dict()
            )
        return entries

    def _tool_catalog(self) -> list[dict[str, Any]]:
        """The actual tool allowlist: the CUA tools the bounded policy
        really exposes, plus the sandbox-gated Terminal (BLOCKED)."""
        from assistant.tools.policy import CUA_ALLOWED_TOOL_NAMES

        entries = [
            CatalogEntry(
                source="gateway", kind="tool", id=f"tool:{name}",
                name=name,
                description="CUA allowlist tool exposed to the bounded driver",
                capability_status="EXECUTABLE", health_status="ONLINE",
                provenance={"allowlist": "cua"},
            ).to_dict()
            for name in sorted(CUA_ALLOWED_TOOL_NAMES)
        ]
        entries.append(
            CatalogEntry(
                source="gateway", kind="tool", id="tool:terminal",
                name="Terminal Command",
                description=(
                    "Shell execution; BLOCKED until an operator-provisioned "
                    "sandbox is tested (A12)"
                ),
                capability_status="BLOCKED", health_status="UNKNOWN",
                provenance={"reason": "sandbox adapter untested"},
            ).to_dict()
        )
        return entries
