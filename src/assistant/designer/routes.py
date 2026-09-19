"""/designer/api/v1 session routes (P1).

Authentication surface only. Mounted exclusively when
``DESIGNER_ENABLED=true`` (C2): the flag-off gateway never serves these
routes, never validates Designer-only settings, never starts Designer
services (Safety note 1).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from assistant.designer import audit
from assistant.designer.auth import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    Actor,
    LoginRateLimiter,
    SessionManager,
    UpstreamAuthAdapter,
    permissions_for_role,
    require_permission,
)
from assistant.designer.credentials import CredentialRef, CredentialStore
from assistant.designer.errors import DesignerError
from assistant.designer.validation import layout_hash, semantic_hash

router = APIRouter(prefix="/designer/api/v1")


def install_error_handler(app: FastAPI) -> None:
    """Translate DesignerError into the stable Designer error envelope."""

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(DesignerError)
    async def _designer_error_handler(request: Request, exc: DesignerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"error": {"code": exc.code, "message": exc.message}}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": {"error": {"code": "invalid_request",
                                           "message": "malformed request payload"}}},
        )


def _designer_state(request: Request) -> dict[str, Any]:
    designer = getattr(request.app.state, "designer", None)
    if designer is None:
        raise DesignerError("session_required", "Designer is not enabled on this gateway")
    return designer


async def resolve_actor(request: Request) -> Actor:
    """Resolve the Designer Actor from the session cookie (server-verified)."""
    designer = _designer_state(request)
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    return await designer["sessions"].resolve(raw_cookie)


async def require_actor_and_csrf(request: Request) -> Actor:
    """Dependency for every mutating route: valid session + CSRF match."""
    actor = await resolve_actor(request)
    designer = _designer_state(request)
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    await designer["sessions"].check_csrf(
        actor, raw_cookie, request.headers.get(CSRF_HEADER_NAME, "")
    )
    return actor


class SessionConnectRequest(BaseModel):
    """One-time upstream credential exchange (R09). The plaintext secret
    is accepted once, verified server-side, stored encrypted, and never
    returned nor persisted in plaintext."""

    model_config = {"extra": "ignore"}

    mode: str
    email: str = ""
    password: str = ""
    api_key: str = ""


@router.post("/session")
async def connect(request: Request, body: SessionConnectRequest) -> Any:
    designer = _designer_state(request)

    # Rate limiting keys off the presented identity, not its secret.
    identity_key = body.email or f"key:{hash(body.api_key) & 0xFFFFFFFF:08x}"
    limiter: LoginRateLimiter = designer["limiter"]
    limiter.check(identity_key)
    limiter.record(identity_key)

    adapter: UpstreamAuthAdapter = designer["upstream"]
    verified = await adapter.verify(
        mode=body.mode, email=body.email, password=body.password, api_key=body.api_key
    )
    if not verified.get("user_id"):
        raise DesignerError("upstream_unavailable", "Open WebUI did not verify identity")

    credential_store: CredentialStore = designer["credentials"]
    ref = await credential_store.store(
        owner_user_id=str(verified["user_id"]),
        purpose="openwebui-designer-session",
        kind=str(verified["credential_kind"]),
        plaintext=str(verified.pop("upstream_token")),
    )
    explicit = await designer["store"].list_grants(str(verified["user_id"]))
    permissions = permissions_for_role(str(verified["role"]), explicit)
    sessions: SessionManager = designer["sessions"]
    tokens = await sessions.create(
        user_id=str(verified["user_id"]),
        role=str(verified["role"]),
        auth_mode=body.mode,
        credential_id=ref.credential_id,
    )
    await audit.record(
        designer["store"],
        actor_user_id=str(verified["user_id"]),
        event="session.created",
        subject={"auth_mode": body.mode, "credential_id": ref.credential_id},
    )
    # The upstream token and the submitted password never appear in any
    # response; only the session cookie (real Set-Cookie) + CSRF token.
    response = JSONResponse(
        status_code=200,
        content={
            "user_id": str(verified["user_id"]),
            "role": str(verified["role"]),
            "permissions": sorted(permissions),
            "csrf_token": tokens["csrf_token"],
            "session_ttl_seconds": SESSION_TTL_SECONDS,
        },
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=tokens["session_token"],
        httponly=True,
        samesite="lax",
        secure=designer["sessions"].cookie_secure,
        path="/",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


@router.get("/session")
async def inspect_session(request: Request) -> dict[str, Any]:
    actor = await resolve_actor(request)
    return {
        "user_id": actor.user_id,
        "role": actor.role,
        "permissions": sorted(actor.permissions),
    }


@router.delete("/session")
async def disconnect(request: Request) -> Any:
    """Revoke the Designer session and delete the local encrypted upstream
    credential reference (Clarification 2).

    - session token: best-effort verified upstream signout first.
    - api_key / unverified kinds: local removal only; the upstream key is
      NEVER touched by logout.
    """
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    # Logout is a mutation: CSRF applies here too.
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    await designer["sessions"].check_csrf(
        actor, raw_cookie, request.headers.get(CSRF_HEADER_NAME, "")
    )
    session = await designer["store"].load_session(actor.session_id_hash)

    upstream_revoked = False
    credential_kind = "none"
    credential_id = (
        str(session["credential_id"])
        if session and session.get("credential_id")
        else ""
    )
    if credential_id:
        credential_store: CredentialStore = designer["credentials"]
        row = await designer["store"].load_credential(credential_id)
        if row is not None and str(row["status"]) == "active":
            credential_kind = str(row["kind"])
            ref = CredentialRef(
                credential_id=credential_id,
                generation=int(row["generation"]),
                purpose=str(row["purpose"]),
            )
            if credential_kind == "session_token":
                try:
                    plaintext = await credential_store.resolve_plaintext(
                        actor_user_id=actor.user_id, ref=ref
                    )
                    adapter: UpstreamAuthAdapter = designer["upstream"]
                    upstream_revoked = await adapter.signout_session_token(plaintext)
                except (LookupError, PermissionError):
                    upstream_revoked = False
            # api_key + unknown kinds fall through: local invalidation only.
            await credential_store.revoke(actor_user_id=actor.user_id, ref=ref)

    await designer["store"].revoke_session(actor.session_id_hash)
    await audit.record(
        designer["store"],
        actor_user_id=actor.user_id,
        event="session.revoked",
        subject={"upstream_revoked": upstream_revoked, "kind": credential_kind},
    )
    response = JSONResponse(
        status_code=200,
        content={
            "revoked": True,
            "upstream_revocation": (
                "revoked"
                if upstream_revoked
                else "not supported/not verified for this credential kind"
            ),
        },
    )
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


# ---------------------------------------------------------------------------
# Agents & revisions (P2): registry + immutable drafts with ETag CAS.
# ---------------------------------------------------------------------------


class AgentCreateRequest(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    description: str = ""


def _slugify(name: str) -> str:
    import re as _re

    slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "agent"


async def _agent_for_actor(designer: dict[str, Any], actor: Actor, agent_id: str) -> dict[str, Any]:
    """Load an agent and enforce per-actor visibility (C1).

    Existence of other users' agents is not disclosed: unauthorized access
    is indistinguishable from a missing row.
    """
    agent = await designer["store"].get_agent(agent_id)
    if agent is None or (
        agent["owner_user_id"] != actor.user_id and not actor.has("designer.admin")
    ):
        raise DesignerError("missing", "agent not found")
    return agent


def _agent_etag(agent: dict[str, Any]) -> str:
    return f'W/"{agent["row_version"]}"'


@router.get("/agents")
async def list_agents(request: Request) -> dict[str, Any]:
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    agents = await designer["store"].list_agents(actor.user_id)
    return {
        "agents": [
            {**agent, "etag": _agent_etag(agent)} for agent in agents
        ]
    }


@router.post("/agents")
async def create_agent(request: Request, body: AgentCreateRequest) -> dict[str, Any]:
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    require_permission(actor, "designer.edit", {"action": "agent.create"})
    if not body.name.strip():
        raise DesignerError("invalid_request", "agent name must not be empty")
    agent = await designer["store"].insert_agent(
        owner_user_id=actor.user_id,
        slug=_slugify(body.name) or "agent",
        display_name=body.name.strip(),
        description=body.description.strip(),
    )
    await audit.record(
        designer["store"],
        actor_user_id=actor.user_id,
        event="agent.created",
        subject={"agent_id": agent["agent_id"], "name": agent["display_name"]},
    )
    return {**agent, "etag": _agent_etag(agent)}


@router.get("/agents/{agent_id}")
async def get_agent(request: Request, agent_id: str) -> dict[str, Any]:
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    agent = await _agent_for_actor(designer, actor, agent_id)
    return {**agent, "etag": _agent_etag(agent)}


class RevisionSaveRequest(BaseModel):
    model_config = {"extra": "ignore"}

    graph: dict[str, Any]
    parent_revision_id: str | None = None


@router.post("/agents/{agent_id}/revisions")
async def save_revision(request: Request, agent_id: str, body: RevisionSaveRequest) -> Any:
    """Append an immutable draft revision (R12). Save NEVER activates:
    active_revision_id is untouched, and no external process starts."""
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    agent = await _agent_for_actor(designer, actor, agent_id)
    require_permission(actor, "designer.edit", {"agent_id": agent_id})

    # Optimistic concurrency: If-Match must carry the current row_version.
    if_match = request.headers.get("if-match", "")
    expected_version = _version_from_etag(if_match)
    if expected_version is None or expected_version != int(agent["row_version"]):
        raise DesignerError("conflict", "stale write: agent was modified concurrently")

    # Version check + deterministic validation only — no externals (Clar 1).
    from assistant.designer.schemas import UnsupportedSchemaError, parse_graph_document
    from assistant.designer.validation import validate_graph

    try:
        graph = parse_graph_document(body.graph)
    except UnsupportedSchemaError as exc:
        raise DesignerError("invalid_request", str(exc)) from exc
    report = validate_graph(graph)
    if not report.ok:
        return JSONResponse(
            status_code=400,
            content={"detail": {"error": {
                "code": "invalid_graph",
                "message": "graph validation failed",
                "validation": report.to_dict(),
            }}},
        )

    revision_number = await designer["store"].next_revision_number(agent_id)
    from assistant.designer.store import new_id

    revision_id = new_id("revision")
    new_row_version = await designer["store"].insert_revision(
        revision_id=revision_id,
        agent_id=agent_id,
        revision_number=revision_number,
        schema_version=graph.schema_version,
        graph_json=body.graph,
        semantic_hash=semantic_hash(graph),
        layout_hash=layout_hash(graph),
        dependency_lock={},
        parent_revision_id=body.parent_revision_id,
        created_by=actor.user_id,
    )
    await designer["store"].record_revision_event(
        revision_id=revision_id,
        agent_id=agent_id,
        event="draft.saved",
        actor_user_id=actor.user_id,
        data={"revision_number": revision_number},
    )
    await audit.record(
        designer["store"],
        actor_user_id=actor.user_id,
        event="draft.saved",
        subject={"agent_id": agent_id, "revision_id": revision_id},
    )
    return JSONResponse(
        status_code=201,
        content={
            "revision_id": revision_id,
            "agent_id": agent_id,
            "revision_number": revision_number,
            "semantic_hash": semantic_hash(graph),
            "layout_hash": layout_hash(graph),
            "row_version": new_row_version,
            "active_revision_id": agent["active_revision_id"],
        },
    )


def _version_from_etag(if_match: str) -> int | None:
    import re as _re

    match = _re.search(r'"(\d+)"', if_match)
    return int(match.group(1)) if match else None


@router.get("/agents/{agent_id}/revisions")
async def list_revisions(request: Request, agent_id: str) -> dict[str, Any]:
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    await _agent_for_actor(designer, actor, agent_id)
    revisions = await designer["store"].list_revisions(agent_id)
    return {"revisions": revisions}


@router.delete("/agents/{agent_id}")
async def archive_agent(request: Request, agent_id: str) -> dict[str, Any]:
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    agent = await _agent_for_actor(designer, actor, agent_id)
    require_permission(actor, "designer.edit", {"agent_id": agent_id})
    await designer["store"].archive_agent(agent_id)
    await audit.record(
        designer["store"],
        actor_user_id=actor.user_id,
        event="agent.archived",
        subject={"agent_id": agent_id, "slug": agent["slug"]},
    )
    return {"archived": True}


# ---------------------------------------------------------------------------
# Sources & catalog (P3): Open WebUI stays the authoring owner (R07).
# ---------------------------------------------------------------------------


@router.get("/catalog")
async def get_catalog(request: Request, kind: str = "skill") -> dict[str, Any]:
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    require_permission(actor, "designer.view", {"kind": kind})
    entries = await designer["sources"].catalog(actor, kind)
    return {"kind": kind, "entries": entries}


class SkillCreateRequest(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    description: str = ""
    content: str


class SkillUpdateRequest(BaseModel):
    model_config = {"extra": "ignore"}

    name: str
    description: str = ""
    content: str
    expected_hash: str


class SkillCopyRequest(BaseModel):
    model_config = {"extra": "ignore"}

    builtin_id: str
    name: str


@router.post("/resources/skills")
async def create_skill(request: Request, body: SkillCreateRequest) -> dict[str, Any]:
    """Create an Open WebUI-owned custom skill (R07: text only; the skill
    lives in Open WebUI afterwards — acceptance A5)."""
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    require_permission(actor, "designer.edit", {"resource": "skill.create"})
    return await designer["sources"].create_skill(
        actor, name=body.name, description=body.description, content=body.content
    )


@router.post("/resources/skills/{skill_id}/update")
async def update_skill(request: Request, skill_id: str, body: SkillUpdateRequest) -> dict[str, Any]:
    """Conflict-detected update: upstream has no transactional CAS, so the
    Designer does read-compare-write and 409s on concurrent change (R07)."""
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    require_permission(actor, "designer.edit", {"resource": "skill.update"})
    return await designer["sources"].update_skill(
        actor,
        skill_id=skill_id,
        name=body.name,
        description=body.description,
        content=body.content,
        expected_hash=body.expected_hash,
    )


@router.post("/resources/skills/copy-builtin")
async def copy_builtin_skill(request: Request, body: SkillCopyRequest) -> dict[str, Any]:
    """Explicit copy of a read-only gateway skill into a new Open WebUI
    custom skill (R07: never writes into application source)."""
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    require_permission(actor, "designer.edit", {"resource": "skill.copy"})
    return await designer["sources"].copy_builtin_skill(
        actor, builtin_id=body.builtin_id, name=body.name
    )
