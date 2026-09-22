"""Sani-owned embedded CUA runtime status (Sani master doc sections 10, 12-13).

Boundary: the Sani DESKTOP HOST owns the embedded CUA runtime's lifecycle
(start/stop of the bundled driver); sani-core never spawns an unsupported
raw daemon. What sani-core owns here is the runtime's STATUS surface:

- a bounded-posture probe of the installed driver (fail-closed, same
  posture rules as the gateway connection);
- macOS permission preflight (Accessibility, Screen Recording) through
  ctypes system frameworks -- zero third-party dependencies, read-only,
  never auto-granting anything (section 25: OS permission approval is not
  a dependency installation);
- per-permission guidance the desktop UI can show the user.
"""

from __future__ import annotations

import asyncio
import ctypes
import shutil
from dataclasses import dataclass
from typing import Any

from assistant.settings import Settings

_FRAMEWORKS = "/System/Library/Frameworks"

_DRIVER_PROBE_TIMEOUT_SECONDS = 10.0

_ACCESSIBILITY_LINK = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)
_SCREEN_LINK = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"


def _ax_is_process_trusted() -> bool | None:
    """Accessibility preflight for THIS process; None when unavailable."""
    try:
        framework = ctypes.cdll.LoadLibrary(
            f"{_FRAMEWORKS}/ApplicationServices.framework/ApplicationServices"
        )
        framework.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(framework.AXIsProcessTrusted())
    except OSError:
        return None


def _screen_capture_preflight() -> bool | None:
    """Screen Recording preflight for THIS process; None when unavailable."""
    try:
        framework = ctypes.cdll.LoadLibrary(f"{_FRAMEWORKS}/CoreGraphics.framework/CoreGraphics")
        framework.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(framework.CGPreflightScreenCaptureAccess())
    except OSError:
        return None


@dataclass(frozen=True)
class PermissionState:
    """One macOS permission as the UI should render it."""

    name: str
    granted: bool | None  # None = unknown (non-macOS or API unavailable)
    guidance: str
    deep_link: str


def macos_permissions() -> tuple[PermissionState, PermissionState]:
    """Read-only preflight of the two permissions computer control needs."""
    accessibility = PermissionState(
        name="accessibility",
        granted=_ax_is_process_trusted(),
        guidance=(
            "Grant Sani Accessibility in System Settings > Privacy & Security "
            "> Accessibility, then restart Sani."
        ),
        deep_link=_ACCESSIBILITY_LINK,
    )
    screen = PermissionState(
        name="screen_recording",
        granted=_screen_capture_preflight(),
        guidance=(
            "Grant Sani Screen Recording in System Settings > Privacy & "
            "Security > Screen Recording, then restart Sani."
        ),
        deep_link=_SCREEN_LINK,
    )
    return accessibility, screen


@dataclass(frozen=True)
class DriverStatus:
    """Fail-closed posture of the installed Cua Driver daemon."""

    found: bool
    bounded_ok: bool
    detail: str


async def probe_driver(settings: Settings) -> DriverStatus:
    """Probe the driver's daemon posture without ever spawning a daemon.

    Reuses the gateway's fail-closed posture evaluation: a standard-mode or
    manifest-less daemon must never back computer control.
    """
    if not settings.cua_enabled:
        return DriverStatus(
            found=False,
            bounded_ok=False,
            detail="CUA is disabled (CUA_ENABLED=false); computer control unavailable",
        )
    executable = shutil.which(settings.cua_command)
    if executable is None:
        return DriverStatus(
            found=False,
            bounded_ok=False,
            detail=(
                f"cua-driver executable {settings.cua_command!r} not found on "
                "PATH; the Sani bundle must provide the embedded driver"
            ),
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            executable,
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), _DRIVER_PROBE_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        return DriverStatus(
            found=True, bounded_ok=False, detail=f"driver status probe failed: {exc}"
        )
    output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
    from assistant.tools.cua import _evaluate_daemon_status

    reason = _evaluate_daemon_status(output)
    if reason is not None:
        return DriverStatus(found=True, bounded_ok=False, detail=reason)
    return DriverStatus(found=True, bounded_ok=True, detail="bounded daemon verified")


async def system_status(settings: Settings) -> dict[str, Any]:
    """One status payload for the desktop UI (subsystem states, no secrets)."""
    accessibility, screen = macos_permissions()
    driver = await probe_driver(settings)
    return {
        "driver": {
            "found": driver.found,
            "bounded_ok": driver.bounded_ok,
            "detail": driver.detail,
        },
        "permissions": [
            {
                "name": accessibility.name,
                "granted": accessibility.granted,
                "guidance": accessibility.guidance,
                "deep_link": accessibility.deep_link,
            },
            {
                "name": screen.name,
                "granted": screen.granted,
                "guidance": screen.guidance,
                "deep_link": screen.deep_link,
            },
        ],
        "memory_backend": settings.memory_backend,
        "jev_credential": _credential_kind(settings),
    }


def _credential_kind(settings: Settings) -> str:
    """Which JEV credential is configured -- presence only, never values."""
    if settings.typesafe_api_key:
        return "typesafe"
    if settings.openrouter_api_key:
        return "openrouter"
    return "none"
