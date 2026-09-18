"""Open WebUI HTTP client (contract-first, P0 probe pending).

All calls authenticate with the user's own upstream credential resolved
server-side from the encrypted store — the browser never holds it (R09).
Contracts follow the pinned v0.11.3 source and are verified by
``scripts/probe_openwebui_contract.py``; until the probe runs, every
route here is exercised only through recorded fixtures (respx) and live
behavior is explicitly marked unverified in ``upstream-contracts.json``.

Hard rules:
- redirects are never followed (SSRF guard; R09);
- timeouts are always set;
- upstream error bodies are capped + redacted before reuse;
- no method here writes anything except the explicit create/update ones.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from assistant.designer.errors import DesignerError

logger = logging.getLogger("assistant.designer.openwebui")

_TIMEOUT_SECONDS = 15.0
_ERROR_EXCERPT_CAP = 200


class OpenWebUIClient:
    """Thin authenticated client over the Open WebUI user API."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            follow_redirects=False,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async with self._client() as client:
            response = await client.request(method, path, **kwargs)
        if response.is_redirect:
            raise DesignerError(
                "upstream_unavailable", "Open WebUI returned a redirect; not followed"
            )
        return response

    async def _get_json(self, path: str) -> Any:
        response = await self._request("GET", path)
        if response.status_code == 401:
            raise DesignerError("invalid_credentials", "upstream credential rejected")
        if response.status_code == 403:
            # Denial is surfaced, never hidden behind an empty list (R07).
            raise DesignerError("upstream_unavailable", "upstream denied access")
        if response.status_code != 200:
            raise DesignerError(
                "upstream_unavailable",
                f"Open WebUI returned {response.status_code}",
            )
        return response.json()

    # --- prompts (authoring stays in Open WebUI, R07) ---

    async def list_prompts(self) -> list[dict[str, Any]]:
        data = await self._get_json("/api/v1/prompts/")
        return data if isinstance(data, list) else []

    async def get_prompt(self, prompt_id: str) -> dict[str, Any] | None:
        response = await self._request("GET", f"/api/v1/prompts/{prompt_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DesignerError(
                "upstream_unavailable", f"Open WebUI returned {response.status_code}"
            )
        return response.json()

    # --- skills (text content; never executable scripts, R07) ---

    async def list_skills(self) -> list[dict[str, Any]]:
        data = await self._get_json("/api/v1/skills/")
        return data if isinstance(data, list) else []

    async def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        response = await self._request("GET", f"/api/v1/skills/{skill_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DesignerError(
                "upstream_unavailable", f"Open WebUI returned {response.status_code}"
            )
        return response.json()

    async def create_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("POST", "/api/v1/skills/create", json=payload)
        if response.status_code in (401, 403):
            raise DesignerError("permission_denied", "upstream rejected skill creation")
        if response.status_code not in (200, 201):
            raise DesignerError(
                "upstream_unavailable",
                f"skill creation returned {response.status_code}",
            )
        return response.json()

    async def update_skill(
        self, skill_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._request("POST", f"/api/v1/skills/{skill_id}/update", json=payload)
        if response.status_code in (401, 403):
            raise DesignerError("permission_denied", "upstream rejected skill update")
        if response.status_code != 200:
            raise DesignerError(
                "upstream_unavailable",
                f"skill update returned {response.status_code}",
            )
        return response.json()

    # --- knowledge (listing only; retrieval is the KnowledgeSource, Fix 2) ---

    async def list_knowledge(self) -> list[dict[str, Any]]:
        data = await self._get_json("/api/v1/knowledge/")
        return data if isinstance(data, list) else []
