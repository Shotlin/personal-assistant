"""Live desktop mission E2E (T12, file 06 L1) — BLOCKED without authorization.

These cases run ONLY through scripts/verify_phase1.py --suite desktop with
an owner-issued approved-test-config.json and --allow-live. Under plain
pytest (the default session) they report BLOCKED, never a fake pass.

C01/N12: an authorization alone is not acceptance. Each case first asserts
the authorization actually covers its scope (fixture app, window, account,
action budget), then reports an explicit BLOCKED naming the physical
harness capability that is not connected in this snapshot. No case body is
an empty placeholder any more.
"""

from __future__ import annotations

from typing import Any

from tests.e2e._live import authorized_config, blocked_until_harness


def _require_desktop_scope(config: dict[str, Any]) -> None:
    """The desktop mission cases need a named fixture app and window."""
    assert config.get("allowed_apps"), "desktop scope must name the fixture app"
    assert config.get("allowed_windows"), "desktop scope must name the fixture window"
    assert config.get("account_ref"), "desktop scope must name the fixture account"
    assert int(config.get("max_deep_calls", 0)) >= 0
    assert int(config.get("max_jev_calls", 0)) >= 0


def test_live_mission_end_to_end() -> None:
    """L1: one bounded open-app mission against the REAL driver."""
    config = authorized_config(_require_desktop_scope)
    blocked_until_harness(config, "real-driver single-mission acceptance")


def test_live_multi_step_fixture() -> None:
    """L1: multi-step open -> navigate -> type -> verify on the real driver."""
    config = authorized_config(_require_desktop_scope)
    blocked_until_harness(config, "real-driver multi-step acceptance")


def test_live_stop_during_work() -> None:
    """L1: emergency stop during real typing, then reconciliation."""
    config = authorized_config(_require_desktop_scope)
    blocked_until_harness(config, "real-driver physical stop measurement")
