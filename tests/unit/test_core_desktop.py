"""Embedded CUA runtime status tests (Sani master doc sections 10, 12-13).

The driver probe runs against tiny shim executables so the fail-closed
posture logic is verified without the real daemon; the permission calls hit
the real macOS frameworks but only assert type safety, never a granted
state (this process may or may not be trusted).
"""

import os
import socket
import stat
from pathlib import Path

from assistant.core.desktop import macos_permissions, probe_driver, system_status
from assistant.settings import Settings


def _settings(cua_command: str) -> Settings:
    return Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        cua_command=cua_command,
        cua_enabled=True,
        cua_capability_manifest_path="/nonexistent",
    )


def _shim(path: Path, body: str) -> str:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


async def test_probe_driver_reports_missing_binary(tmp_path: Path) -> None:
    status = await probe_driver(_settings("definitely-not-a-driver-binary"))
    assert status.found is False
    assert status.bounded_ok is False
    assert "not found" in status.detail


async def test_probe_driver_accepts_bounded_daemon(tmp_path: Path) -> None:
    shim = _shim(
        tmp_path / "cua-driver",
        'echo "permission mode: bounded"\n'
        'echo "capability manifest: configured=true approved_at_startup=true valid=true"\n',
    )
    status = await probe_driver(_settings(shim))
    assert status.found is True
    assert status.bounded_ok is True


async def test_probe_driver_rejects_standard_mode_daemon(tmp_path: Path) -> None:
    shim = _shim(tmp_path / "cua-driver", 'echo "permission mode: standard"\n')
    status = await probe_driver(_settings(shim))
    assert status.found is True
    assert status.bounded_ok is False
    assert "bounded" in status.detail


async def test_probe_driver_honors_cua_disabled(tmp_path: Path) -> None:
    settings = Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        cua_command="whatever",
        cua_enabled=False,
    )
    status = await probe_driver(settings)
    assert status.found is False
    assert "CUA_ENABLED=false" in status.detail


async def test_probe_driver_rejects_a_stale_embedded_socket() -> None:
    """A leftover socket pathname must not be presented as a ready daemon."""
    stale_socket = Path(f"/private/tmp/sani-stale-{os.getpid()}.sock")
    stale_socket.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(stale_socket))
    listener.close()
    settings = _settings("unused-for-embedded-driver")
    settings.cua_socket = str(stale_socket)

    try:
        status = await probe_driver(settings)
    finally:
        stale_socket.unlink(missing_ok=True)

    assert status.found is True
    assert status.bounded_ok is False
    assert "not accepting connections" in status.detail


def test_macos_permissions_return_renderable_states() -> None:
    accessibility, screen = macos_permissions()
    for permission in (accessibility, screen):
        assert permission.granted in (True, False, None)
        assert "System Settings" in permission.guidance
        assert permission.deep_link.startswith("x-apple.systempreferences:")


def test_embedded_core_uses_its_sani_host_permission_results() -> None:
    """The frozen core is a child, so TCC must be evaluated as the Sani host."""
    settings = Settings(
        sani_host_accessibility_permission="granted",
        sani_host_screen_recording_permission="granted",
    )

    accessibility, screen = macos_permissions(settings)

    assert accessibility.granted is True
    assert screen.granted is True


async def test_system_status_shape_has_no_secrets(tmp_path: Path) -> None:
    import asyncio

    shim = _shim(
        tmp_path / "cua-driver",
        'echo "permission mode: bounded"\n'
        'echo "capability manifest: configured=true approved_at_startup=true valid=true"\n',
    )
    settings = Settings(
        app_env="development",
        model_provider="generic_openai_compatible",
        model_base_url="http://127.0.0.1:1",
        model_api_key="k",
        model_name="m",
        cua_command=shim,
        cua_enabled=True,
        memory_backend="sqlite",
        sani_data_dir=str(tmp_path),
        typesafe_api_key="",
        openrouter_api_key="sk-or-secret-value",
    )
    payload = await asyncio.wait_for(system_status(settings), 10)
    assert payload["driver"]["bounded_ok"] is True
    assert payload["memory_backend"] == "sqlite"
    assert payload["jev_credential"] == "openrouter"
    assert "sk-or-secret-value" not in repr(payload)
