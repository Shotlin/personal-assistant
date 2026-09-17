"""Verify a live Cua Driver installation (spec section 13, Task 5).

Usage:
    uv run python scripts/verify_cua.py            # connectivity + filtering
    uv run python scripts/verify_cua.py --live     # additionally launch Calculator

Requires the driver installed and the bounded daemon running
(see README). Requires CUA_ENABLED=true and a valid manifest path in .env.
"""

from __future__ import annotations

import argparse
import asyncio

from assistant.agent.context import RunBudget
from assistant.settings import Settings
from assistant.tools.cua import load_cua_tools
from assistant.tools.policy import cua_run_budget


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Cua Driver MCP connectivity")
    parser.add_argument("--live", action="store_true", help="launch Calculator as a live check")
    args = parser.parse_args(argv)

    settings = Settings()
    connection = await load_cua_tools(settings)
    print("discovered:", sorted(connection.discovered_names))
    print("enabled:   ", sorted(connection.tool_names))
    print("skipped:   ", sorted(connection.skipped_names))

    if not args.live:
        print("CUA filtering OK (connectivity only).")
        return 0

    by_name = {t.name: t for t in connection.tools}
    budget = RunBudget()
    cua_run_budget.set(budget)

    apps_tool = by_name.get("list_apps")
    if apps_tool is None:
        print("list_apps tool not available; cannot run live check")
        return 1
    apps = await apps_tool.ainvoke({})
    names = _extract_app_names(apps)
    print(f"observed {len(names)} apps; calculator-like present: {_has_calculator(names)}")

    launch = by_name.get("launch_app")
    if launch is None:
        print("launch_app not allowlisted; skipping launch check")
        return 0
    result = await launch.ainvoke({"app_name": "Calculator"})
    print("launch_app result (truncated):", str(result)[:200])
    print(f"mutating budget used: {budget.used}")
    return 0


def _extract_app_names(apps: object) -> list[str]:
    if isinstance(apps, str):
        return [line.strip() for line in apps.splitlines() if line.strip()]
    if isinstance(apps, dict):
        for key in ("apps", "applications", "data"):
            value = apps.get(key)
            if isinstance(value, list):
                out = []
                for item in value:
                    if isinstance(item, dict) and "name" in item:
                        out.append(str(item["name"]))
                    elif isinstance(item, str):
                        out.append(item)
                return out
    if isinstance(apps, list):
        return [str(i) for i in apps]
    return []


def _has_calculator(names: list[str]) -> bool:
    return any("calculator" in name.lower() for name in names)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
