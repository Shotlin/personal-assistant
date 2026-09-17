"""Shared fixtures for all test suites (real compose PostgreSQL)."""

import socket

import pytest

POSTGRES_HOST = "127.0.0.1"
POSTGRES_PORT = 5433
POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"


def postgres_reachable(timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((POSTGRES_HOST, POSTGRES_PORT), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def require_postgres() -> None:
    """Fail loudly when the compose database is not running."""
    if not postgres_reachable():
        pytest.fail(
            f"PostgreSQL is not reachable at {POSTGRES_HOST}:{POSTGRES_PORT}. "
            "Start it first: docker compose up -d postgres"
        )
