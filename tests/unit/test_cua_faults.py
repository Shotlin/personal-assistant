"""The fault taxonomy, asserted against captured driver evidence.

Every string here is a real driver response recorded from the pinned
cua-driver-rs 0.28.2 on this Mac (Phase 0/2 spikes), not a plausible invention:
an invented marker is how three computer-control bugs once survived a fully
passing suite.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from assistant.runtime.cua_faults import (
    RECOVERY_FOR,
    CuaFault,
    FaultDetail,
    FaultRecovery,
    classify_exception,
    classify_payload,
    classify_text,
)

# --- live captures -----------------------------------------------------------

POLICY_LOCKOUT = "Policy loading error: capability manifest idle timeout exceeded"
PERMISSIONS_PENDING = (
    "permissions_pending: macOS Accessibility or Screen Recording permission is "
    "still pending; no action started, retry after the permission gate completes"
)
SESSION_ENDED_0_28_2 = (
    "session 'p2' has ended; tool call 'get_screen_size' was rejected. Call "
    "start_session with this id to revive it before issuing further actions, "
    "or use a new session id."
)
SESSION_ENDED_OLDER = "session mcp-1-2 has ended and must be revived before any action"
MISSING_PID = "Missing required integer field: pid"
MISSING_WINDOW = "Missing required integer field: window_id"


@pytest.mark.parametrize(
    "text, expected",
    [
        (POLICY_LOCKOUT, CuaFault.POLICY),
        (PERMISSIONS_PENDING, CuaFault.PERMISSION),
        (SESSION_ENDED_0_28_2, CuaFault.SESSION),
        (SESSION_ENDED_OLDER, CuaFault.SESSION),
        (MISSING_PID, CuaFault.TARGET),
        (MISSING_WINDOW, CuaFault.WINDOW),
        ('{"code": "window_not_found", "effect": "refused"}', CuaFault.WINDOW),
        ('{"error": "APP_NOT_INSTALLED"}', CuaFault.TARGET),
        ("", CuaFault.UNKNOWN),
        ("some app refused that press", CuaFault.UNKNOWN),
    ],
)
def test_driver_text_maps_to_one_named_fault(text: str, expected: CuaFault) -> None:
    assert classify_text(text) is expected


def test_an_unrecognised_fault_stays_unknown_rather_than_becoming_a_guess() -> None:
    """The old code's failure mode: an unmatched string collapsed into
    "not_authorized", which sent the user to System Settings for a bug that was
    nothing of the kind."""
    detail = FaultDetail(CuaFault.UNKNOWN, "some app refused that press")
    assert detail.recovery is FaultRecovery.ABORT
    assert not detail.recoverable


# --- structured refusals -----------------------------------------------------


def test_structured_refusals_are_read_as_structured() -> None:
    assert (
        classify_payload(
            {
                "refusal": {"code": "invalid_element_token", "message": "bad"},
                "status": "refused",
            }
        )
        is CuaFault.ACTION
    )
    assert (
        classify_payload(
            {
                "code": "window_not_found",
                "effect": "refused",
                "reason": "WindowServer has no record of window 999999 "
                "(closed or stale); re-enumerate windows and re-target",
            }
        )
        is CuaFault.WINDOW
    )
    assert (
        classify_payload({"code": "window_target_not_found", "effect": "refused", "pid": 9})
        is CuaFault.WINDOW
    )
    assert classify_payload({"effect": "unverifiable"}) is CuaFault.UNVERIFIABLE
    assert classify_payload({"windows": []}) is None


# --- exceptions --------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        anyio.ClosedResourceError(),
        anyio.EndOfStream(),
        anyio.BrokenResourceError(),
        EOFError(),
        ConnectionResetError(),
        TimeoutError(),
    ],
)
def test_a_dead_lease_is_a_transport_fault_even_with_no_message(exc: BaseException) -> None:
    # The captured real failure of a killed daemon is `anyio.ClosedResourceError`
    # with an EMPTY message: text matching alone can never see it.
    assert classify_exception(exc) is CuaFault.TRANSPORT


def test_an_exception_carrying_its_own_fault_is_not_re_matched() -> None:
    class Known(RuntimeError):
        fault = CuaFault.PERMISSION

    assert classify_exception(Known("whatever")) is CuaFault.PERMISSION


# --- recovery mapping --------------------------------------------------------


def test_every_fault_names_a_next_move() -> None:
    for fault in CuaFault:
        assert fault in RECOVERY_FOR
    assert RECOVERY_FOR[CuaFault.TRANSPORT] is FaultRecovery.RECONNECT_TRANSPORT
    assert RECOVERY_FOR[CuaFault.DAEMON] is FaultRecovery.RECOVER_DAEMON
    assert RECOVERY_FOR[CuaFault.POLICY] is FaultRecovery.REAPPROVE_POLICY
    assert RECOVERY_FOR[CuaFault.PERMISSION] is FaultRecovery.GRANT_PERMISSION
    assert RECOVERY_FOR[CuaFault.WINDOW] is FaultRecovery.REENUMERATE_WINDOWS


def test_a_detail_carries_an_actionable_message_not_an_error_class() -> None:
    detail = FaultDetail(CuaFault.PERMISSION, PERMISSIONS_PENDING)
    assert "Accessibility" in detail.message
    assert detail.detail == PERMISSIONS_PENDING[:300]
    assert FaultDetail(CuaFault.WINDOW).recoverable


def test_classification_of_a_real_refusal_shape_is_wired_end_to_end() -> None:
    """`classify_outcome_text` prefers the structured code over the text."""
    from assistant.runtime.cua_faults import classify_outcome_text

    structured: dict[str, Any] = {
        "code": "window_not_found",
        "effect": "refused",
        "reason": "WindowServer has no record of window 999999",
    }
    detail = classify_outcome_text("scroll error", structured)
    assert detail.fault is CuaFault.WINDOW
    assert detail.recovery is FaultRecovery.REENUMERATE_WINDOWS
