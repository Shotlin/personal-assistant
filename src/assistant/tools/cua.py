"""Cua Driver MCP connection (spec sections 13.2-13.7).

Two ways to talk to the driver:

- :func:`open_cua_connection` -- persistent stdio session owned by the
  gateway. The transport lease stays open for the process lifetime, so
  driver-side *sessions* (agent cursor, per-run state) survive across
  tool calls. Used by the application lifespan.
- :func:`load_cua_tools` -- stateless discovery via the MCP adapter
  (spawns a short-lived proxy per call). Fine for verification scripts;
  sessions do NOT persist through it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import stat
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from assistant.runtime.cua_faults import CuaFault, classify_exception
from assistant.settings import Settings
from assistant.tools.policy import (
    SESSION_LIFECYCLE_TOOL_NAMES,
    apply_tool_policy,
    assert_observation_available,
    filter_cua_tools,
)

logger = logging.getLogger("assistant.tools.cua")

if TYPE_CHECKING:  # pragma: no cover
    pass

#: Calls that cannot change the desktop, so replaying one after a transport drop
#: is safe. `start_session`/`end_session` are documented idempotent by the
#: driver. Everything else -- a click, a keystroke, typed text -- may already
#: have landed before the socket died, and is never silently sent twice.
NON_MUTATING_TOOLS = frozenset(
    {
        "list_apps",
        "list_windows",
        "get_window_state",
        "get_desktop_state",
        "get_accessibility_tree",
        "get_screen_size",
        "verify_state",
        "zoom",
        "check_permissions",
        "start_session",
        "end_session",
        "get_agent_cursor_state",
    }
)


class CuaTransportError(RuntimeError):
    """The MCP lease could not be established or rebuilt."""

    def __init__(self, message: str, fault: CuaFault = CuaFault.TRANSPORT) -> None:
        super().__init__(message)
        self.fault = fault


@dataclass(frozen=True)
class CuaConnection:
    """Filtered CUA tool inventory plus what the driver actually exposed."""

    tools: list[BaseTool]
    tool_names: list[str]
    discovered_names: list[str]
    skipped_names: list[str]
    # Raw allowlisted normalized tools for trusted recipes only.
    # Model-facing tools above remain policy-wrapped.
    tools_by_name: dict[str, BaseTool] = field(default_factory=dict)
    #: Unwrapped lifecycle tools for the trusted DesktopSessionManager
    #: only (never the model inventory); absent names mean the installed
    #: driver does not expose them.
    lifecycle_tools_by_name: dict[str, BaseTool] = field(default_factory=dict)


def _require_manifest(settings: Settings) -> Path | None:
    """The reviewed capability manifest, or None where no manifest is the policy.

    Under approved architecture D1 the daemon runs in ``standard`` mode and the
    deterministic action-class gate belongs to Sani: there a manifest is not
    merely absent, it is the wrong artifact to demand of the driver.
    """
    if not settings.cua_enabled:
        raise RuntimeError("CUA is disabled; CUA tools require CUA_ENABLED=true")
    if settings.cua_permission_mode == "standard":
        return None
    manifest = Path(settings.cua_capability_manifest_path)
    if not settings.cua_capability_manifest_path.startswith("/"):
        raise RuntimeError("CUA_CAPABILITY_MANIFEST_PATH must be absolute")
    if not manifest.is_file():
        raise RuntimeError(
            f"CUA capability manifest not found at {manifest}; author it against the "
            "installed Cua Driver schema and start the driver in bounded mode."
        )
    return manifest


def driver_mcp_args(settings: Settings) -> list[str]:
    """Return the only MCP launch shape allowed for this driver instance.

    Installed Sani starts an embedded, bounded daemon itself.  Its MCP proxy
    must select that private socket explicitly: a bare macOS ``mcp`` command
    may otherwise revive a standalone standard-mode daemon.  Development
    retains the standalone command when no socket was supplied.
    """
    if not settings.cua_socket:
        return ["mcp"]
    return ["mcp", "--embedded", "--socket", settings.cua_socket]


def _evaluate_daemon_status(output: str, permission_mode: str = "bounded") -> str | None:
    """Fail-closed posture check over ``cua-driver status`` output.

    Returns a remediation message when the daemon's live posture is not the
    mode Sani asked for, or ``None`` when it matches.

    Why this exists (2026-09-18 incident): the driver's ``mcp`` proxy
    silently resurrects a dead daemon via ``open -a CuaDriver``, and on
    this host ``open --args`` drops the arguments — the resurrected
    daemon came up in *standard* mode without the capability manifest
    even though both TCC toggles were enabled. The application must
    therefore verify the daemon's actual posture instead of trusting
    its own ``CUA_PERMISSION_MODE`` setting. That verification is
    mode-parameterised, so a standard-mode host rejects a bounded or
    resurrected daemon as loudly as a bounded host rejects a standard one.
    """
    lowered = output.lower()
    if "daemon is not running" in lowered:
        return (
            "CuaDriver daemon is not running. Start it via the LaunchAgent "
            "(launchctl kickstart -k gui/501/com.trycua.cua_driver_daemon); "
            "relying on the mcp proxy resurrection would silently run "
            "standard mode without the capability manifest."
        )
    mode_line = next((line for line in output.splitlines() if "permission mode:" in line), "")
    if permission_mode not in mode_line:
        return (
            f"CuaDriver daemon is not in {permission_mode} mode "
            f"({mode_line.strip() or 'mode unreadable'}); CUA_ENABLED=true "
            f"requires a daemon started with --permission-mode {permission_mode}. "
            "Restart it via the LaunchAgent — never via `open --args`, "
            "which drops the mode/manifest arguments on this host."
        )
    if permission_mode != "bounded":
        # Standard mode carries no manifest by design. Demanding one would
        # re-impose the ceiling architecture D1 moved into Sani's own gate.
        return None
    manifest_line = next(
        (line for line in output.splitlines() if line.strip().startswith("capability manifest:")),
        "",
    )
    if not (
        "configured=true" in manifest_line
        and "approved_at_startup=true" in manifest_line
        and "valid=true" in manifest_line
    ):
        return (
            "CuaDriver daemon is bounded but its capability manifest is "
            f"not configured+approved at startup ({manifest_line.strip() or 'unreported'}); "
            "restart the daemon with --capability-manifest and "
            "--approve-capability-manifest (see the LaunchAgent plist)."
        )
    return None


async def _assert_daemon_posture(settings: Settings) -> None:
    """Verify the live daemon is running the mode Sani launched it in.

    A socket that merely accepts a connection proves nothing about which mode
    it was started in, and that gap is why a locked-out bounded manifest and a
    resurrected standalone daemon could both pass for healthy. The posture is
    therefore read from the daemon itself, over Sani's own endpoint when it has
    one -- never from the setting that asked for it.
    """
    import asyncio
    import shutil

    status_args = ["status"]
    if settings.cua_socket:
        # Do not call bare `status` here: on macOS that command addresses the
        # global standalone daemon, not Sani's private embedded endpoint.
        try:
            is_socket = stat.S_ISSOCK(os.stat(settings.cua_socket).st_mode)
        except OSError as exc:
            raise RuntimeError(
                f"embedded CuaDriver socket is unavailable at {settings.cua_socket}: {exc}"
            ) from exc
        if not is_socket:
            raise RuntimeError(
                f"embedded CuaDriver endpoint is not a socket: {settings.cua_socket}"
            )
        status_args += ["--socket", settings.cua_socket]

    executable = shutil.which(settings.cua_command)
    if executable is None:
        raise RuntimeError(
            f"cua-driver executable {settings.cua_command!r} not found on PATH; "
            "cannot verify the daemon posture (fail-closed)."
        )
    proc = await asyncio.create_subprocess_exec(
        executable,
        *status_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
    reason = _evaluate_daemon_status(output, settings.cua_permission_mode)
    if reason is not None:
        raise RuntimeError(reason)
    mode_line = next((line for line in output.splitlines() if "permission mode:" in line), "")
    manifest_sha = next(
        (
            line.split(":", 1)[1].strip()
            for line in output.splitlines()
            if "capability manifest sha256:" in line
        ),
        "",
    )
    logger.info(
        "cua_daemon_posture_verified",
        extra={
            "event": "cua_daemon_posture_verified",
            "mode": mode_line.split(":", 1)[-1].strip(),
            "manifest_sha256": manifest_sha,
        },
    )


def _filtered_connection(discovered: Sequence[BaseTool]) -> CuaConnection:
    result = filter_cua_tools(discovered)
    logger.info(
        "cua_tools_filtered",
        extra={
            "event": "cua_tools_filtered",
            "discovered": sorted(result.discovered_names),
            "enabled": sorted(result.enabled_names),
            "skipped": sorted(result.skipped_names),
        },
    )
    assert_observation_available(result.enabled_names)
    wrapped, wrapped_names = apply_tool_policy(result.enabled)
    # Trusted-path only: raw lifecycle tools for the DesktopSessionManager.
    # Filtered to SESSION_LIFECYCLE_TOOL_NAMES so the controller cannot
    # reach beyond session management even here.
    lifecycle = {
        getattr(tool, "name", ""): tool
        for tool in discovered
        if getattr(tool, "name", "") in SESSION_LIFECYCLE_TOOL_NAMES
    }
    return CuaConnection(
        tools=wrapped,
        tool_names=wrapped_names,
        discovered_names=result.discovered_names,
        skipped_names=result.skipped_names,
        tools_by_name={getattr(t, "name", "?"): t for t in result.enabled},
        lifecycle_tools_by_name=lifecycle,
    )


async def load_cua_tools(settings: Settings) -> CuaConnection:
    """Stateless discovery through the MCP adapter (verification scripts)."""
    _require_manifest(settings)
    await _assert_daemon_posture(settings)
    client = MultiServerMCPClient(
        {
            "cua": {
                "command": settings.cua_command,
                "args": driver_mcp_args(settings),
                "transport": "stdio",
            }
        }
    )
    discovered = await client.get_tools(server_name="cua")
    return _filtered_connection(list(discovered))


class McpTransport:
    """One stdio MCP lease, owned by exactly one task, rebuilt in that task.

    The lease is an anyio task group, and a task group may only be entered and
    exited by the task that owns it. Entering the replacement lease from whichever
    turn happened to notice the failure does not work -- measured live on 0.28.2,
    the dying driver cancels its own scope, so the caller's task is inside a
    cancelled scope before the new lease is even opened, and "reconnect" raises
    `CancelledError` instead of recovering. So one task owns the lease for its
    whole life and every request -- including the rebuild -- runs there.

    The tools the agent was built with hold *this* object rather than a session,
    which is what lets a driver crash and restart underneath a running Deep Agent
    without rebuilding the agent or failing the process's remaining turns.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue: asyncio.Queue[tuple[str, Any, asyncio.Future]] | None = None
        self._owner: asyncio.Task | None = None
        self.leases = 0

    @property
    def reconnections(self) -> int:
        """Leases opened after the first one."""
        return max(0, self.leases - 1)

    @property
    def running(self) -> bool:
        return self._owner is not None and not self._owner.done()

    async def start(self) -> None:
        if self.running:
            return
        self._queue = asyncio.Queue()
        self._owner = asyncio.create_task(self._serve(), name="cua-mcp-transport")

    #: A restarted driver needs a moment to listen again -- measured live: the
    #: socket file exists before anything is accepting on it, and a lease opened
    #: in that window dies with "Connection closed". Bounded, and waited only
    #: before opening a lease: a read that waits a moment is cheap, and a
    #: mutation is never replayed at all.
    ENDPOINT_WAIT_SECONDS = 3.0

    async def _wait_for_endpoint(self) -> None:
        socket = str(getattr(self._settings, "cua_socket", "") or "")
        if not socket:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.ENDPOINT_WAIT_SECONDS
        while True:
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(socket), timeout=0.5
                )
            except (OSError, TimeoutError):
                writer = None
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return
            if loop.time() >= deadline:
                return  # open anyway: the proxy's own failure is then the evidence
            await asyncio.sleep(0.1)

    async def _new_lease(self) -> tuple[contextlib.AsyncExitStack, Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        await self._wait_for_endpoint()
        stack = contextlib.AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=self._settings.cua_command,
                        args=driver_mcp_args(self._settings),
                    )
                )
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
        except BaseException as exc:
            with contextlib.suppress(Exception):
                await stack.aclose()
            fault = classify_exception(exc)
            raise CuaTransportError(
                f"the computer-control driver connection could not be opened: {exc}",
                fault if fault is not CuaFault.UNKNOWN else CuaFault.DAEMON,
            ) from exc
        return stack, session

    async def _serve(self) -> None:
        queue = self._queue
        assert queue is not None  # set by start()
        stack: contextlib.AsyncExitStack | None = None
        session: Any = None
        try:
            while True:
                kind, payload, future = await queue.get()
                if future.done():
                    continue
                try:
                    if kind == "close":
                        if stack is not None:
                            await stack.aclose()
                            stack, session = None, None
                        future.set_result(None)
                        continue
                    if session is None:
                        stack, session = await self._new_lease()
                        self.leases += 1
                    if kind == "list_tools":
                        result = await session.list_tools()
                    elif kind == "call":
                        name, arguments = payload
                        result = await session.call_tool(name, arguments)
                    else:  # pragma: no cover - a programming error, not a fault
                        raise CuaTransportError(f"unknown transport request {kind!r}")
                    future.set_result(result)
                except BaseException as exc:
                    # Nothing is trusted after a failed exchange: the lease is
                    # dropped here so the next request opens a new one instead of
                    # writing into a half-dead pipe.
                    if stack is not None:
                        with contextlib.suppress(Exception):
                            await stack.aclose()
                        stack, session = None, None
                    if not future.done():
                        future.set_exception(exc)
                    if isinstance(exc, asyncio.CancelledError):
                        raise
        finally:
            if stack is not None:
                with contextlib.suppress(Exception):
                    await stack.aclose()

    async def _request(self, kind: str, payload: Any = None) -> Any:
        if not self.running or self._queue is None:
            raise CuaTransportError("the computer-control driver transport is stopped")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put((kind, payload, future))
        return await future

    async def list_tools(self) -> Any:
        return await self._request("list_tools")

    async def call_tool(self, name: str, kwargs: dict[str, Any]) -> Any:
        return await self._request("call", (name, kwargs))

    async def aclose(self) -> None:
        owner, self._owner = self._owner, None
        if owner is None or owner.done():
            return
        with contextlib.suppress(Exception):
            await self._request("close")
        owner.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await owner


@asynccontextmanager
async def open_cua_connection(settings: Settings) -> AsyncIterator[CuaConnection]:
    """Persistent stdio MCP session owned by the gateway.

    The transport lease stays open, which keeps driver-side sessions
    (agent cursor, run state) alive across tool calls. Yields the filtered
    tool inventory and closes the session on exit.
    """
    _require_manifest(settings)
    # Fail closed before any transport is opened: whatever mode Sani launched,
    # the daemon answering must be in it. The 2026-09-18 incident was the mcp
    # proxy resurrecting a daemon in a mode nobody chose (spec 13.3/13.4).
    await _assert_daemon_posture(settings)

    transport = McpTransport(settings)
    await transport.start()
    try:
        tools_response = await transport.list_tools()
        discovered: list[BaseTool] = [
            StructuredTool(
                name=tool.name,
                description=tool.description or "",
                args_schema=tool.inputSchema,
                coroutine=_caller(transport, tool.name),
            )
            for tool in tools_response.tools
        ]
        yield _filtered_connection(discovered)
    finally:
        await transport.aclose()


def _caller(transport: McpTransport, name: str) -> Any:
    """Build the async callable for one tool, with a bounded recovery path.

    A transport-class failure rebuilds the lease once. Whether the call is then
    re-sent depends on what it does: a read is safe to replay, a mutation is
    not -- the driver may have performed it and lost the pipe on the way back. A
    mutation that cannot be confirmed is reported as unknown so the agent loop
    re-observes the screen rather than acting twice.
    """

    from assistant.tools.result_normalizer import ToolOutcome, normalize_mcp_result

    async def call(**kwargs: Any) -> Any:
        try:
            result = await transport.call_tool(name, kwargs)
            return normalize_mcp_result(result)
        except asyncio.CancelledError:
            # This turn was stopped. That is an instruction, not a fault, and
            # "recovering" from it would keep driving the desktop after Stop.
            raise
        except BaseException as exc:
            if classify_exception(exc) is not CuaFault.TRANSPORT:
                raise
            if name in NON_MUTATING_TOOLS:
                # The failed exchange already dropped the lease inside the owner
                # task, so this replay opens a fresh one. Once only: a second
                # transport failure propagates rather than becoming a loop.
                result = await transport.call_tool(name, kwargs)
                return normalize_mcp_result(result)
            logger.warning(
                "cua_action_outcome_unknown",
                extra={"event": "cua_action_outcome_unknown", "tool": name},
            )
            return ToolOutcome(
                status="unknown",
                effect="unverifiable",
                text=(
                    f"{name} could not be confirmed: the driver connection dropped "
                    "and the action is never replayed blindly"
                ),
            )

    return call
