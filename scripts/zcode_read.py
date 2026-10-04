"""Read ZCode's real account, models, balances and sessions through its own window.

Quits ZCode, relaunches it with a private debug port, reads, then relaunches it normally.
Pass --yes to confirm that closing and reopening ZCode is fine right now.

    uv run python scripts/zcode_read.py --yes [--save]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from assistant.claude_code.store import SessionStore
from assistant.coding_agents.zcode_cdp.launcher import ZCodeLauncher
from assistant.coding_agents.zcode_cdp.reader import read_snapshot, save_snapshot
from assistant.settings import Settings


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="ZCode may be closed and reopened now")
    parser.add_argument("--save", action="store_true", help="keep the result in Sani's own state")
    args = parser.parse_args()
    snap = await read_snapshot(ZCodeLauncher(), consent=args.yes)
    print(json.dumps(snap.as_dict(), indent=2, ensure_ascii=False))
    if args.save and not snap.refusal:
        settings = Settings()
        await save_snapshot(SessionStore(settings.sani_db_path), snap)
        print("saved")
    return 1 if snap.refusal else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
