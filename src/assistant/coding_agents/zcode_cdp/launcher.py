"""Start ZCode with a private debug port, prove the port is ZCode's, and put it back.

The debug port has no password, so it is: random, ``127.0.0.1`` only, owned by a verified
ZCode process, open only while Sani reads, and closed again by relaunching ZCode normally.
It only works on a fresh launch (ZCode is single-instance), so a running ZCode must be quit
first; the caller must have the user's consent before that.
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assistant.coding_agents.zcode_cdp.client import CdpError, http_get_json

APP_NAME = "ZCode"
APP_PATH = Path("/Applications/ZCode.app")

Run = Callable[[list[str]], Awaitable[tuple[int, str]]]


async def run_quiet(command: list[str]) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 15)
    except (OSError, TimeoutError):
        return 1, ""
    return proc.returncode or 0, out.decode("utf-8", "replace")


@dataclass
class ZCodeLauncher:
    run: Run = run_quiet
    get: Callable[[str], Awaitable[Any]] = http_get_json
    pick_port: Callable[[], int] = field(default=lambda: random.randint(20000, 45000))
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    app_path: Path = APP_PATH
    port: int = 0

    def installed(self) -> bool:
        return self.app_path.exists()

    async def pids(self) -> list[int]:
        code, out = await self.run(["pgrep", "-x", APP_NAME])
        return [int(p) for p in out.split() if p.isdigit()] if code == 0 else []

    async def running(self) -> bool:
        return bool(await self.pids())

    async def _wait_gone(self, seconds: float = 15.0) -> bool:
        waited = 0.0
        while waited < seconds:
            if not await self.running():
                return True
            await self.sleep(0.5)
            waited += 0.5
        return not await self.running()

    async def quit(self) -> bool:
        """Ask ZCode to quit the polite way (it may prompt about unsaved work)."""
        await self.run(["osascript", "-e", f'tell application "{APP_NAME}" to quit'])
        return await self._wait_gone()

    async def start_with_port(self) -> int:
        """Launch ZCode with a private port. ZCode must not already be running."""
        if not self.installed():
            raise CdpError("ZCode is not installed")
        if await self.running():
            raise CdpError("ZCode is already running; it has to be closed first")
        self.port = self.pick_port()
        code, _ = await self.run(
            ["open", "-a", APP_NAME, "--args", f"--remote-debugging-port={self.port}"]
        )
        if code != 0:
            raise CdpError("ZCode could not be started")
        await self._wait_ready()
        await self.verify_listener()
        return self.port

    async def _wait_ready(self, seconds: float = 40.0) -> None:
        waited = 0.0
        while waited < seconds:
            try:
                await self.get(f"http://127.0.0.1:{self.port}/json/version")
                return
            except Exception:
                await self.sleep(0.5)
                waited += 0.5
        raise CdpError("ZCode did not open its debug port in time")

    async def version(self) -> str:
        """The app version from its own user agent, e.g. ``3.14.4``."""
        info = await self.get(f"http://127.0.0.1:{self.port}/json/version")
        found = re.search(r"ZCode/(\d+\.\d+\.\d+)", str(info.get("User-Agent", "")))
        return found.group(1) if found else ""

    async def verify_listener(self) -> None:
        """The process listening on the port must be ZCode, on 127.0.0.1 only."""
        code, out = await self.run(["lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN", "-Fpn"])
        if code != 0 or not out:
            raise CdpError("could not confirm who owns the debug port")
        pids = {int(line[1:]) for line in out.splitlines() if line.startswith("p")}
        names = [line[1:] for line in out.splitlines() if line.startswith("n")]
        if any(not name.startswith("127.0.0.1:") for name in names):
            raise CdpError("the debug port is open beyond this Mac; refusing to use it")
        if not pids or not pids <= set(await self.pids()):
            raise CdpError("the debug port is not owned by ZCode; refusing to use it")

    async def restore_normal(self) -> bool:
        """Quit the port-enabled ZCode and open it again the normal way."""
        gone = await self.quit()
        if not gone:
            return False
        await self.run(["open", "-a", APP_NAME])
        self.port = 0
        return True
