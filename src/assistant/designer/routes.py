"""/designer/api/v1 session routes (P1).

Authentication surface only. Mounted exclusively when
``DESIGNER_ENABLED=true`` (C2): the flag-off gateway never serves these
routes, never validates Designer-only settings, never starts Designer
services (Safety note 1).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
    revisions = await designer["store"].list_revisions(agent_id)
    draft = None
    if revisions:
        latest = await designer["store"].get_revision(revisions[0]["revision_id"])
        if latest:
            draft = {
                "revision_id": latest["revision_id"],
                "revision_number": latest["revision_number"],
                "graph": latest["graph_json"],
                "row_version": agent["row_version"],
            }
    return {
        **agent,
        "etag": _agent_etag(agent),
        "revisions_count": len(revisions),
        "draft": draft,
    }


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


@router.get("/agents/{agent_id}/revisions/{revision_id}")
async def get_revision(request: Request, agent_id: str, revision_id: str) -> dict[str, Any]:
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    await _agent_for_actor(designer, actor, agent_id)
    rev = await designer["store"].get_revision(revision_id)
    if rev is None or str(rev["agent_id"]) != agent_id:
        raise DesignerError("missing", "revision not found")
    return rev


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


class ValidateGraphRequest(BaseModel):
    model_config = {"extra": "ignore"}

    graph: dict[str, Any]


@router.post("/agents/{agent_id}/validate")
async def validate_graph_endpoint(
    request: Request, agent_id: str, body: ValidateGraphRequest
) -> dict[str, Any]:
    """Dry-run graph validation (P2/P9, R06).

    Validates syntax, schemas, singletons, and connectivity without creating a
    revision or mutating state. Zero external side effects.
    """
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    await _agent_for_actor(designer, actor, agent_id)
    require_permission(actor, "designer.view", {"agent_id": agent_id})

    from assistant.designer.schemas import UnsupportedSchemaError, parse_graph_document
    from assistant.designer.validation import validate_graph

    try:
        graph = parse_graph_document(body.graph)
    except UnsupportedSchemaError as exc:
        return {
            "ok": False,
            "issues": [{
                "node_id": None,
                "edge_id": None,
                "code": "unsupported_schema",
                "message": str(exc),
            }],
        }

    report = validate_graph(graph)
    return report.to_dict()


class ContextPreviewRequest(BaseModel):
    model_config = {"extra": "ignore"}

    graph: dict[str, Any] | None = None
    revision_id: str | None = None
    sample_messages: list[dict[str, Any]] = []


@router.post("/agents/{agent_id}/context-preview")
async def context_preview(
    request: Request, agent_id: str, body: ContextPreviewRequest
) -> dict[str, Any]:
    """Non-billable context allocation & budget preview (P6, R08, R16, Fix 8).

    Calculates context token breakdown and budget constraints with ZERO
    external provider calls.
    """
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    agent = await _agent_for_actor(designer, actor, agent_id)
    require_permission(actor, "designer.view", {"agent_id": agent_id})

    from assistant.designer.compiler import compile_execution_config
    from assistant.designer.context import prepare_context
    from assistant.designer.schemas import parse_graph_document

    if body.graph is not None:
        graph = parse_graph_document(body.graph)
        revision_id = body.revision_id or "draft"
    elif body.revision_id:
        revision = await designer["store"].get_revision(body.revision_id)
        if revision is None:
            raise DesignerError("missing", "revision not found")
        graph = parse_graph_document(dict(revision["graph_json"]))
        revision_id = body.revision_id
    elif agent.get("active_revision_id"):
        revision = await designer["store"].get_revision(str(agent["active_revision_id"]))
        if revision is None:
            raise DesignerError("missing", "active revision not found")
        graph = parse_graph_document(dict(revision["graph_json"]))
        revision_id = str(agent["active_revision_id"])
    else:
        raise DesignerError(
            "invalid_request",
            "must provide graph, revision_id, or agent must have an active revision",
        )

    config = compile_execution_config(
        agent_id=agent_id, revision_id=revision_id, graph=graph
    )
    prepared = prepare_context(config, body.sample_messages)
    return {
        "agent_id": agent_id,
        "revision_id": revision_id,
        "system_prompt": prepared.system_prompt,
        "skills_instructions": prepared.skills_instructions,
        "retained_messages": prepared.retained_messages,
        "token_breakdown": {
            "system_tokens": prepared.token_breakdown.system_tokens,
            "skills_tokens": prepared.token_breakdown.skills_tokens,
            "history_tokens": prepared.token_breakdown.history_tokens,
            "total_estimated_tokens": prepared.token_breakdown.total_estimated_tokens,
        },
        "total_turns_provided": prepared.total_turns_provided,
        "turns_retained": prepared.turns_retained,
        "truncated": prepared.truncated,
        "budget": {
            "recent_turns_limit": config.context.recent_turns,
            "estimated_input_tokens_limit": config.context.estimated_input_tokens,
            "output_token_cap": config.context.output_token_cap,
            "max_model_attempts": config.context.max_model_attempts,
            "max_tool_calls": config.context.max_tool_calls,
            "run_wall_clock_seconds": config.context.run_wall_clock_seconds,
        },
    }

# ---------------------------------------------------------------------------
# Activation, revocation & rollback (P7): Prepare → CAS Activate mandatory.
# ---------------------------------------------------------------------------


class ActivateRequest(BaseModel):
    model_config = {"extra": "ignore"}

    revision_id: str


class RollbackRequest(BaseModel):
    model_config = {"extra": "ignore"}

    revision_id: str


@router.post("/agents/{agent_id}/activate")
async def activate_revision(request: Request, agent_id: str, body: ActivateRequest) -> Any:
    """Prepare + CAS-activate a revision (P7, R05, R12).

    Validates the candidate graph, compiles ExecutionConfig, and
    atomically swaps active_revision_id. Failed preparation preserves
    the currently active revision.
    """
    designer = _designer_state(request)
    actor = await require_actor_and_csrf(request)
    agent = await _agent_for_actor(designer, actor, agent_id)
    require_permission(actor, "designer.activate", {"agent_id": agent_id})

    if_match = request.headers.get("if-match", "")
    expected_version = _version_from_etag(if_match)
    if expected_version is None or expected_version != int(agent["row_version"]):
        raise DesignerError("conflict", "stale write: agent was modified concurrently")

    from assistant.designer.activation import activate

    runtime_pool = designer.get("runtime_pool")
    result = await activate(
        designer["store"],
        agent_id=agent_id,
        revision_id=body.revision_id,
        expected_version=expected_version,
        actor_user_id=actor.user_id,
        runtime_pool=runtime_pool,
    )

    return JSONResponse(
        status_code=200,
        content={
            "activated": True,
            "agent_id": result.agent_id,
            "revision_id": result.revision_id,
            "previous_revision_id": result.previous_revision_id,
            "row_version": result.new_row_version,
            "runtimes_drained": result.runtimes_drained,
            "etag": f'W/"{result.new_row_version}"',
        },
    )


@router.post("/agents/{agent_id}/revoke")
async def revoke_revision(request: Request, agent_id: str) -> Any:
    """Immediate revocation (P7, R14): NULL the active pointer, drain
    runtimes, block next dispatch. Permission: designer.revoke."""
    designer = _designer_state(request)
    actor = await require_actor_and_csrf(request)
    agent = await _agent_for_actor(designer, actor, agent_id)
    require_permission(actor, "designer.revoke", {"agent_id": agent_id})

    if_match = request.headers.get("if-match", "")
    expected_version = _version_from_etag(if_match)
    if expected_version is None or expected_version != int(agent["row_version"]):
        raise DesignerError("conflict", "stale write: agent was modified concurrently")

    from assistant.designer.activation import revoke_now

    runtime_pool = designer.get("runtime_pool")
    result = await revoke_now(
        designer["store"],
        agent_id=agent_id,
        expected_version=expected_version,
        actor_user_id=actor.user_id,
        runtime_pool=runtime_pool,
    )

    return JSONResponse(
        status_code=200,
        content={
            "revoked": True,
            "agent_id": result.agent_id,
            "revoked_revision_id": result.revoked_revision_id,
            "row_version": result.new_row_version,
            "runtimes_drained": result.runtimes_drained,
            "etag": f'W/"{result.new_row_version}"',
        },
    )


@router.post("/agents/{agent_id}/rollback")
async def rollback_revision(
    request: Request, agent_id: str, body: RollbackRequest
) -> Any:
    """Rollback to an earlier revision (P7): re-validates the target
    revision before activating it. Permission: designer.activate."""
    designer = _designer_state(request)
    actor = await require_actor_and_csrf(request)
    agent = await _agent_for_actor(designer, actor, agent_id)
    require_permission(actor, "designer.activate", {"agent_id": agent_id})

    if_match = request.headers.get("if-match", "")
    expected_version = _version_from_etag(if_match)
    if expected_version is None or expected_version != int(agent["row_version"]):
        raise DesignerError("conflict", "stale write: agent was modified concurrently")

    from assistant.designer.activation import rollback

    runtime_pool = designer.get("runtime_pool")
    result = await rollback(
        designer["store"],
        agent_id=agent_id,
        target_revision_id=body.revision_id,
        expected_version=expected_version,
        actor_user_id=actor.user_id,
        runtime_pool=runtime_pool,
    )

    return JSONResponse(
        status_code=200,
        content={
            "rolled_back": True,
            "agent_id": result.agent_id,
            "revision_id": result.revision_id,
            "previous_revision_id": result.previous_revision_id,
            "row_version": result.new_row_version,
            "runtimes_drained": result.runtimes_drained,
            "etag": f'W/"{result.new_row_version}"',
        },
    )


# ---------------------------------------------------------------------------
# Events & SSE (P8, R04, R17, R18): Live view streaming & historical replay.
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/events")
async def stream_run_events(request: Request, run_id: str) -> Any:
    """Stream monotonic run events via SSE with Last-Event-ID replay (P8).

    Enforces session authentication and run ownership (foreign runs return 404
    without disclosure). Client disconnects never cancel the underlying run.
    """
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    require_permission(actor, "designer.view", {"run_id": run_id})

    run = await designer["store"].get_run_registry_entry(run_id)
    if run is None or (run["user_id"] != actor.user_id and not actor.has("designer.admin")):
        raise DesignerError("missing", "run not found")

    header_val = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    after_event_id: int | None = None
    if header_val:
        try:
            after_event_id = int(header_val)
        except ValueError:
            after_event_id = None

    from collections.abc import AsyncIterator

    from assistant.designer.events import (
        TERMINAL_EVENTS,
        RunEvent,
        format_sse_event,
        format_sse_heartbeat,
        get_event_hub,
    )

    hub = designer.get("event_hub") or get_event_hub()
    queue = await hub.subscribe(run_id)

    async def event_generator() -> AsyncIterator[str]:
        seen_event_ids: set[int] = set()
        disconnect_task = asyncio.create_task(request.is_disconnected())
        get_task: asyncio.Task[RunEvent] | None = None
        try:
            # 1. Historical replay from database (pure read - zero side effects)
            historical = await designer["store"].list_run_events(
                run_id, after_event_id=after_event_id
            )
            for ev_dict in historical:
                ev = RunEvent(
                    event_id=ev_dict["event_id"],
                    run_id=ev_dict["run_id"],
                    agent_id=ev_dict["agent_id"],
                    revision_id=ev_dict["revision_id"],
                    sequence_number=ev_dict["sequence_number"],
                    event_type=ev_dict["event_type"],
                    payload=ev_dict["payload"],
                    at=ev_dict["at"],
                )
                seen_event_ids.add(ev.event_id)
                yield format_sse_event(ev)
                if ev.event_type in TERMINAL_EVENTS:
                    return

            # Check if run is already in terminal state from registry
            current_run = await designer["store"].get_run_registry_entry(run_id)
            if current_run and current_run.get("status") in ("completed", "failed", "cancelled"):
                return

            # 2. Live stream from in-memory hub with heartbeats
            heartbeat_interval = 15.0
            last_heartbeat = asyncio.get_running_loop().time()
            while True:
                if disconnect_task.done():
                    if get_task and not get_task.done():
                        get_task.cancel()
                    break

                if get_task is None or get_task.done():
                    get_task = asyncio.create_task(queue.get())

                done, _ = await asyncio.wait(
                    [get_task, disconnect_task],
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if disconnect_task in done:
                    if not get_task.done():
                        get_task.cancel()
                    break

                if get_task in done:
                    ev = get_task.result()
                    get_task = None
                    if ev.event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(ev.event_id)
                    yield format_sse_event(ev)
                    if ev.event_type in TERMINAL_EVENTS:
                        break
                else:
                    now = asyncio.get_running_loop().time()
                    if now - last_heartbeat >= heartbeat_interval:
                        last_heartbeat = now
                        yield format_sse_heartbeat()
        except (asyncio.CancelledError, GeneratorExit):
            # Client disconnected; stream cleanly terminated
            return
        finally:
            if get_task and not get_task.done():
                get_task.cancel()
            if not disconnect_task.done():
                disconnect_task.cancel()
            await hub.unsubscribe(run_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/snapshot")
async def get_run_snapshot(request: Request, run_id: str) -> Any:
    """Return aggregated run execution state (P8)."""
    designer = _designer_state(request)
    actor = await resolve_actor(request)
    require_permission(actor, "designer.view", {"run_id": run_id})

    run = await designer["store"].get_run_registry_entry(run_id)
    if run is None or (run["user_id"] != actor.user_id and not actor.has("designer.admin")):
        raise DesignerError("missing", "run not found")

    from assistant.designer.events import RunEvent, build_run_snapshot

    raw_events = await designer["store"].list_run_events(run_id)
    events = [
        RunEvent(
            event_id=ev["event_id"],
            run_id=ev["run_id"],
            agent_id=ev["agent_id"],
            revision_id=ev["revision_id"],
            sequence_number=ev["sequence_number"],
            event_type=ev["event_type"],
            payload=ev["payload"],
            at=ev["at"],
        )
        for ev in raw_events
    ]
    snapshot = build_run_snapshot(events)
    if snapshot["status"] == "unknown":
        snapshot["status"] = run.get("status", "unknown")
        snapshot["run_id"] = run["run_id"]
        snapshot["agent_id"] = run.get("agent_id", "")
        snapshot["revision_id"] = run.get("revision_id", "")
        if run.get("failure_reason"):
            snapshot["error"] = run["failure_reason"]

    return JSONResponse(status_code=200, content=snapshot)


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


def mount_designer_spa(app: FastAPI, dist_dir: Path | None = None) -> None:
    """Mount the built React SPA static assets and HTML fallback for /designer/."""
    if dist_dir is None:
        dist_dir = Path(__file__).resolve().parents[3] / "frontend" / "agent-designer" / "dist"

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        from starlette.staticfiles import StaticFiles

        app.mount(
            "/designer/assets",
            StaticFiles(directory=str(assets_dir)),
            name="designer_assets",
        )

    from fastapi.responses import FileResponse, HTMLResponse

    @app.get("/designer/{full_path:path}")
    async def designer_spa_fallback(full_path: str) -> Any:
        # Never intercept API routes
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        index_file = dist_dir / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        placeholder = (
            "<!DOCTYPE html><html><head><title>Agent Designer</title></head>"
            "<body><div id='root'><h1>Agent Designer</h1>"
            "<p>Frontend assets not built yet.</p></div></body></html>"
        )
        return HTMLResponse(placeholder, status_code=200)

    @app.get("/designer")
    async def designer_root() -> Any:
        index_file = dist_dir / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        placeholder = (
            "<!DOCTYPE html><html><head><title>Agent Designer</title></head>"
            "<body><div id='root'><h1>Agent Designer</h1>"
            "<p>Frontend assets not built yet.</p></div></body></html>"
        )
        return HTMLResponse(placeholder, status_code=200)

