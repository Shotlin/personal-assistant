"""Standalone Velo runner (Velo spec section 17).

    uv run python scripts/run_velo.py "Open Chrome and search WhatsApp Web"
    uv run python scripts/run_velo.py --text "I will call you later" \\
        "Open WhatsApp Web, select Rahul, and send my message"

No GUI, no Deep Agent, no OpenAI/OpenRouter fallback: Velo observes through
the existing bounded CUA driver and decides through real JEV/TypeSafe only.
Exit codes: 0 DONE, 1 FAILED, 3 ASK_USER, 4 STOPPED, 2 configuration error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import uuid
from typing import Any

from assistant.agent.context import RunBudget
from assistant.runtime.session import (
    DesktopSessionManager,
    McpToolDesktopDriver,
)
from assistant.settings import Settings
from assistant.tools.cua import open_cua_connection
from assistant.velo.agent import VeloAgent
from assistant.velo.cua_adapter import VeloCuaAdapter, allowed_apps_from_manifest
from assistant.velo.jev import JevDecisionEngine, text_candidates_from_objective
from assistant.velo.types import (
    VeloLimits,
    VeloMetrics,
    VeloObjective,
    VeloResult,
    VeloStatus,
)

EXIT_CODES = {
    VeloStatus.DONE: 0,
    VeloStatus.FAILED: 1,
    VeloStatus.ASK_USER: 3,
    VeloStatus.STOPPED: 4,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Velo quick-control agent on one objective."
    )
    parser.add_argument("objective", nargs="+", help="the user objective text")
    parser.add_argument(
        "--text",
        action="append",
        default=[],
        metavar="EXACT_TEXT",
        help="exact user text payload (user_text_1, user_text_2, ...); typed byte for byte",
    )
    parser.add_argument("--max-steps", type=int, default=None, help="override VELO_MAX_STEPS")
    parser.add_argument(
        "--max-runtime", type=float, default=None, help="override VELO_MAX_RUNTIME_SECONDS"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable final output")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = Settings()
        if not settings.velo_enabled:
            print("Velo is disabled (VELO_ENABLED=false); refusing to run.", file=sys.stderr)
            return 2
        limits = VeloLimits(
            max_steps=args.max_steps or settings.velo_max_steps,
            max_runtime_seconds=float(
                args.max_runtime
                if args.max_runtime is not None
                else settings.velo_max_runtime_seconds
            ),
            max_same_action_repeats=settings.velo_max_same_action_repeats,
            max_consecutive_failed_actions=settings.velo_max_consecutive_failed_actions,
            recent_history_steps=settings.velo_recent_history_steps,
        )
        allowed_apps = allowed_apps_from_manifest(settings.cua_capability_manifest_path)
        jev = JevDecisionEngine(
            api_key=settings.typesafe_api_key,
            model=settings.velo_jev_model,
            timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001 -- config errors are terminal
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    objective = VeloObjective(
        text=" ".join(args.objective).strip(),
        user_text_payloads={
            f"user_text_{position}": text for position, text in enumerate(args.text, start=1)
        },
        text_candidates=text_candidates_from_objective(" ".join(args.objective)),
    )
    run_id = f"velo-{uuid.uuid4().hex[:8]}"

    try:
        result = await _run_objective(settings, limits, allowed_apps, jev, objective, run_id)
    except Exception as exc:  # noqa: BLE001 -- startup failures must fail closed
        print(f"Velo cannot start: {exc}", file=sys.stderr)
        return 1
    _report(result, json_output=args.json)
    return EXIT_CODES[result.status]


async def _run_objective(
    settings: Settings,
    limits: VeloLimits,
    allowed_apps: dict[str, str],
    jev: JevDecisionEngine,
    objective: VeloObjective,
    run_id: str,
) -> VeloResult:
    """Open the persistent CUA connection and run one bounded Velo loop."""
    async with open_cua_connection(settings) as connection:
        manager = DesktopSessionManager(
            McpToolDesktopDriver(connection.lifecycle_tools_by_name),
            enabled=settings.active_cursor_persistence_enabled,
        )
        async with manager.open(run_id) as desktop_run:
            budget = RunBudget(max_actions=limits.max_steps)
            adapter = VeloCuaAdapter(
                connection,
                desktop_run,
                budget,
                allowed_apps=allowed_apps,
            )
            agent = _build_agent(adapter, jev, limits, allowed_apps)
            return await _run_with_cancellation(agent, objective, manager, run_id)


def _report(result: VeloResult, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(_result_payload(result), indent=2))
        return
    print(f"[{result.status.value}] {result.reason}")
    print("metrics:", json.dumps(result.metrics.as_dict()))
    if result.status is VeloStatus.ASK_USER:
        print("Velo needs your answer; re-run with a more specific objective.")


def _build_agent(
    adapter: VeloCuaAdapter,
    jev: JevDecisionEngine,
    limits: VeloLimits,
    allowed_apps: dict[str, str],
) -> VeloAgent:
    return VeloAgent(adapter, jev, limits, allowed_apps=allowed_apps)


async def _run_with_cancellation(
    agent: Any, objective: VeloObjective, manager: DesktopSessionManager, run_id: str
) -> Any:
    """Run the agent; Ctrl-C cancels locally -- no new CUA action may start."""
    loop = asyncio.get_running_loop()
    interrupt = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, interrupt.set)
        except NotImplementedError:  # pragma: no cover -- non-main thread
            pass

    def progress(line: str) -> None:
        print(line, flush=True)

    task = asyncio.create_task(agent.run(objective, on_progress=progress))
    interrupt_wait = asyncio.create_task(interrupt.wait())
    done, _ = await asyncio.wait({task, interrupt_wait}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        interrupt_wait.cancel()
        return task.result()
    # Local stop: no model round trip, no new CUA action (master plan 7.5).
    print("[STOP] cancellation requested; stopping locally...", flush=True)
    manager.cancel(run_id)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=8)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return VeloResult(
            status=VeloStatus.STOPPED,
            reason="cancelled by user (forced)",
            metrics=VeloMetrics(),
        )
    except asyncio.CancelledError:
        raise


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "reason": result.reason,
        "metrics": result.metrics.as_dict(),
        "steps": [
            {
                "index": step.index,
                "decision": {
                    "status": step.decision.status.value,
                    "action": step.decision.action.value if step.decision.action else None,
                    "target_id": step.decision.target_id,
                    "payload_id": step.decision.payload_id,
                    "key": step.decision.key,
                    "combo": step.decision.combo,
                    "direction": step.decision.direction,
                    "app_name": step.decision.app_name,
                    "confidence": step.decision.confidence,
                    "reason": step.decision.reason,
                },
                "action_result": (
                    {
                        "status": step.action_result.status,
                        "effect": step.action_result.effect,
                        "detail": step.action_result.detail,
                    }
                    if step.action_result
                    else None
                ),
                "scene_changed": (step.verification.changed if step.verification else None),
                "decide_ms": round(step.decide_ms, 1),
                "act_ms": round(step.act_ms, 1),
                "verify_ms": round(step.verify_ms, 1),
            }
            for step in result.steps
        ],
    }


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:  # late Ctrl-C outside the run task
        print("[STOP] cancelled by user", file=sys.stderr)
        raise SystemExit(4) from None
