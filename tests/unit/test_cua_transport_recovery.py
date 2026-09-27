"""Transport recovery: rebuild the lease in its owning task, never replay a
mutation blindly.

Live capture that drove this design: killing the driver under an open connection
makes the next call raise a bare ``anyio.ClosedResourceError`` with no message,
and that cancel scope belongs to the dying lease -- which is why the lease is
rebuilt by the single task that owns it, and why the caller's own cancellation
must never be mistaken for a fault to recover from.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any

import anyio
import pytest

from assistant.runtime.cua_faults import CuaFault, classify_exception
from assistant.tools.cua import NON_MUTATING_TOOLS, CuaTransportError, McpTransport, _caller
from assistant.tools.result_normalizer import ToolOutcome


class FakeSession:
    """One MCP session whose first call dies, then works."""

    def __init__(self, fail_with: BaseException | None = None) -> None:
        self.calls: list[str] = []
        self.fail_with = fail_with

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append(name)
        if self.fail_with is not None:
            raise self.fail_with
        return "ok"


class StubTransport(McpTransport):
    """A transport whose leases are fakes, so every rebuild is observable."""

    def __init__(
        self,
        sessions: list[FakeSession],
        refuse_new_lease: bool = False,
    ) -> None:
        super().__init__(None)  # type: ignore[arg-type]
        self._pending = list(sessions)
        self._refuse_new_lease = refuse_new_lease

    async def _new_lease(self) -> tuple[contextlib.AsyncExitStack, Any]:
        if self._refuse_new_lease or not self._pending:
            raise OSError("Cua Driver daemon is not running")
        return contextlib.AsyncExitStack(), self._pending.pop(0)


async def _transport(*sessions: FakeSession, refuse: bool = False) -> StubTransport:
    transport = StubTransport(list(sessions), refuse_new_lease=refuse)
    await transport.start()
    return transport


@pytest.fixture(autouse=True)
def _normalize_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    from assistant.tools import result_normalizer

    monkeypatch.setattr(
        result_normalizer,
        "normalize_mcp_result",
        lambda result: ToolOutcome("ok", "confirmed", text="normalized"),
    )


async def test_a_read_is_replayed_once_over_a_fresh_lease() -> None:
    first, second = FakeSession(anyio.ClosedResourceError()), FakeSession()
    transport = await _transport(first, second)

    outcome = await _caller(transport, "get_screen_size")()

    assert outcome.status == "ok"
    assert transport.reconnections == 1
    assert first.calls == ["get_screen_size"]
    assert second.calls == ["get_screen_size"]


async def test_a_mutation_is_never_replayed_after_the_pipe_dies() -> None:
    """The click may already have landed. Reporting unknown costs the loop one
    re-observation; replaying it costs the user a second click."""
    first, second = FakeSession(anyio.ClosedResourceError()), FakeSession()
    transport = await _transport(first, second)

    outcome = await _caller(transport, "click")(element_token="e1")

    assert outcome.status == "unknown"
    assert outcome.effect == "unverifiable"
    assert "never replayed" in outcome.text
    assert first.calls == ["click"]
    assert second.calls == [], "the replacement lease must not repeat the action"


async def test_a_fault_that_reconnection_cannot_fix_propagates() -> None:
    """A permission refusal is not a transport fault: rebuilding the lease would
    turn one visible error into a silent retry."""
    first = FakeSession(RuntimeError("permissions_pending: nope"))
    transport = await _transport(first, FakeSession())

    with pytest.raises(RuntimeError, match="permissions_pending"):
        await _caller(transport, "get_screen_size")()
    assert transport.reconnections == 0


async def test_a_lease_that_cannot_be_rebuilt_reports_the_daemon_fault() -> None:
    transport = await _transport(FakeSession(anyio.ClosedResourceError()), refuse=True)

    with pytest.raises(OSError) as raised:
        await _caller(transport, "list_apps")()
    assert classify_exception(raised.value) is CuaFault.DAEMON
    assert transport.reconnections == 0


async def test_a_stopped_transport_refuses_work_rather_than_holding_a_dead_lease() -> None:
    transport = await _transport(FakeSession())
    await transport.aclose()

    with pytest.raises(CuaTransportError) as raised:
        await transport.call_tool("list_apps", {})
    assert raised.value.fault is CuaFault.TRANSPORT
    assert not transport.running


async def test_closing_a_transport_stops_its_owning_task() -> None:
    transport = await _transport(FakeSession())
    owner = transport._owner
    assert owner is not None and not owner.done()
    await transport.aclose()
    assert owner.done()


async def test_requests_are_served_one_at_a_time_in_order() -> None:
    """Two turns sharing one sidecar must not interleave inside one lease."""

    class SlowSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
            self.calls.append(f"start:{name}")
            await asyncio.sleep(0.01)
            self.calls.append(f"end:{name}")
            return "ok"

    session = SlowSession()
    transport = await _transport(session)
    results = await asyncio.gather(
        transport.call_tool("list_apps", {}), transport.call_tool("get_screen_size", {})
    )

    assert len(results) == 2
    assert session.calls == [
        "start:list_apps",
        "end:list_apps",
        "start:get_screen_size",
        "end:get_screen_size",
    ]


def test_only_non_mutating_tools_are_on_the_replay_list() -> None:
    assert {"click", "type_text", "scroll", "press_key", "launch_app"}.isdisjoint(
        NON_MUTATING_TOOLS
    )
    # start_session is documented idempotent, which is what makes its replay safe.
    assert {"start_session", "end_session", "list_apps"} <= NON_MUTATING_TOOLS


async def test_a_lease_waits_for_a_driver_that_is_still_coming_up() -> None:
    """Measured live: after a restart the socket file exists before anything
    accepts on it, and a lease opened in that window dies on 'Connection closed'.
    So the wait is for a listening endpoint, not for a path."""
    import socket as socket_module
    from types import SimpleNamespace

    # A short, fixed directory: AF_UNIX paths are capped at ~104 bytes, and a
    # pytest tmp_path blows straight through that.
    base = "/private/tmp"
    path = f"{base}/sani-late-{os.getpid()}.sock"
    transport = StubTransport([])
    transport._settings = SimpleNamespace(cua_socket=path)
    transport.ENDPOINT_WAIT_SECONDS = 3.0
    held: list[socket_module.socket] = []

    async def bind_later() -> None:
        await asyncio.sleep(0.4)
        listener = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        listener.bind(path)
        listener.listen(1)
        held.append(listener)

    task = asyncio.create_task(bind_later())
    loop = asyncio.get_running_loop()
    began = loop.time()
    await transport._wait_for_endpoint()
    waited = loop.time() - began
    await task

    assert 0.3 < waited < 2.0, f"expected to wait for the listener, waited {waited:.2f}s"

    # A path nothing ever binds is abandoned inside the bound, never hung on.
    transport.ENDPOINT_WAIT_SECONDS = 0.3
    transport._settings = SimpleNamespace(cua_socket=f"{base}/sani-never-{os.getpid()}.sock")
    began = loop.time()
    await transport._wait_for_endpoint()
    assert loop.time() - began < 1.5
    for listener in held:
        listener.close()
    if os.path.exists(path):
        os.unlink(path)
