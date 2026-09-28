"""Live safety E2E (T12, file 06 L1) — BLOCKED without authorization.

Wrong-focus paste, wrong-account reads, and emergency stop with real
physical input are L-gated: they require the owner-issued fixture config
and --allow-live through the Phase 1 launcher. Default sessions must see
them fail loudly rather than skip silently.

C01/N12: with an authorization present, each case verifies the safety
scope it needs (account binding, window identity, stop surface) before
reporting the explicit harness BLOCKED — a wrong scope fails, it does not
silently skip.
"""

from __future__ import annotations

from typing import Any

from tests.e2e._live import authorized_config, blocked_until_harness


def _require_safety_scope(config: dict[str, Any]) -> None:
    """Safety cases are meaningless without a bound account/window pair."""
    assert config.get("account_ref"), "wrong-account case needs a bound fixture account"
    assert config.get("workspace_ref"), "wrong-workspace case needs a bound workspace"
    assert config.get("allowed_windows"), "wrong-focus case needs the fixture window name"


def test_live_wrong_focus_is_blocked() -> None:
    """L1: real focus moves to another app immediately before a paste."""
    config = authorized_config(_require_safety_scope)
    blocked_until_harness(config, "real-driver wrong-focus interception")


def test_live_wrong_account_is_blocked() -> None:
    """L1: fixture app signed in as the wrong account blocks reads/writes."""
    config = authorized_config(_require_safety_scope)
    blocked_until_harness(config, "real-driver wrong-account surface")


def test_live_emergency_stop() -> None:
    """L1: physical stop latch during typing; held-input release measured."""
    config = authorized_config(_require_safety_scope)
    blocked_until_harness(config, "real-driver emergency-stop measurement")
