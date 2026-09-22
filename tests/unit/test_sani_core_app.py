"""End-to-end sani-core app tests over in-memory streams (Sani master doc 13/14).

Fake agents only; the Tauri host is simulated with a fed asyncio.StreamReader
(stdin) and a real os.pipe-backed writer (stdout) so frames flow exactly as
they would over the bundled sidecar process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable
from typing import Any

import orjson
import pytest

from assistant.core.app import SaniCoreApp
from assistant.core.protocol import read_frame
from assistant.core.registry import AgentDescriptor, AgentRegistry

_TIMEOUT = 2.0


class _FakeAgent:
    """Streams two events, then finishes."""

    def __init__(self, agent_id: str = "fake", name: str = "Fake") -> None:
        self._descriptor = AgentDescriptor(id=agent_id, name=name, capabilities=("chat",))

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(
        self,
        text: str,
        *,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        await on_event("step", {"n": 1})
        await on_event("step", {"n": 2})
        return {"status": "done", "echo": text}

    async def cancel(self) -> None:
        return None


class _GatedAgent:
    """Holds its concurrency slot until the test releases it."""

    def __init__(self) -> None:
        self._descriptor = AgentDescriptor(id="gated", name="Gated", capabilities=("chat",))
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(
        self,
        text: str,
        *,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        self.started.set()
        await self.release.wait()
        return {"status": "done"}

    async def cancel(self) -> None:
        return None


class _SlowCancellableAgent:
    """Streams one event, then idles until cancelled."""

    def __init__(self) -> None:
        self._descriptor = AgentDescriptor(id="slow", name="Slow", capabilities=("chat",))
        self.cancel_calls = 0

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(
        self,
        text: str,
        *,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        await on_event("step", {"n": 0})
        while not cancel_check():
            await asyncio.sleep(0.01)
        raise asyncio.CancelledError

    async def cancel(self) -> None:
        self.cancel_calls += 1


class _ExplodingAgent:
    def __init__(self) -> None:
        self._descriptor = AgentDescriptor(id="boom", name="Boom", capabilities=("chat",))

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(
        self,
        text: str,
        *,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        raise RuntimeError("boom")

    async def cancel(self) -> None:
        return None


def _request(request_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": request_id, "method": method, "params": params if params is not None else {}}


class _Sidecar:
    """Drives SaniCoreApp.serve: fed stdin reader, pipe-backed stdout reader."""

    def __init__(
        self,
        app: SaniCoreApp,
        stdout_reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        stdout_transport: asyncio.BaseTransport,
    ) -> None:
        self.stdin = asyncio.StreamReader()
        self.stdout = stdout_reader
        self._writer = writer
        self._stdout_transport = stdout_transport
        self.serve_task: asyncio.Task[None] = asyncio.create_task(app.serve(self.stdin, writer))

    def send(self, payload: dict[str, Any]) -> None:
        body = orjson.dumps(payload)
        self.stdin.feed_data(len(body).to_bytes(4, "big") + body)

    def send_raw(self, raw: bytes) -> None:
        self.stdin.feed_data(raw)

    async def recv(self) -> dict[str, Any]:
        frame = await asyncio.wait_for(read_frame(self.stdout), _TIMEOUT)
        assert frame is not None, "sidecar closed its stdout before sending the expected frame"
        return frame

    async def recv_eof(self) -> None:
        frame = await asyncio.wait_for(read_frame(self.stdout), _TIMEOUT)
        assert frame is None, f"expected EOF, got frame {frame}"

    async def recv_nothing(self) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(read_frame(self.stdout), 0.2)

    async def close(self) -> None:
        self.stdin.feed_eof()
        await asyncio.wait_for(self.serve_task, _TIMEOUT)
        self._writer.close()
        self._stdout_transport.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


async def _start(app: SaniCoreApp) -> _Sidecar:
    loop = asyncio.get_running_loop()
    read_fd, write_fd = os.pipe()
    stdout_reader = asyncio.StreamReader()
    read_transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(stdout_reader), os.fdopen(read_fd, "rb")
    )
    write_transport, protocol = await loop.connect_write_pipe(
        lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader()), os.fdopen(write_fd, "wb")
    )
    writer = asyncio.StreamWriter(write_transport, protocol, None, loop)
    return _Sidecar(app, stdout_reader, writer, read_transport)


async def test_agents_list_returns_registered_descriptors() -> None:
    registry = AgentRegistry()
    registry.register(_FakeAgent())
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(_request("1", "agents.list"))
        frame = await sidecar.recv()
        assert frame == {
            "type": "response",
            "id": "1",
            "ok": True,
            "result": {"agents": [{"id": "fake", "name": "Fake", "capabilities": ["chat"]}]},
            "error": "",
        }
    finally:
        await sidecar.close()


async def test_run_start_streams_events_then_final_response() -> None:
    registry = AgentRegistry()
    registry.register(_FakeAgent())
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(_request("r1", "run.start", {"agent_id": "fake", "text": "hi"}))
        first = await sidecar.recv()
        second = await sidecar.recv()
        final = await sidecar.recv()
        assert first["type"] == "event"
        assert first["kind"] == "step"
        assert first["data"] == {"n": 1}
        assert first["run_id"]
        assert second == {
            "type": "event",
            "run_id": first["run_id"],
            "kind": "step",
            "data": {"n": 2},
        }
        assert final == {
            "type": "response",
            "id": "r1",
            "ok": True,
            "result": {"status": "done", "echo": "hi"},
            "error": "",
        }
    finally:
        await sidecar.close()


async def test_run_start_unknown_agent_gets_error_response() -> None:
    sidecar = await _start(SaniCoreApp(AgentRegistry()))
    try:
        sidecar.send(_request("r1", "run.start", {"agent_id": "ghost", "text": "hi"}))
        frame = await sidecar.recv()
        assert frame["type"] == "response"
        assert frame["id"] == "r1"
        assert frame["ok"] is False
        assert "unknown agent id" in frame["error"]
    finally:
        await sidecar.close()


async def test_run_start_bad_params_gets_error_response() -> None:
    registry = AgentRegistry()
    registry.register(_FakeAgent())
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(_request("r1", "run.start", {"agent_id": "fake"}))
        frame = await sidecar.recv()
        assert frame["type"] == "response"
        assert frame["id"] == "r1"
        assert frame["ok"] is False
        assert "agent_id" in frame["error"]
        # The session must survive a bad-params request.
        sidecar.send(_request("2", "agents.list"))
        assert (await sidecar.recv())["ok"] is True
    finally:
        await sidecar.close()


async def test_unknown_method_gets_error_response() -> None:
    registry = AgentRegistry()
    registry.register(_FakeAgent())
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(_request("7", "workflow.plan"))
        frame = await sidecar.recv()
        assert frame == {
            "type": "response",
            "id": "7",
            "ok": False,
            "result": None,
            "error": "unknown method",
        }
    finally:
        await sidecar.close()


async def test_concurrency_cap_rejects_extra_run() -> None:
    gated = _GatedAgent()
    registry = AgentRegistry()
    registry.register(gated)
    sidecar = await _start(SaniCoreApp(registry, max_concurrent_runs=1))
    try:
        sidecar.send(_request("r1", "run.start", {"agent_id": "gated", "text": "a"}))
        await asyncio.wait_for(gated.started.wait(), _TIMEOUT)
        sidecar.send(_request("r2", "run.start", {"agent_id": "gated", "text": "b"}))
        rejected = await sidecar.recv()
        assert rejected["type"] == "response"
        assert rejected["id"] == "r2"
        assert rejected["ok"] is False
        assert "limit" in rejected["error"]
        gated.release.set()
        done = await sidecar.recv()
        assert done["id"] == "r1"
        assert done["ok"] is True
    finally:
        await sidecar.close()


async def test_run_cancel_stops_slow_agent_and_reports_cancelled() -> None:
    agent = _SlowCancellableAgent()
    registry = AgentRegistry()
    registry.register(agent)
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(_request("r1", "run.start", {"agent_id": "slow", "text": "x"}))
        started = await sidecar.recv()
        assert started["kind"] == "step"
        sidecar.send(_request("r2", "run.cancel", {"run_id": started["run_id"]}))
        frames = [await sidecar.recv() for _ in range(3)]
        acks = [f for f in frames if f["type"] == "response" and f["id"] == "r2"]
        cancelled = [f for f in frames if f["type"] == "event" and f["kind"] == "cancelled"]
        finals = [f for f in frames if f["type"] == "response" and f["id"] == "r1"]
        assert len(acks) == 1 and acks[0]["ok"] is True
        assert len(cancelled) == 1 and cancelled[0]["run_id"] == started["run_id"]
        assert len(finals) == 1
        assert finals[0]["ok"] is True
        assert finals[0]["result"] == {"status": "cancelled"}
        # The cancelled event always precedes the run's own response.
        assert frames.index(cancelled[0]) < frames.index(finals[0])
        assert agent.cancel_calls == 1
        await sidecar.recv_nothing()
    finally:
        await sidecar.close()


async def test_run_cancel_unknown_run_returns_error() -> None:
    sidecar = await _start(SaniCoreApp(AgentRegistry()))
    try:
        sidecar.send(_request("r1", "run.cancel", {"run_id": "ghost"}))
        frame = await sidecar.recv()
        assert frame["type"] == "response"
        assert frame["id"] == "r1"
        assert frame["ok"] is False
        assert "unknown run" in frame["error"]
    finally:
        await sidecar.close()


async def test_agent_failure_yields_error_response_and_session_survives() -> None:
    registry = AgentRegistry()
    registry.register(_ExplodingAgent())
    registry.register(_FakeAgent(name="Fine"))
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(_request("r1", "run.start", {"agent_id": "boom", "text": "x"}))
        frame = await sidecar.recv()
        assert frame["type"] == "response"
        assert frame["id"] == "r1"
        assert frame["ok"] is False
        assert "boom" in frame["error"]
        sidecar.send(_request("r2", "run.start", {"agent_id": "fake", "text": "x"}))
        assert (await sidecar.recv())["kind"] == "step"
        assert (await sidecar.recv())["kind"] == "step"
        final = await sidecar.recv()
        assert final["type"] == "response"
        assert final["id"] == "r2"
        assert final["ok"] is True
    finally:
        await sidecar.close()


async def test_malformed_frame_terminates_serve_and_closes_stdout() -> None:
    sidecar = await _start(SaniCoreApp(AgentRegistry()))
    try:
        sidecar.send_raw((5).to_bytes(4, "big") + b"nope!")
        await asyncio.wait_for(sidecar.serve_task, _TIMEOUT)
        assert sidecar.serve_task.done()
        assert not sidecar.serve_task.cancelled()
        assert sidecar.serve_task.exception() is None
        await sidecar.recv_eof()
    finally:
        await sidecar.close()


async def test_non_object_frame_terminates_serve() -> None:
    sidecar = await _start(SaniCoreApp(AgentRegistry()))
    try:
        sidecar.send_raw((2).to_bytes(4, "big") + b"[]")
        await asyncio.wait_for(sidecar.serve_task, _TIMEOUT)
        assert sidecar.serve_task.done()
        assert sidecar.serve_task.exception() is None
    finally:
        await sidecar.close()


async def test_invalid_concurrency_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_concurrent_runs"):
        SaniCoreApp(AgentRegistry(), max_concurrent_runs=0)
