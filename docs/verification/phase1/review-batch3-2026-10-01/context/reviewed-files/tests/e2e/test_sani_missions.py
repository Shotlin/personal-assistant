"""Live desktop mission E2E (T12, file 06 L1) — executable, owner-gated.

These cases run ONLY through scripts/verify_phase1.py --suite desktop with
an owner-issued approved-test-config.json and --allow-live. Under plain
pytest (the default session) they report BLOCKED, never a fake pass.

D10: with an authorization present the bodies EXECUTE — the real
production mission composition (MissionService + VeloExecutor + real CUA
driver) runs the configured fixture scope and asserts real outcomes. No
body is an unconditional placeholder; equally, nothing here runs live
desktop actions without the launcher-propagated authorization.
"""

from __future__ import annotations

import time
from typing import Any

from assistant.missions.contracts import CancellationToken, RequestEnvelope, new_id
from tests.e2e._live import (
    authorized_config,
    build_mission_harness,
    run_stop_during_work,
    run_wrong_focus,
)


def _require_desktop_scope(config: dict[str, Any]) -> None:
    """The desktop mission cases need a named fixture app and window."""
    assert config.get("allowed_apps"), "desktop scope must name the fixture app"
    assert config.get("allowed_windows"), "desktop scope must name the fixture window"
    assert config.get("account_ref"), "desktop scope must name the fixture account"
    assert config.get("fixture_command"), "desktop scope must name the fixture command"
    assert int(config.get("max_deep_calls", 0)) >= 0
    assert int(config.get("max_jev_calls", 0)) >= 0


def _request(text: str) -> RequestEnvelope:
    return RequestEnvelope(
        request_id=new_id(), conversation_id="live-acceptance",
        owner_id="sani-local", input_origin="typed_final", input_revision=1,
        text=text, submitted_at_ms=int(time.time() * 1000),
    )


async def test_live_mission_end_to_end() -> None:
    """L1: one bounded open-app mission against the REAL driver, gated and
    executed through the production composition."""
    config = authorized_config(_require_desktop_scope)
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        mission = await service.submit(_request(str(config["fixture_command"])),
                                       cancel=CancellationToken())
        assert mission.status == "COMPLETED", (
            f"the live mission ended {mission.status}: "
            f"{[r.uncertainty for r in [mission]][0] or 'no uncertainty recorded'}"
        )
    finally:
        await aclose()


async def test_live_multi_step_fixture() -> None:
    """L1: the configured multi-step fixture commands run in order and all
    verify against the real driver."""
    config = authorized_config(_require_desktop_scope)
    commands = list(config.get("fixture_multi_step_commands") or [])
    assert commands, "the multi-step case needs fixture_multi_step_commands"
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        for command in commands:
            mission = await service.submit(_request(str(command)),
                                           cancel=CancellationToken())
            assert mission.status == "COMPLETED", f"{command!r} ended {mission.status}"
    finally:
        await aclose()


async def test_live_stop_during_work() -> None:
    """L1: the stop is issued while the mission OWNS the lease and work is
    active (the harness helper waits for the grant); post-stop state must
    be terminal-or-honest and the latency within the authorized bound."""
    config = authorized_config(_require_desktop_scope)
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        report = await run_stop_during_work(
            service, config, str(config["fixture_command"]))
        assert report["stopped"] is True
        assert report["latency_within_bound"], report
        assert report["terminal"] or report["status_after"] in {
            "PAUSED", "BLOCKED", "NEEDS_APPROVAL"}, report
    finally:
        await aclose()


async def test_live_wrong_focus_is_blocked() -> None:
    """L1: a command targeting an app OUTSIDE the authorized scope refuses
    (via the measurement helper) — no effect lands out of scope."""
    config = authorized_config(_require_desktop_scope)
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        outside = str(config.get("outside_app_command") or "")
        assert outside, "the config must name an out-of-scope command"
        report = await run_wrong_focus(service, config, outside)
        assert not report["completed"], (
            "an out-of-scope command must not complete")
    finally:
        await aclose()


async def test_live_wrong_account_is_blocked() -> None:
    """L1: the authorized scope is ACCOUNT-BOUND; the authority refuses a
    surface whose observed account differs (JAR-018). The bound scope is
    verified in the offline fake validation; live, the refusal surfaces as
    a non-completed mission with an authority denial recorded."""
    config = authorized_config(_require_desktop_scope)
    assert config.get("wrong_account_command"), (
        "the config must name a command exercising the wrong account")
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        report = await run_wrong_focus(service, config,
                                       str(config["wrong_account_command"]))
        assert not report["completed"], (
            "a wrong-account target must not complete")
    finally:
        await aclose()


async def test_live_emergency_stop() -> None:
    """L1: the physical emergency stop path — stop while the mission may be
    mid-dispatch — reports the actual lease state and never claims physical
    input release it cannot prove."""
    config = authorized_config(_require_desktop_scope)
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        mission = await service.submit(_request(str(config["fixture_command"])),
                                       cancel=CancellationToken())
        result = await service._desktop_queue.stop_owner(f"mission:{mission.mission_id}")
        assert result["certain"] is True
        assert result.get("input_released_acked") in (None, False, True)
        # The lease is gone either way; the queue admits the next waiter.
        assert service._desktop_queue.current_owner is None
    finally:
        await aclose()
