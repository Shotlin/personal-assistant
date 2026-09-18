"""Apply Designer SQL migrations under a PostgreSQL advisory lock.

Contract (plan Fix 9):

- Migrations live in ``migrations/designer/`` as ordered, checksummed
  ``NNN_name.sql`` files.
- Each migration runs inside its own transaction while holding
  ``pg_advisory_xact_lock`` (auto-released at commit), so two gateway
  processes starting simultaneously cannot interleave DDL.
- The ``designer_migrations`` ledger records id + sha256 checksum. A file
  whose checksum differs from the ledger entry fails loudly (files are
  immutable once applied; edit only by adding a new migration).
- Idempotent at the file level too: already-applied migrations are
  skipped, so reruns are safe (acceptance A11).

Never runs unless explicitly invoked (this script) or when
``DESIGNER_ENABLED=true`` at gateway startup (Safety note 1: flag-off
startup never touches Designer migrations).

Usage::

    uv run python scripts/migrate_designer.py [--database-url ...]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "designer"
DEFAULT_DATABASE_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"

# Fixed advisory-lock key for Designer schema changes. Any process that
# applies Designer migrations must take this same lock.
DESIGNER_MIGRATION_LOCK_KEY = 0x41474453  # "AGDS"


def load_migrations(directory: Path) -> list[tuple[str, str, str]]:
    """Return (id, checksum, sql) for each *.sql file in filename order."""
    if not directory.exists():
        return []
    result: list[tuple[str, str, str]] = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        result.append((path.stem, checksum, sql))
    return result


async def apply_migrations(database_url: str, directory: Path) -> list[str]:
    """Apply pending migrations; return the ids applied this run."""
    applied: list[str] = []
    migrations = load_migrations(directory)
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        # Bootstrap the ledger itself (idempotent) so the first migration
        # can be recorded; migration files never create it.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS designer_migrations (
                id         TEXT PRIMARY KEY,
                checksum   TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for migration_id, checksum, sql in migrations:
            # One transaction per migration: ledger check + DDL + record
            # commit atomically under the advisory xact lock.
            async with conn.transaction():
                cursor = conn.cursor()
                await cursor.execute(
                    "SELECT checksum FROM designer_migrations WHERE id = %s",
                    (migration_id,),
                )
                row = await cursor.fetchone()
                if row is not None:
                    existing_checksum = str(row[0])
                    if existing_checksum != checksum:
                        raise RuntimeError(
                            f"migration {migration_id} changed after being applied "
                            f"(ledger {existing_checksum[:12]} != file {checksum[:12]}); "
                            "applied migrations are immutable - add a new file instead"
                        )
                    continue
                await cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (DESIGNER_MIGRATION_LOCK_KEY,)
                )
                await cursor.execute(sql)
                await cursor.execute(
                    "INSERT INTO designer_migrations (id, checksum) VALUES (%s, %s)",
                    (migration_id, checksum),
                )
                applied.append(migration_id)
    return applied


async def _run(database_url: str) -> int:
    applied = await apply_migrations(database_url, MIGRATIONS_DIR)
    # Never print credentials embedded in the DSN; keep only the host part.
    safe_target = database_url.split("@")[-1] if "@" in database_url else database_url
    if applied:
        print(f"applied {len(applied)} migration(s) to {safe_target}")
        for migration_id in applied:
            print(f"  + {migration_id}")
    else:
        print(f"designer migrations up to date at {safe_target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default="",
        help="PostgreSQL DSN (defaults to DATABASE_URL env, then .env, then the compose default)",
    )
    args = parser.parse_args()
    database_url = args.database_url
    if not database_url:
        from dotenv import dotenv_values

        env = dotenv_values(REPO_ROOT / ".env")
        database_url = str(env.get("DATABASE_URL") or DEFAULT_DATABASE_URL)
    try:
        return asyncio.run(_run(database_url))
    except Exception as exc:  # noqa: BLE001 -- operator-facing failure report
        print(f"migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
