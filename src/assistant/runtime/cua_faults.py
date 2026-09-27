"""One place that decides what went wrong with a driver call, and what to do.

Before this module, every driver fault travelled as a string. A session that
ended, a policy that idled out, a macOS permission that was never granted and a
killed transport all arrived at the caller as `"... failed: <text>"`, so the only
recovery available was "abort the run" -- and a run that aborts on the first
stale window id is indistinguishable from one that cannot work at all.

Every marker here is copied from a capture against the pinned driver
(cua-driver-rs 0.28.2) on this Mac, not guessed:

- ``Policy loading error: capability manifest idle timeout exceeded`` -- the
  bounded daemon's answer to *every* call once its manifest idles out, including
  its own permission probe.
- ``permissions_pending: macOS Accessibility or Screen Recording permission is
  still pending; no action started, retry after the permission gate completes``
- ``session 'p2' has ended; tool call 'get_screen_size' was rejected. Call
  start_session with this id to revive it before issuing further actions, or use
  a new session id.``
- ``Missing required integer field: window_id``
- ``{"code": "window_target_not_found", "effect": "refused", "pid": ...,
  "candidates": []}`` and ``{"code": "window_not_found", "effect": "refused",
  "reason": "WindowServer has no record of window ... (closed or stale);
  re-enumerate windows and re-target"}``
- ``{"refusal": {"code": "invalid_element_token", ...}, "status": "refused"}``,
  ``{"error": "APP_NOT_INSTALLED"}``
- Transport death surfaces as a bare ``anyio.ClosedResourceError`` with an empty
  message, which is why classification takes the exception *type* as well as the
  text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class CuaFault(StrEnum):
    """What failed. Ordered roughly by who can fix it."""

    #: The stdio MCP lease is gone; calls cannot reach any daemon.
    TRANSPORT = "transport"
    #: Nothing is listening on the endpoint Sani owns.
    DAEMON = "daemon"
    #: The daemon refuses every call on its capability policy (bounded legacy).
    POLICY = "policy"
    #: macOS has not authorized the host whose identity the driver acts under.
    PERMISSION = "permission"
    #: A named session ended; the driver explicitly invites one revival.
    SESSION = "session"
    #: The app/pid to act on is not there (not running, not installed, no pid).
    TARGET = "target"
    #: The window id is stale or closed; re-enumerate and re-target.
    WINDOW = "window"
    #: The action itself was refused (unknown element token, unsupported press).
    ACTION = "action"
    #: The driver acted but cannot prove the effect. Not a failure.
    UNVERIFIABLE = "unverifiable"
    #: Nothing matched. Never guess a cause that was not observed.
    UNKNOWN = "unknown"


class FaultRecovery(StrEnum):
    """The next move a caller should make. A hint, never an automatic loop."""

    RECONNECT_TRANSPORT = "reconnect_transport"
    RECOVER_DAEMON = "recover_daemon"
    REAPPROVE_POLICY = "reapprove_policy"
    GRANT_PERMISSION = "grant_permission"
    REVIVE_SESSION = "revive_session"
    RETARGET_APP = "retarget_app"
    REENUMERATE_WINDOWS = "reenumerate_windows"
    REOBSERVE = "reobserve"
    VERIFY_VISUALLY = "verify_visually"
    ASK_USER = "ask_user"
    ABORT = "abort"


#: One message per fault, for the UI and for the model. Actionable, never a
#: bare error class.
FAULT_MESSAGES: dict[CuaFault, str] = {
    CuaFault.TRANSPORT: (
        "Sani lost its connection to the computer-control driver; the driver will "
        "be reconnected."
    ),
    CuaFault.DAEMON: (
        "The computer-control driver is not answering. Sani will restart its own "
        "private driver process."
    ),
    CuaFault.POLICY: (
        "The driver's capability policy expired and is refusing every action. This "
        "is not a macOS permission problem; the driver needs re-approving."
    ),
    CuaFault.PERMISSION: (
        "macOS has not authorized Sani to control the keyboard and screen. Grant "
        "Accessibility and Screen Recording to Sani."
    ),
    CuaFault.SESSION: (
        "The driver session ended; Sani will start a fresh one and re-check the "
        "screen."
    ),
    CuaFault.TARGET: "The application to act on is not running or is not installed.",
    CuaFault.WINDOW: "The window changed or closed; Sani will re-read the windows and aim again.",
    CuaFault.ACTION: (
        "That specific action was refused by the target; Sani will choose a "
        "different way to do it."
    ),
    CuaFault.UNVERIFIABLE: (
        "The driver acted but cannot prove the effect from the accessibility tree "
        "alone."
    ),
    CuaFault.UNKNOWN: "The computer-control driver failed in a way Sani has not seen.",
}

RECOVERY_FOR: dict[CuaFault, FaultRecovery] = {
    CuaFault.TRANSPORT: FaultRecovery.RECONNECT_TRANSPORT,
    CuaFault.DAEMON: FaultRecovery.RECOVER_DAEMON,
    CuaFault.POLICY: FaultRecovery.REAPPROVE_POLICY,
    CuaFault.PERMISSION: FaultRecovery.GRANT_PERMISSION,
    CuaFault.SESSION: FaultRecovery.REVIVE_SESSION,
    CuaFault.TARGET: FaultRecovery.RETARGET_APP,
    CuaFault.WINDOW: FaultRecovery.REENUMERATE_WINDOWS,
    CuaFault.ACTION: FaultRecovery.REOBSERVE,
    CuaFault.UNVERIFIABLE: FaultRecovery.VERIFY_VISUALLY,
    CuaFault.UNKNOWN: FaultRecovery.ABORT,
}

#: A fault whose replay cannot act twice. `start_session` is documented
#: idempotent, and a read never mutates -- a click is neither.
SAFE_TO_REPLAY = frozenset(
    {CuaFault.PERMISSION, CuaFault.POLICY, CuaFault.DAEMON, CuaFault.TARGET}
)

#: Faults that mean "look again", not "stop".
RECOVERABLE = frozenset(
    {
        CuaFault.TRANSPORT,
        CuaFault.DAEMON,
        CuaFault.POLICY,
        CuaFault.SESSION,
        CuaFault.TARGET,
        CuaFault.WINDOW,
        CuaFault.ACTION,
        CuaFault.UNVERIFIABLE,
    }
)

_MISSING_FIELD = re.compile(r"missing required (?:integer |string |)?field: (\w+)", re.I)

#: Markers in driver text, in match order. Most specific first.
_TEXT_MARKERS: tuple[tuple[CuaFault, tuple[str, ...]], ...] = (
    (
        CuaFault.POLICY,
        ("policy loading error", "idle timeout exceeded", "capability manifest"),
    ),
    (CuaFault.PERMISSION, ("permissions_pending", "permission is still pending")),
    (
        CuaFault.SESSION,
        (
            "has ended and must be revived",
            "must be revived",
            "has ended; tool call",
            "call start_session with this id",
        ),
    ),
    (CuaFault.WINDOW, ("window_not_found", "no record of window", "window_target_not_found")),
    (
        CuaFault.TARGET,
        ("app_not_installed", "application not found", "no running application"),
    ),
    (
        CuaFault.ACTION,
        ("invalid_element_token", "-25206", "action unsupported", "cannot perform"),
    ),
    (CuaFault.TRANSPORT, ("closed resource", "end of stream", "transport is closed")),
    (
        CuaFault.DAEMON,
        ("daemon is not running", "cannot connect", "connection refused", "no such file"),
    ),
)


class FaultDetail:
    """A classified fault: what, a short evidence string, and the next move."""

    __slots__ = ("fault", "detail", "recovery")

    def __init__(self, fault: CuaFault, detail: str = "") -> None:
        self.fault = fault
        self.detail = detail[:300]
        self.recovery = RECOVERY_FOR[fault]

    @property
    def message(self) -> str:
        return FAULT_MESSAGES[self.fault]

    @property
    def recoverable(self) -> bool:
        return self.fault in RECOVERABLE

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"FaultDetail({self.fault.value}, detail={self.detail!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, FaultDetail)
            and other.fault == self.fault
            and other.detail == self.detail
        )


def _missing_field_fault(text: str) -> CuaFault | None:
    """`Missing required integer field: pid/window_id` is a targeting bug.

    pid and window_id are the two fields the driver demands for every aimed
    action, so a complaint about one is Sani addressing an action at nothing --
    which is a target fault, not a transport or permission fault.
    """
    match = _MISSING_FIELD.search(text)
    if match is None:
        return None
    return CuaFault.TARGET if match.group(1).lower() == "pid" else CuaFault.WINDOW


def classify_text(text: str) -> CuaFault:
    """Name the fault from the driver's own words."""
    if not text:
        return CuaFault.UNKNOWN
    lowered = text.lower()
    field_fault = _missing_field_fault(lowered)
    if field_fault is not None:
        return field_fault
    for fault, markers in _TEXT_MARKERS:
        if any(marker in lowered for marker in markers):
            return fault
    return CuaFault.UNKNOWN


def classify_exception(exc: BaseException) -> CuaFault:
    """Name the fault from a raised exception.

    The anyio errors an MCP teardown raises carry no message at all, so the type
    is the only signal there. An exception already knowing its own fault (a
    ``fault`` attribute) wins over any text matching, because it was classified
    where the failure actually happened.
    """
    known = getattr(exc, "fault", None)
    if isinstance(known, CuaFault):
        return known
    name = type(exc).__name__
    if name in {
        "ClosedResourceError",
        "BrokenResourceError",
        "EndOfStream",
        "BusyResourceError",
    }:
        return CuaFault.TRANSPORT
    if isinstance(exc, EOFError) or isinstance(exc, ConnectionError):
        return CuaFault.TRANSPORT
    if isinstance(exc, TimeoutError):
        return CuaFault.TRANSPORT
    return classify_text(f"{name}: {exc}")


def _codes(payload: Mapping[str, Any]) -> list[str]:
    """Every code-ish string the driver uses in a refusal shape."""
    codes: list[str] = []
    for key in ("code", "error", "reason", "status"):
        value = payload.get(key)
        if isinstance(value, str):
            codes.append(value)
    refusal = payload.get("refusal")
    if isinstance(refusal, Mapping):
        for key in ("code", "message"):
            value = refusal.get(key)
            if isinstance(value, str):
                codes.append(value)
    return codes


def classify_payload(payload: Mapping[str, Any]) -> CuaFault | None:
    """Classify the driver's structured refusals, which are not free text.

    Returns ``None`` when the payload carries no fault at all -- a successful
    ``{"windows": []}`` is an empty answer, not an error.
    """
    codes = " ".join(_codes(payload))
    if codes:
        fault = classify_text(codes)
        if fault is not CuaFault.UNKNOWN:
            return fault
    effect = str(payload.get("effect", "")).lower()
    if effect == "unverifiable":
        return CuaFault.UNVERIFIABLE
    if effect == "refused":
        return CuaFault.ACTION
    return None


def classify_outcome_text(text: str, structured: Mapping[str, Any] | None = None) -> FaultDetail:
    """Best available classification of one normalized tool outcome."""
    if structured:
        fault = classify_payload(structured)
        if fault is not None:
            return FaultDetail(fault, text or str(dict(structured)))
    fault = classify_text(text)
    if fault is CuaFault.UNKNOWN and text:
        effect = text.lower()
        if "unverifiable" in effect:
            fault = CuaFault.UNVERIFIABLE
    return FaultDetail(fault, text)
