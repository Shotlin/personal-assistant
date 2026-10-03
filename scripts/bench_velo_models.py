"""Measure the two model calls the Velo planner/vision rungs make, with YOUR key.

    OPENROUTER_API_KEY=sk-or-... uv run python scripts/bench_velo_models.py [screenshot.png]

Prints, per call, latency and whether the answer validated. The planner is run on
a few spoken-style requests; the vision locator on a screenshot you pass (PNG, the
size Sani sends is 1280px wide) with the control name you give via VELO_BENCH_TARGET.
Nothing is stored or sent anywhere except the model provider you configured.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

from assistant.models import build_chat_model
from assistant.settings import Settings
from assistant.velo.planner import make_plan
from assistant.velo.vision import VisionLocator

REQUESTS = (
    "open chrome then open youtube then search that channel and play the video",
    "now open codex input box and write a prompt for a simple saas website then submit",
    "download all the images chatgpt generated and then open the first mockup",
)


async def main() -> None:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        sys.exit("set OPENROUTER_API_KEY first")
    settings = Settings(
        openrouter_api_key=key, model_provider="openrouter",
        model_name=os.environ.get("MODEL_NAME", "z-ai/glm-5.3-flash"),
        model_reasoning_effort="low",
    )
    model = build_chat_model(settings)
    times = []
    for text in REQUESTS:
        started = time.monotonic()
        plan = await make_plan(model, text)
        took = time.monotonic() - started
        times.append(took)
        steps = [f"{s.recipe}" for s in plan.steps] if plan and plan.kind == "plan" else plan
        print(f"plan {took:5.1f}s  valid={plan is not None}  {steps}")
    print(f"planner median {statistics.median(times):.1f}s over {len(times)} requests")
    if len(sys.argv) > 1:
        png = Path(sys.argv[1]).read_bytes()
        target = os.environ.get("VELO_BENCH_TARGET", "the main button")
        import io

        from PIL import Image
        width, height = Image.open(io.BytesIO(png)).size
        started = time.monotonic()
        hit = await VisionLocator(settings, model=model).locate(png, target, width, height)
        print(f"vision {time.monotonic() - started:5.1f}s  target={target!r}  hit={hit}")


if __name__ == "__main__":
    asyncio.run(main())
