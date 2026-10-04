"""Run one task in the real ZCode app, through the same toolkit the Deep Agent uses.

    ZCODE_MODE=window uv run python scripts/zcode_run.py --dir ~/Documents/proj \\
        --mode edit --prompt "Create hello.txt containing hi" [--enable-control]

``--enable-control`` records the user's standing yes to closing and reopening ZCode (the same
switch Settings will offer). State (control flag, task mapping) lives in $SANI_DATA_DIR.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from assistant.claude_code import context
from assistant.claude_code.toolkit import ClaudeCodeToolkit
from assistant.core.protocol import AGENT_PROGRESS
from assistant.settings import Settings


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--mode", default="edit", choices=["read", "edit", "run"])
    parser.add_argument("--conversation", default="script-chat")
    parser.add_argument("--new-session", action="store_true")
    parser.add_argument("--cancel-after", type=float, default=0.0)
    parser.add_argument("--enable-control", action="store_true")
    parser.add_argument("--disable-control", action="store_true")
    parser.add_argument("--keep-open", action="store_true", help="leave the debug port open")
    args = parser.parse_args()

    settings = Settings(zcode_mode="window", zcode_cli_enabled=True)
    from assistant.coding_agents.zcode_cdp.runner import ZCodeWindowRunner
    from assistant.coding_agents.zcode_window import ZCodeWindowBackend

    backend = ZCodeWindowBackend(settings.sani_data_dir)
    toolkit = ClaudeCodeToolkit(
        settings,
        backend=backend,
        runner_factory=lambda _b, _v: ZCodeWindowRunner(
            backend.control,
            allow_any_provider=settings.zcode_allowed_providers == "any",
            selection=backend.selection,
            remember=backend.control.remember,
        ),
    )
    if args.enable_control:
        await backend.control.set_enabled(True)
    if args.disable_control:
        await backend.control.set_enabled(False)

    async def sink(kind: str, data: dict[str, Any]) -> None:
        if kind == AGENT_PROGRESS and "step" in data:
            step = data["step"]
            print(f"  [{step.get('status', ''):9}] {step.get('kind', 'step'):7} {step['label']}")

    context.conversation_id.set(args.conversation)
    context.event_sink.set(sink)
    task = asyncio.create_task(
        toolkit.run(
            task=args.prompt,
            project_dir=str(Path(args.dir).expanduser()),
            mode=args.mode,
            new_session=args.new_session,
            purpose="Script run",
        )
    )
    if args.cancel_after:
        await asyncio.sleep(args.cancel_after)
        task.cancel()
    try:
        print(await task)
    except asyncio.CancelledError:
        print("(cancelled)")
    if not args.keep_open:
        await backend.control.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
