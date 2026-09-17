"""GET /v1/models -- exactly one Phase 1 model (spec section 16.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from assistant.api.auth import SettingsDep, require_gateway_key
from assistant.api.schemas import ModelEntry, ModelListResponse

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])


@router.get("/models", response_model=ModelListResponse)
async def list_models(settings: SettingsDep) -> ModelListResponse:
    return ModelListResponse(data=[ModelEntry(id=settings.assistant_model_id)])
