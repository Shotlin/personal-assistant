"""Unit tests for the bounded-daemon posture guard (2026-09-18 incident).

The driver's ``mcp`` proxy resurrects a dead daemon in standard mode when
``open --args`` drops arguments (this host). ``_evaluate_daemon_status``
must fail closed for every posture except bounded + approved manifest.
"""

from assistant.tools.cua import _evaluate_daemon_status

BOUNDED_OK = """Cua Driver daemon is running
  socket: /Users/sayan/Library/Caches/cua-driver/cua-driver.sock
  pid: 5699
  permission mode: bounded (trusted_startup_configuration)
  user policy: configured=false, active=false, valid=true
  managed policy: configured=false, active=false, valid=true
  authorization host: unavailable (unavailable)
  capability manifest: configured=true, approved_at_startup=true, valid=true
  capability manifest sha256: 107ff67558fbf72ca96690eea7f01de41cb4eaf8a2ff28f886b25ca91ba32eb7
"""

STANDARD_MODE = BOUNDED_OK.replace(
    "permission mode: bounded (trusted_startup_configuration)",
    "permission mode: standard (built_in_default)",
).replace("  capability manifest sha256: 107ff67558fbf72ca96690eea7f01de41cb4eaf8a2ff28f886b25ca91ba32eb7\n", "")

NOT_RUNNING = "Cua Driver daemon is not running"

MANIFEST_UNAPPROVED = BOUNDED_OK.replace(
    "capability manifest: configured=true, approved_at_startup=true, valid=true",
    "capability manifest: configured=false, approved_at_startup=false, valid=true",
)


def test_bounded_manifest_approved_passes() -> None:
    assert _evaluate_daemon_status(BOUNDED_OK) is None


def test_missing_daemon_fails_closed() -> None:
    reason = _evaluate_daemon_status(NOT_RUNNING)
    assert reason is not None
    assert "daemon is not running" in reason
    assert "LaunchAgent" in reason


def test_standard_mode_fails_closed() -> None:
    reason = _evaluate_daemon_status(STANDARD_MODE)
    assert reason is not None
    assert "not in bounded mode" in reason
    assert "open --args" in reason


def test_manifest_not_approved_fails_closed() -> None:
    reason = _evaluate_daemon_status(
        BOUNDED_OK.replace(
            "capability manifest: configured=true, approved_at_startup=true, valid=true",
            "capability manifest: configured=false, approved_at_startup=false, valid=true",
        )
    )
    assert reason is not None
    assert "capability manifest" in reason


def test_unreadable_mode_fails_closed() -> None:
    reason = _evaluate_daemon_status("some unexpected output\n")
    assert reason is not None
    assert "not in bounded mode" in reason
