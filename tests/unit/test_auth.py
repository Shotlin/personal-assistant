"""Unit tests for bearer-token auth (Phase 1 Task 1)."""

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from assistant.api.auth import parse_bearer_token, require_gateway_key, tokens_match
from assistant.settings import Settings

GATEWAY_KEY = "test-gateway-key-123"


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        agent_gateway_api_key=GATEWAY_KEY,
        model_provider="openrouter",
        openrouter_api_key="dummy-not-real",
        cua_enabled=False,
    )
    app = FastAPI()
    app.state.settings = settings

    @app.get("/protected", dependencies=[Depends(require_gateway_key)])
    def protected() -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(app) as test_client:
        yield test_client


def test_parse_bearer_token_valid() -> None:
    token = parse_bearer_token("Bearer abc123")
    assert token is not None
    assert token.token == "abc123"


def test_parse_bearer_token_case_insensitive_scheme() -> None:
    token = parse_bearer_token("bearer abc123")
    assert token is not None
    assert token.token == "abc123"


def test_parse_bearer_token_missing() -> None:
    assert parse_bearer_token(None) is None
    assert parse_bearer_token("") is None


def test_parse_bearer_token_wrong_scheme() -> None:
    assert parse_bearer_token("Basic abc123") is None


def test_parse_bearer_token_empty_token() -> None:
    assert parse_bearer_token("Bearer ") is None
    assert parse_bearer_token("Bearer") is None


def test_tokens_match_positive() -> None:
    assert tokens_match("secret", "secret") is True


def test_tokens_match_negative() -> None:
    assert tokens_match("secret", "different") is False
    assert tokens_match("secret", None) is False
    assert tokens_match("secret", "") is False


def test_protected_missing_key_rejected(client: TestClient) -> None:
    response = client.get("/protected")
    assert response.status_code == 401
    body = response.json()
    assert body["detail"]["error"]["code"] == "invalid_api_key"


def test_protected_incorrect_key_rejected(client: TestClient) -> None:
    response = client.get("/protected", headers={"Authorization": "Bearer wrong-key"})
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "invalid_api_key"


def test_protected_valid_key_accepted(client: TestClient) -> None:
    response = client.get("/protected", headers={"Authorization": f"Bearer {GATEWAY_KEY}"})
    assert response.status_code == 200
    assert response.json() == {"ok": "yes"}
