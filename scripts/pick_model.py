"""Pick a cheap, tool-capable OpenRouter model and optionally set MODEL_NAME.

Queries the live OpenRouter catalog with your key (key never printed),
filters for models whose supported parameters include tool calling,
sorts by blended price, and prints the cheapest candidates.

Usage:
    uv run python scripts/pick_model.py            # show top candidates
    uv run python scripts/pick_model.py --set ID   # write MODEL_NAME=ID into .env
    uv run python scripts/pick_model.py --free     # only :free variants
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key

CATALOG_URL = "https://openrouter.ai/api/v1/models"
ENV_PATH = Path(".env")


def _blended_price_per_m(model: dict) -> float | None:
    pricing = model.get("pricing") or {}
    try:
        prompt = float(pricing.get("prompt") or 0)
        completion = float(pricing.get("completion") or 0)
    except (TypeError, ValueError):
        return None
    return prompt * 1_000_000 * 0.25 + completion * 1_000_000 * 0.75


def main() -> int:
    parser = argparse.ArgumentParser(description="Pick a cheap tool-capable OpenRouter model")
    parser.add_argument(
        "--set", dest="set_model", metavar="MODEL_ID", help="write MODEL_NAME into .env"
    )
    parser.add_argument("--free", action="store_true", help="only :free variants")
    parser.add_argument("--top", type=int, default=8, help="how many candidates to show")
    args = parser.parse_args()

    key = (dotenv_values(ENV_PATH).get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        print("OPENROUTER_API_KEY is not set in .env; cannot query the catalog.")
        return 1

    with httpx.Client(timeout=30, headers={"Authorization": f"Bearer {key}"}) as client:
        response = client.get(CATALOG_URL)
        response.raise_for_status()
        models = response.json().get("data", [])

    tool_capable = []
    for model in models:
        params = model.get("supported_parameters") or []
        if "tools" not in params:
            continue
        if args.free and not model.get("id", "").endswith(":free"):
            continue
        price = _blended_price_per_m(model)
        if price is None:
            continue
        tool_capable.append((price, model.get("id", "?"), model.get("context_length")))

    tool_capable.sort()
    if not tool_capable:
        print("No tool-capable models found (unexpected).")
        return 1

    top = min(args.top, len(tool_capable))
    print(f"Top {top} tool-capable candidates (blended price per 1M tokens):")
    for price, model_id, ctx in tool_capable[: args.top]:
        print(f"  {model_id:55s} ~${price:8.3f}/1M  ctx={ctx}")

    if args.set_model:
        chosen = args.set_model
        if not any(model_id == chosen for _, model_id, _ in tool_capable):
            print(f"warning: {chosen} not in the tool-capable list; setting anyway")
        set_key(ENV_PATH, "MODEL_NAME", chosen)
        print(f"MODEL_NAME set to {chosen!r} in .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
