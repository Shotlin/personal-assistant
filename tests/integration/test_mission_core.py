"""Mission-mode sani-core integration (T06, file 06 I1).

Real framed-IPC dispatch through SaniCoreApp with the mission-backed
registry (JARVIS_MISSIONS_ENABLED): request dedup over the wire, honest
status for escalations, and cheap chat for questions. The desktop world is
a fake; the shipping bundle demonstration stays behind L1.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import orjson
import pytest

from assistant.core.app import SaniCoreApp
from assistant.settings import Settings

_TIMEOUT = 5.0


class _FakeStreamAgent:
    """A Deep graph double: streams one token then finishes."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def astream(self, *_args: Any, **_kwargs: Any) -> Any:
        from langchain_core.messages import AIMessageChunk

        for index in range(3):
            yield AIMessageChunk(content=f"chunk-{index}", id="m1")
            await asyncio.sleep(0.001)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path / "sani-data"),
        cua_enabled=False,
        openrouter_api_key="fixture-not-a-secret",
        jarvis_missions_enabled=True,
        model_provider="openrouter",
        model_name="fixture/model",
        agent_builder_echo="",  # not a real field; placeholder ignored
    )


class _Sidecar:
    """Feeds SaniCoreApp.serve over a real os.pipe (same as the app tests).

    stdout is pumped by a dedicated thread into a queue: a timed-out read
    must never consume data meant for the next one.
    """

    def __init__(self, app: SaniCoreApp) -> None:
        import queue as queue_module
        import threading

        self.stdin = asyncio.StreamReader()
        self.stdout_r, self.stdout_w = os.pipe()
        self._frames: queue_module.Queue[bytes] = queue_module.Queue()
        self._buffer = b""
        self._pump = threading.Thread(target=self._pump_stdout, daemon=True)
        self._pump.start()
        self.serve_task: asyncio.Task[None] = asyncio.create_task(
            app.serve(  # type: ignore[arg-type]
                self.stdin, _PipeWriter(os.fdopen(self.stdout_w, "wb"))  # type: ignore[arg-type]
            )
        )

    def _pump_stdout(self) -> None:
        while True:
            try:
                chunk = os.read(self.stdout_r, 65536)
            except OSError:
                break
            if not chunk:
                break
            self._buffer += chunk
            while len(self._buffer) >= 4:
                length = int.from_bytes(self._buffer[:4], "big")
                if len(self._buffer) < 4 + length:
                    break
                self._frames.put(self._buffer[4 : 4 + length])
                self._buffer = self._buffer[4 + length :]

    async def send(self, frame: dict[str, Any]) -> None:
        body = orjson.dumps(frame)
        self.stdin.feed_data(len(body).to_bytes(4, "big") + body)
        await asyncio.sleep(0)

    async def read(self) -> dict[str, Any] | None:
        import queue as queue_module

        loop = asyncio.get_running_loop()

        def take() -> bytes:
            try:
                return self._frames.get(timeout=_TIMEOUT)
            except queue_module.Empty as exc:
                raise TimeoutError from exc

        data = await loop.run_in_executor(None, take)
        return orjson.loads(data)

    async def close(self) -> None:
        self.stdin.feed_eof()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(self.serve_task, _TIMEOUT)
        os.close(self.stdout_r)


class _PipeWriter:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def write(self, data: bytes) -> None:
        self._raw.write(data)

    def is_closing(self) -> bool:
        return False

    async def drain(self) -> None:
        self._raw.flush()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._raw.flush()

    async def wait_closed(self) -> None:
        return None


def _mission_registry(tmp_path: Path) -> tuple[Any, Settings, Any]:
    """The mission-composed registry with a fake Deep transport.

    Returns (registry, settings, mission_provider) — the same provider the
    production __main__ wires into SaniCoreApp.
    """
    from assistant.core.agents import CoreResources, DeepAgentEntry, MissionEntry, RuntimeProvider
    from assistant.core.registry import AgentRegistry

    settings = _settings(tmp_path)
    deep_double = _FakeStreamAgent()
    provider = RuntimeProvider(settings, agent_builder=_returns(deep_double))
    deep = DeepAgentEntry(settings, provider=provider)
    resources = CoreResources(
        settings=settings, provider=provider, deep=deep, registry=AgentRegistry()
    )
    velo = MissionEntry(settings, resources=resources, deep=deep)
    resources.mission_entry = velo
    resources.registry.register(deep)
    resources.registry.register(velo)
    return resources.registry, settings, resources.mission_service


def _returns(value: Any) -> Callable[[], Any]:
    async def build() -> Any:
        return value

    return build


@pytest.fixture()
async def sidecar(tmp_path: Path) -> Any:
    registry, _settings, mission_provider = _mission_registry(tmp_path)
    app = SaniCoreApp(registry, status_provider=None)
    sc = _Sidecar(app)
    yield sc
    await sc.close()


async def test_registry_lists_mission_capabilities(tmp_path: Path) -> None:
    registry, _settings, _provider = _mission_registry(tmp_path)
    ids = {a.id for a in registry.list()}
    assert ids == {"deep", "velo"}
    velo = registry.get("velo")
    assert "missions" in velo.descriptor.capabilities


async def test_question_over_ipc_is_cheap_chat(sidecar: Any) -> None:
    await sidecar.send(
        {"type": "request", "id": "q1", "method": "run.start",
         "params": {"agent_id": "velo", "text": "What is a contract?", "thread_id": "c1",
                    "run_id": "run-q1"}}
    )
    frames = []
    while True:
        frame = await sidecar.read()
        frames.append(frame)
        if frame.get("type") == "response":
            break
    response = frames[-1]
    assert response["ok"] is True
    assert response["result"]["status"] == "done", "a question answers in chat"
    assert "mission_status" not in response["result"], "chat turns are not missions"


async def test_action_over_ipc_becomes_a_mission_and_reports_honestly(sidecar: Any) -> None:
    await sidecar.send(
        {"type": "request", "id": "m1", "method": "run.start",
         "params": {"agent_id": "velo", "text": "open safari", "thread_id": "c1",
                    "run_id": "run-m1"}}
    )
    frames = []
    while True:
        frame = await sidecar.read()
        frames.append(frame)
        if frame.get("type") == "response":
            break
    response = frames[-1]
    assert response["ok"] is True
    result = response["result"]
    # No desktop in this environment: the unit escalates, and the wire result
    # is honest about it (never "done").
    assert result["status"] != "done"
    assert result["mission_status"] in {"PAUSED", "BLOCKED", "NEEDS_APPROVAL"}
    assert result["verified"] is False
    assert result["mission_id"]


async def test_duplicate_request_over_ipc_dedups(sidecar: Any) -> None:
    frame = {"type": "request", "id": "d1", "method": "run.start",
             "params": {"agent_id": "velo", "text": "open safari", "thread_id": "c1",
                        "run_id": "run-dup"}}
    await sidecar.send(frame)
    frames = []
    while True:
        f = await sidecar.read()
        frames.append(f)
        if f.get("type") == "response":
            break
    first = frames[-1]["result"]
    await sidecar.send({**frame, "id": "d2"})
    while True:
        f = await sidecar.read()
        frames.append(f)
        if f.get("type") == "response" and f.get("id") == "d2":
            break
    second = frames[-1]["result"]
    assert first["mission_id"] == second["mission_id"], (
        "the same stable request id maps to one durable mission"
    )
