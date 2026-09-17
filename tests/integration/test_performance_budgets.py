"""Local latency budgets (spec section 24, measured via ASGI transport).

Targets: /healthz p95 < 100 ms, /v1/models p95 < 150 ms. These measure our
orchestration overhead only (no third-party inference).
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from assistant.main import create_app
from assistant.settings import Settings

GATEWAY_KEY = "perf-gateway-key"

HEALTHZ_P95_MS = 100
MODELS_P95_MS = 150
SAMPLES = 25


def make_lifespan():  # noqa: ANN202
    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        yield


def _p95(values_ms: list[float]) -> float:
    ordered = sorted(values_ms)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index]


@pytest.fixture
async def perf_app(require_postgres: None) -> Any:
    app = create_app(
        Settings(
            agent_gateway_api_key=GATEWAY_KEY,
            model_provider="openrouter",
            openrouter_api_key="dummy",
            cua_enabled=False,
        ),
        lifespan=make_lifespan(),
    )
    async with app.router.lifespan_context(app):
        yield app


async def test_healthz_p95_under_budget(perf_app: Any) -> None:
    transport = httpx.ASGITransport(app=perf_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
        samples: list[float] = []
        for _ in range(SAMPLES):
            start = time.perf_counter()
            response = await client.get("/healthz")
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert response.status_code == 200
            samples.append(elapsed_ms)
    p95 = _p95(samples)
    print(f"\n/healthz p95 = {p95:.2f} ms over {SAMPLES} samples")
    assert p95 < HEALTHZ_P95_MS, f"/healthz p95 {p95:.2f} ms exceeds {HEALTHZ_P95_MS} ms"


async def test_models_p95_under_budget(perf_app: Any) -> None:
    transport = httpx.ASGITransport(app=perf_app)
    headers = {"Authorization": f"Bearer {GATEWAY_KEY}"}
    async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
        samples: list[float] = []
        for _ in range(SAMPLES):
            start = time.perf_counter()
            response = await client.get("/v1/models", headers=headers)
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert response.status_code == 200
            samples.append(elapsed_ms)
    p95 = _p95(samples)
    print(f"\n/v1/models p95 = {p95:.2f} ms over {SAMPLES} samples")
    assert p95 < MODELS_P95_MS, f"/v1/models p95 {p95:.2f} ms exceeds {MODELS_P95_MS} ms"
