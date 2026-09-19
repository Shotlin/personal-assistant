"""GET /v1/models — legacy single model; per-actor registry when flag-on.

Flag-off (C2): exactly one entry, ``settings.assistant_model_id`` — the
Phase-1 contract is untouched. Flag-on: the Agent Registry lists the
agents this actor may use (C5); model discovery is presentation only and
chat re-checks authorization independently.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from assistant.api.auth import SettingsDep, require_gateway_key
from assistant.api.identity import extract_identity
from assistant.api.schemas import ModelEntry, ModelListResponse

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    settings: SettingsDep, request: Request
) -> Any:
    if not settings.designer_enabled:
        return ModelListResponse(data=[ModelEntry(id=settings.assistant_model_id)])

    from assistant.designer.chat_authorization import list_models_for_actor

    # Model-discovery identity is best-effort (C5): Open WebUI may send
    # user headers on model refresh or not. Absent identity yields the
    # safe enabled catalog — it is never an authorization grant.
    user_id = ""
    try:
        identity = extract_identity(
            request.headers, is_production=settings.is_production
        )
        user_id = identity.user_id
    except Exception:  # noqa: BLE001 -- discovery must not require identity
        user_id = ""
    entries = await list_models_for_actor(request.app, settings, user_id=user_id)
    return ModelListResponse(
        data=[ModelEntry(id=entry["id"], owned_by=entry["owned_by"]) for entry in entries or []]
    )
