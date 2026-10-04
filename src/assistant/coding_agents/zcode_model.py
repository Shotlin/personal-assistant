"""Which model would ZCode use for the next run, asked of ZCode itself.

``zcode -p`` has no model flag: it uses whatever default ZCode resolves, which can be
the user's own Z.ai plan or a custom provider with someone else's key. To honour "only my
Z.ai plan", Sani asks ZCode's own app-server (the stdio protocol the ZCode app uses) what
it would use, before it spends anything. That question costs no tokens, but ZCode keeps an
empty session for it, so the answer is cached for a short time and the probe sessions live
in one folder under Sani's data directory.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: ZCode's provider ids for Z.ai plans (Start, Individual, Team, Idle). BigModel (China) is
#: a different account family and is NOT included.
ZAI_PREFIX = "account:zai-"
_CACHE_SECONDS = 120.0
_PROBE_SECONDS = 30.0
_cache: dict[str, tuple[float, ModelProbe | None]] = {}


@dataclass(frozen=True, slots=True)
class ModelProbe:
    provider: str
    model: str
    #: (provider id, model id, provider label) for everything ZCode offers.
    available: tuple[tuple[str, str, str], ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.model}" if self.provider else "none"


def is_zai_plan(provider_id: str) -> bool:
    return provider_id.startswith(ZAI_PREFIX)


def describe(provider_id: str) -> str:
    if is_zai_plan(provider_id):
        return "your Z.ai plan"
    if provider_id.startswith("account:"):
        return "another ZCode plan account"
    return "a custom provider"


async def _talk(command: list[str], env: dict[str, str], workspace: Path) -> ModelProbe | None:
    workspace.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *command,
        "app-server",
        cwd=str(workspace),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        start_new_session=True,
        limit=8 * 1024 * 1024,
    )
    assert process.stdin is not None and process.stdout is not None  # noqa: S101

    def send(message: dict[str, Any]) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode())  # type: ignore[union-attr]

    try:
        send(
            {
                "id": 1,
                "method": "session/create",
                "params": {
                    "workspace": {"workspacePath": str(workspace), "workspaceKey": str(workspace)},
                    "mode": "plan",
                },
            }
        )
        await process.stdin.drain()
        while True:
            raw = await process.stdout.readline()
            if not raw:
                return None
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("method") == "session/requestRuntimePreferences":
                send(
                    {
                        "id": message.get("id"),
                        "result": {
                            "nativeSearchEnhancementsEnabled": False,
                            "memoryEnabled": False,
                            "askUserQuestionAutoResolutionEnabled": False,
                        },
                    }
                )
                await process.stdin.drain()
                continue
            if message.get("id") == 1 and "method" not in message:
                return _parse(message)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=3)


def _parse(message: dict[str, Any]) -> ModelProbe | None:
    result = message.get("result")
    if not isinstance(result, dict):
        return None
    settings = result.get("settings")
    model = settings.get("model") if isinstance(settings, dict) else None
    if not isinstance(model, dict):
        return None
    raw_current = model.get("current")
    current: dict[str, Any] = raw_current if isinstance(raw_current, dict) else {}
    available: list[tuple[str, str, str]] = []
    for entry in model.get("available") or []:
        ref = entry.get("ref") if isinstance(entry, dict) else None
        if isinstance(ref, dict):
            available.append(
                (
                    str(ref.get("providerId") or ""),
                    str(ref.get("modelId") or ""),
                    str(entry.get("providerLabel") or ""),
                )
            )
    return ModelProbe(
        provider=str(current.get("providerId") or ""),
        model=str(current.get("modelId") or ""),
        available=tuple(available),
    )


async def probe_default_model(
    command: list[str], env: dict[str, str], workspace: Path, *, fresh: bool = False
) -> ModelProbe | None:
    """What ZCode would use right now, or None if it could not be confirmed."""
    key = "\0".join(command)
    cached = _cache.get(key)
    if cached is not None and not fresh and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    try:
        probe = await asyncio.wait_for(_talk(command, env, workspace), timeout=_PROBE_SECONDS)
    except (TimeoutError, OSError):
        probe = None
    _cache[key] = (time.monotonic(), probe)
    return probe


def clear_cache() -> None:
    _cache.clear()
