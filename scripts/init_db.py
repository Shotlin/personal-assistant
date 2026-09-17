"""Create Postgres schemas for thread checkpoints and long-term memory.

Idempotent: safe to run repeatedly. Reads only DATABASE_URL (no model
provider configuration required). Never prints connection credentials.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import dotenv_values

from assistant.memory.postgres import open_memory_resources

_DEFAULT_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"


def _database_url() -> str:
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    env_file = Path(".env")
    if env_file.is_file():
        value = dotenv_values(env_file).get("DATABASE_URL")
        if value:
            return value
    return _DEFAULT_URL


def _display_target(database_url: str) -> str:
    """Return 'host:port/database' without credentials."""
    tail = database_url.split("@", 1)[-1]
    return tail.split("?", 1)[0] if tail else "<redacted>"


async def main() -> int:
    database_url = _database_url()
    async with open_memory_resources(database_url) as resources:
        # setup() runs inside open_memory_resources; touch both handles to
        # prove they are usable before declaring success.
        assert resources.saver is not None
        assert resources.store is not None
    print(f"Postgres schemas ready at {_display_target(database_url)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
