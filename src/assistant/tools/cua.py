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

from assistant.settings import Settings
from assistant.tools.policy import (
    SESSION_LIFECYCLE_TOOL_NAMES,
    apply_tool_policy,
    assert_observation_available,
    filter_cua_tools,
)

logger = logging.getLogger("assistant.tools.cua")

if TYPE_CHECKING:  # pragma: no cover
    from mcp import ClientSession


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

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command=settings.cua_command,
        args=driver_mcp_args(settings),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        session = ClientSession(read_stream, write_stream)
        async with session as mcp_session:
            await mcp_session.initialize()

            tools_response = await mcp_session.list_tools()
            discovered: list[BaseTool] = []
            for tool in tools_response.tools:
                discovered.append(
                    StructuredTool(
                        name=tool.name,
                        description=tool.description or "",
                        args_schema=tool.inputSchema,
                        coroutine=_caller(mcp_session, tool.name),
                    )
                )
            yield _filtered_connection(discovered)


def _caller(session: ClientSession, name: str) -> Any:
    """Build an async callable that routes one tool call over ``session``.

    Returns the normalized :class:`ToolOutcome` so the policy wrapper can
    produce bounded model text while structured evidence stays available
    to trusted local verification (master plan WP2/F02).
    """

    from assistant.tools.result_normalizer import normalize_mcp_result

    async def call(**kwargs: Any) -> Any:
        result = await session.call_tool(name, kwargs)
        return normalize_mcp_result(result)

    return call
