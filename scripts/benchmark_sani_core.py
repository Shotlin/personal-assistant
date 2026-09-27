"""sani-core latency benchmark (master plan stage 1: honest per-stage timings).

Replaces the retired HTTP-gateway benchmark: the shipping path is the Tauri
host's private framed-JSON IPC to the sani-core sidecar, so this script spawns
the sidecar directly and speaks that protocol. It separates four measurements
(master plan section 9) instead of reporting one end-to-end blur:

- input_accepted_ms:   run.start written -> first frame back
- first_event_ms:      -> first progress/token event (acknowledgement, never
                        to be reported as completion)
- first_action_ms:     -> first CUA action progress line (desktop work began)
- completed_ms:        -> terminal response (the only "done" that counts)

Each response also carries the sidecar's engine identity, route, and
verification flag, so a run can always say which build produced it.

Usage:
    uv run python scripts/benchmark_sani_core.py --message "Open Safari" \
        [--runs 5] [--agent velo] [--core-cmd ".venv/bin/python -m assistant.core"]

Requires the same environment the sidecar needs (CUA_ENABLED, model keys, ...).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from typing import Any

sys.path.insert(0, "src")

from assistant.core.protocol import read_frame, write_frame  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default="Open Safari")
    parser.add_argument("--agent", default="velo", help="velo | deep")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument(
        "--core-cmd",
        default=".venv/bin/python -m assistant.core",
        help="command that starts the sidecar (framed JSON on stdio)",
    )
    return parser.parse_args()


class Sidecar:
    """One spawned sani-core process over private framed-JSON stdio."""

    def __init__(self, command: str) -> None:
        self._argv = command.split()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr,
        )
        self._reader = self._proc.stdout
        self._writer = self._proc.stdin

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._writer is not None and self._reader is not None
        await write_frame(self._writer, payload)
        return await read_frame(self._reader)

    async def close(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except TimeoutError:
            self._proc.kill()


async def run_once(sidecar: Sidecar, agent: str, message: str, index: int) -> dict[str, Any]:
    started = time.monotonic()
    response = await sidecar.request(
        {
            "type": "request",
            "id": str(index),
            "method": "run.start",
            "params": {"agent_id": agent, "text": message, "thread_id": f"bench-{index}"},
        }
    )
    if response.get("type") != "response":
        raise RuntimeError(f"unexpected frame: {json.dumps(response)[:200]}")
    if not response.get("ok"):
        raise RuntimeError(f"run.start rejected: {response.get('error')}")
    input_accepted_ms = (time.monotonic() - started) * 1000

    first_event_ms: int | None = None
    first_action_ms: int | None = None
    result: dict[str, Any] | None = None
    while result is None:
        frame = await _next_frame(sidecar)
        if frame is None:
            raise RuntimeError("sidecar closed the connection mid-run")
        if frame.get("type") == "event":
            now_ms = int((time.monotonic() - started) * 1000)
            if first_event_ms is None:
                first_event_ms = now_ms
            if frame.get("kind") == "agent.progress" and first_action_ms is None:
                first_action_ms = now_ms
        elif frame.get("type") == "response" and frame.get("id") == str(index):
            result = frame

    payload = result.get("result") or {}
    return {
        "input_accepted_ms": int(input_accepted_ms),
        "first_event_ms": first_event_ms,
        "first_action_ms": payload.get("timing", {}).get("first_action_ms") or first_action_ms,
        "completed_ms": (payload.get("timing") or {}).get("completed_ms"),
        "route": payload.get("route"),
        "verified": payload.get("verified"),
        "engine_revision": (payload.get("engine") or {}).get("revision") or "unknown",
        "response": str(payload.get("response", ""))[:120],
    }


async def _next_frame(sidecar: Sidecar) -> dict[str, Any] | None:
    # Events stream unsolicited on stdout; read_frame is the shared contract.
    assert sidecar._reader is not None
    return await read_frame(sidecar._reader)


def _summary(label: str, values: list[int | None]) -> str:
    clean = [v for v in values if v is not None]
    if not clean:
        return f"{label}: n/a"
    if len(clean) == 1:
        return f"{label}: {clean[0]}ms"
    return (
        f"{label}: p50 {statistics.median(clean):.0f}ms  "
        f"p95 {sorted(clean)[max(0, int(len(clean) * 0.95) - 1)]:.0f}ms  "
        f"max {max(clean)}ms"
    )


async def main_async() -> int:
    args = _parse_args()
    sidecar = Sidecar(args.core_cmd)
    await sidecar.start()

    agents = await sidecar.request(
        {"type": "request", "id": "agents", "method": "agents.list", "params": {}}
    )
    result = agents.get("result") or {}
    engine = result.get("engine") or {}
    roster = [a.get("id") for a in result.get("agents") or []]
    print(f"sidecar engine: revision={engine.get('revision') or 'unknown'} roster={roster}")
    if args.agent not in roster:
        print(f"error: agent {args.agent!r} is not registered", file=sys.stderr)
        await sidecar.close()
        return 2

    runs: list[dict[str, Any]] = []
    for index in range(1, args.runs + 1):
        try:
            runs.append(await run_once(sidecar, args.agent, args.message, index))
        except Exception as exc:  # noqa: BLE001 -- one failed run is reported, not fatal
            print(f"run {index}: FAILED ({exc})", file=sys.stderr)
    await sidecar.close()

    print(f"\n{args.runs} runs of {args.message!r} via {args.agent}:")
    for run in runs:
        print(
            f"  accepted {run['input_accepted_ms']:>5}ms  "
            f"first_event {str(run['first_event_ms']):>5}  "
            f"first_action {str(run['first_action_ms']):>5}  "
            f"completed {str(run['completed_ms']):>6}  "
            f"route={run['route']} verified={run['verified']}"
        )
    if runs:
        print()
        print(_summary("input_accepted", [r["input_accepted_ms"] for r in runs]))
        print(_summary("first_event   ", [r["first_event_ms"] for r in runs]))
        print(_summary("first_action  ", [r["first_action_ms"] for r in runs]))
        print(_summary("completed     ", [r["completed_ms"] for r in runs]))
    return 0 if runs else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
