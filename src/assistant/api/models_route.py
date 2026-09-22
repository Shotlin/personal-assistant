"""GET /v1/models -- the gateway's single-model contract.

Sani's local desktop client talks to exactly one assistant model id;
per-actor model registries belonged to a removed layer.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from assistant.api.auth import SettingsDep, require_gateway_key
from assistant.api.schemas import ModelEntry, ModelListResponse

router = APIRouter(prefix="/v1", dependencies=[Depends(require_gateway_key)])


@router.get("/models", response_model=ModelListResponse)
async def list_models(settings: SettingsDep) -> Any:
    return ModelListResponse(data=[ModelEntry(id=settings.assistant_model_id)])
