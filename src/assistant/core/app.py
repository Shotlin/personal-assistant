"""The sani-core sidecar application loop (Sani master doc 13/14, 9).

The Tauri host owns this process over private stdin/stdout framed-JSON
IPC -- there is deliberately no localhost web server here. One serve()
session reads requests, dispatches them against the registry, and
streams run events and responses back as frames; concurrent runs
interleave on the same writer behind a lock. The lifecycle is exactly
run and cancel -- a small agent registry, not a workflow platform.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from assistant.core.protocol import (
    Event,
    ProtocolError,
    Request,
    Response,
    error_from_request,
    read_frame,
    response_from_request,
    write_frame,
)
from assistant.core.registry import AgentProtocol, AgentRegistry

logger = logging.getLogger("assistant.core")

DEFAULT_MAX_CONCURRENT_RUNS = 4


def _parse_request(frame: dict[str, Any]) -> Request:
    """Validate the request envelope; ProtocolError here means a broken peer."""
    request_id = frame.get("id")
    method = frame.get("method")
    if not isinstance(request_id, str) or not isinstance(method, str):
        raise ProtocolError("not a request: 'id' and 'method' must be strings")
    params = frame.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolError("request 'params' must be a JSON object")
    return Request(id=request_id, method=method, params=params)


@dataclass
class _Run:
    """One in-flight agent run owned by a single serve session."""

    request: Request
    run_id: str
    agent: AgentProtocol
    cancel_requested: bool = False
    task: asyncio.Task[None] = field(init=False)


class _Session:
    """One framed connection: the writer lock plus the runs it spawned."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._write_lock = asyncio.Lock()
        self.closed = False
        self.runs: dict[str, _Run] = {}

    async def send(self, payload: dict[str, Any]) -> None:
        """Write one frame under the lock; never raise after a dead peer.

        Constraint: events and responses interleave on one writer, so every
        frame is serialized through the same lock. A write failure means the
        host is gone -- drop further frames instead of killing the loop or
        corrupting half-written frames.
        """
        if self.closed:
            return
        async with self._write_lock:
            if self.closed:
                return
            try:
                await write_frame(self._writer, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.closed = True
                logger.warning("sani-core: dropping connection after write failure: %s", exc)

    async def shutdown(self) -> None:
        tasks = [run.task for run in self.runs.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.closed = True
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


class SaniCoreApp:
    """Dispatches framed requests against the registry with capped concurrency."""

    def __init__(
        self,
        registry: AgentRegistry,
        max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
        *,
        status_provider: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        self._registry = registry
        self._max_concurrent_runs = max_concurrent_runs
        self._status_provider = status_provider

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve framed requests until clean EOF; malformed frames end the session."""
        session = _Session(writer)
        try:
            while True:
                try:
                    frame = await read_frame(reader)
                    if frame is None:
                        break
                    request = _parse_request(frame)
                except ProtocolError as exc:
                    logger.warning("sani-core: malformed frame; terminating connection: %s", exc)
                    break
                await self._dispatch(session, request)
        finally:
            await session.shutdown()

    async def _dispatch(self, session: _Session, request: Request) -> None:
        handler = self._handler_for(request.method)
        if handler is None:
            await session.send(error_from_request(request, "unknown method").to_frame())
            return
        try:
            await handler(session, request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Constraint: the client must always get a Response, even when a
            # handler itself fails; the run task handles its own failures.
            logger.warning("sani-core: %s failed unexpectedly: %s", request.method, exc)
            await session.send(error_from_request(request, f"internal error: {exc}").to_frame())

    def _handler_for(self, method: str) -> Callable[[_Session, Request], Awaitable[None]] | None:
        if method == "agents.list":
            return self._handle_agents_list
        if method == "run.start":
            return self._handle_run_start
        if method == "run.cancel":
            return self._handle_run_cancel
        if method == "system.status":
            return self._handle_system_status
        return None

    async def _handle_system_status(self, session: _Session, request: Request) -> None:
        if self._status_provider is None:
            await session.send(
                error_from_request(request, "no status provider configured").to_frame()
            )
            return
        result = await self._status_provider()
        await session.send(response_from_request(request, result).to_frame())

    async def _handle_agents_list(self, session: _Session, request: Request) -> None:
        result = {"agents": [descriptor.as_dict() for descriptor in self._registry.list()]}
        await session.send(response_from_request(request, result).to_frame())

    async def _handle_run_start(self, session: _Session, request: Request) -> None:
        agent_id = request.params.get("agent_id")
        text = request.params.get("text")
        if not isinstance(agent_id, str) or not isinstance(text, str):
            await session.send(
                error_from_request(
                    request, "run.start requires string params 'agent_id' and 'text'"
                ).to_frame()
            )
            return
        try:
            agent = self._registry.get(agent_id)
        except KeyError as exc:
            await session.send(error_from_request(request, str(exc)).to_frame())
            return
        if len(session.runs) >= self._max_concurrent_runs:
            await session.send(
                error_from_request(
                    request,
                    f"run limit reached: at most {self._max_concurrent_runs} concurrent runs",
                ).to_frame()
            )
            return
        run = _Run(request=request, run_id=uuid.uuid4().hex, agent=agent)
        run.task = asyncio.create_task(
            self._execute_run(session, run, text), name=f"sani-run-{run.run_id}"
        )
        session.runs[run.run_id] = run

    async def _handle_run_cancel(self, session: _Session, request: Request) -> None:
        run_id = request.params.get("run_id")
        if not isinstance(run_id, str):
            await session.send(
                error_from_request(request, "run.cancel requires string param 'run_id'").to_frame()
            )
            return
        run = session.runs.get(run_id)
        if run is None:
            await session.send(error_from_request(request, f"unknown run: {run_id}").to_frame())
            return
        run.cancel_requested = True
        with contextlib.suppress(Exception):
            await run.agent.cancel()
        run.task.cancel()
        await session.send(
            Response(id=request.id, ok=True, result={"status": "cancelling"}).to_frame()
        )

    async def _execute_run(self, session: _Session, run: _Run, text: str) -> None:
        async def on_event(kind: str, data: dict[str, Any]) -> None:
            await session.send(Event(run_id=run.run_id, kind=kind, data=data).to_frame())

        try:
            result = await run.agent.run(
                text, on_event=on_event, cancel_check=lambda: run.cancel_requested
            )
        except asyncio.CancelledError:
            # Constraint: a second cancel() landing during cleanup must not
            # corrupt the frame writer -- cleanup sends are shielded, and the
            # cancellation is re-raised so the task still ends cancelled.
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(
                    session.send(Event(run_id=run.run_id, kind="cancelled", data={}).to_frame())
                )
                await asyncio.shield(
                    session.send(
                        response_from_request(run.request, {"status": "cancelled"}).to_frame()
                    )
                )
            raise
        except Exception as exc:
            logger.warning("sani-core: run %s failed: %s", run.run_id, exc)
            await session.send(error_from_request(run.request, f"agent error: {exc}").to_frame())
        else:
            await session.send(response_from_request(run.request, result).to_frame())
        finally:
            session.runs.pop(run.run_id, None)
