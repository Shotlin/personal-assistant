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
        self.threads: list[str] = []

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        self.threads.append(thread_id)
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
        thread_id: str,
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
        thread_id: str,
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
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        raise RuntimeError("boom")

    async def cancel(self) -> None:
        return None


class _StallingAgent:
    """Never finishes: models a hung provider call or a wedged tool."""

    def __init__(self) -> None:
        self._descriptor = AgentDescriptor(id="stall", name="Stall", capabilities=("chat",))
        self.cancel_calls = 0

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {"status": "done"}

    async def cancel(self) -> None:
        self.cancel_calls += 1


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

    async def recv_response(self, request_id: str, limit: int = 8) -> dict[str, Any]:
        """Next response frame for `request_id`, skipping interleaved events."""
        for _ in range(limit):
            frame = await self.recv()
            if frame["type"] == "response" and frame.get("id") == request_id:
                return frame
        raise AssertionError(f"no response frame for request {request_id!r}")


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
        frames = [await sidecar.recv() for _ in range(5)]
        kinds = [frame.get("kind") for frame in frames if frame["type"] == "event"]
        assert kinds == ["agent.started", "step", "step", "agent.completed"]
        run_id = frames[0]["run_id"]
        # Identity is on every frame, not just the first: the UI never has to
        # remember which agent a run started with to label a later event.
        for frame in frames[:4]:
            assert frame["run_id"] == run_id
            assert frame["agent_id"] == "fake"
        assert frames[0]["data"] == {"text": "hi"}
        assert frames[1]["data"] == {"n": 1}
        assert frames[3]["data"] == {"status": "done"}
        assert frames[4] == {
            "type": "response",
            "id": "r1",
            "ok": True,
            "result": {"status": "done", "echo": "hi"},
            "error": "",
        }
    finally:
        await sidecar.close()


async def test_run_start_threads_the_conversation_id_to_the_agent() -> None:
    agent = _FakeAgent()
    registry = AgentRegistry()
    registry.register(agent)
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(
            _request("r1", "run.start", {"agent_id": "fake", "text": "a", "thread_id": "c-77"})
        )
        await sidecar.recv_response("r1")
        assert agent.threads == ["c-77"]
        # A second turn on the same conversation must reuse the same thread.
        sidecar.send(
            _request("r2", "run.start", {"agent_id": "fake", "text": "b", "thread_id": "c-77"})
        )
        await sidecar.recv_response("r2")
        assert agent.threads == ["c-77", "c-77"]
        # Omitting it is a one-shot thread, never another conversation's.
        sidecar.send(_request("r3", "run.start", {"agent_id": "fake", "text": "c"}))
        await sidecar.recv_response("r3")
        assert agent.threads == ["c-77", "c-77", ""]
    finally:
        await sidecar.close()


async def test_run_start_rejects_non_string_thread_id_and_keeps_session() -> None:
    registry = AgentRegistry()
    registry.register(_FakeAgent())
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(_request("r1", "run.start", {"agent_id": "fake", "text": "a", "thread_id": 7}))
        frame = await sidecar.recv()
        assert frame["ok"] is False
        assert "thread_id" in frame["error"]
        sidecar.send(_request("2", "agents.list"))
        assert (await sidecar.recv())["ok"] is True
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
        rejected = await sidecar.recv_response("r2")
        assert rejected["ok"] is False
        assert "limit" in rejected["error"]
        gated.release.set()
        done = await sidecar.recv_response("r1")
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
        opened = [await sidecar.recv() for _ in range(2)]
        assert [frame["kind"] for frame in opened] == ["agent.started", "step"]
        run_id = opened[0]["run_id"]
        sidecar.send(_request("r2", "run.cancel", {"run_id": run_id}))
        frames = [await sidecar.recv() for _ in range(3)]
        acks = [f for f in frames if f["type"] == "response" and f["id"] == "r2"]
        cancelled = [f for f in frames if f["type"] == "event" and f["kind"] == "agent.cancelled"]
        finals = [f for f in frames if f["type"] == "response" and f["id"] == "r1"]
        assert len(acks) == 1 and acks[0]["ok"] is True
        assert len(cancelled) == 1
        assert cancelled[0]["run_id"] == run_id
        assert cancelled[0]["agent_id"] == "slow"
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
        started, failed, response = [await sidecar.recv() for _ in range(3)]
        assert started["kind"] == "agent.started"
        # A failure names the agent that failed, so the UI can stop animating
        # the right avatar instead of leaving it stuck in "working".
        assert failed["kind"] == "agent.failed"
        assert failed["agent_id"] == "boom"
        assert "boom" in failed["data"]["error"]
        assert response["type"] == "response"
        assert response["ok"] is False
        assert "boom" in response["error"]
        sidecar.send(_request("r2", "run.start", {"agent_id": "fake", "text": "x"}))
        kinds = [(await sidecar.recv())["kind"] for _ in range(4)]
        assert kinds == ["agent.started", "step", "step", "agent.completed"]
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


async def test_host_named_run_id_is_used_verbatim_and_rejects_collisions() -> None:
    gated = _GatedAgent()
    registry = AgentRegistry()
    registry.register(gated)
    sidecar = await _start(SaniCoreApp(registry))
    try:
        sidecar.send(
            _request("r1", "run.start", {"agent_id": "gated", "text": "hi", "run_id": "run-7"})
        )
        started = await sidecar.recv()
        # The host named it, so it can cancel this exact run without waiting
        # for a frame that tells it the id.
        assert started["run_id"] == "run-7"
        assert started["kind"] == "agent.started"
        await asyncio.wait_for(gated.started.wait(), _TIMEOUT)
        sidecar.send(
            _request("r2", "run.start", {"agent_id": "gated", "text": "hi", "run_id": "run-7"})
        )
        clash = await sidecar.recv_response("r2")
        assert clash["ok"] is False
        assert "run id in use" in clash["error"]
        gated.release.set()
        assert (await sidecar.recv_response("r1"))["ok"] is True, "live run untouched"
        sidecar.send(_request("r3", "run.start", {"agent_id": "gated", "text": "x", "run_id": 5}))
        bad = await sidecar.recv_response("r3")
        assert bad["ok"] is False
        assert "run_id" in bad["error"]
    finally:
        await sidecar.close()


async def test_run_cancel_rejects_non_string_run_id() -> None:
    sidecar = await _start(SaniCoreApp(AgentRegistry()))
    try:
        sidecar.send(_request("r1", "run.cancel", {"run_id": 7}))
        frame = await sidecar.recv()
        assert frame["ok"] is False
        assert "run_id" in frame["error"]
    finally:
        await sidecar.close()


async def test_invalid_concurrency_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_concurrent_runs"):
        SaniCoreApp(AgentRegistry(), max_concurrent_runs=0)


async def test_invalid_wall_clock_is_rejected() -> None:
    with pytest.raises(ValueError, match="run_wall_clock_seconds"):
        SaniCoreApp(AgentRegistry(), run_wall_clock_seconds=0)


async def test_run_wall_clock_terminates_a_hung_agent() -> None:
    agent = _StallingAgent()
    registry = AgentRegistry()
    registry.register(agent)
    sidecar = await _start(SaniCoreApp(registry, run_wall_clock_seconds=0.05))
    try:
        sidecar.send(_request("r1", "run.start", {"agent_id": "stall", "text": "x"}))
        started = await sidecar.recv()
        assert started["kind"] == "agent.started"
        failed = await sidecar.recv()
        # The client learns which agent died, and the slot frees: a single-owner
        # desktop host must never wait out a wedged run.
        assert failed["kind"] == "agent.failed"
        assert failed["agent_id"] == "stall"
        assert "wall-clock" in failed["data"]["error"]
        response = await sidecar.recv()
        assert response["type"] == "response"
        assert response["ok"] is False
        assert "wall-clock" in response["error"]
        assert agent.cancel_calls == 1
        # The session and its run table survive: the next turn still works.
        sidecar.send(_request("2", "agents.list"))
        assert (await sidecar.recv())["ok"] is True
    finally:
        await sidecar.close()
