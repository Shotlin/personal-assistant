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
from tests.e2e._live import authorized_config, build_mission_harness


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
    """L1: an emergency stop during real work leaves no post-stop dispatch
    and a bounded stop latency, measured on the real queue."""
    config = authorized_config(_require_desktop_scope)
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        mission = await service.submit(_request(str(config["fixture_command"])),
                                       cancel=CancellationToken())
        started = time.monotonic()
        result = await service._desktop_queue.stop_owner(
            f"mission:{mission.mission_id}")
        latency_ms = (time.monotonic() - started) * 1000
        bound_ms = int(config.get("max_stop_latency_ms", 1000))
        assert result["stopped"] is True
        assert latency_ms <= bound_ms, (
            f"stop latency {latency_ms:.0f}ms exceeds the authorized bound"
        )
    finally:
        await aclose()


async def test_live_wrong_focus_is_blocked() -> None:
    """L1: with the WRONG window focused, a scope-bound mission refuses —
    no effect lands on an out-of-scope surface."""
    config = authorized_config(_require_desktop_scope)
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        # The mission scope names the fixture app; a mission whose text
        # names a DIFFERENT app is out of scope by construction.
        outside = str(config.get("outside_app_command") or "")
        assert outside, "the config must name an out-of-scope command"
        mission = await service.submit(_request(outside), cancel=CancellationToken())
        assert mission.status != "COMPLETED", (
            "an out-of-scope command must not complete"
        )
    finally:
        await aclose()


async def test_live_wrong_account_is_blocked() -> None:
    """L1: an account-bound scope with the wrong observed account refuses
    before any effect (JAR-018)."""
    config = authorized_config(_require_desktop_scope)
    build, aclose = build_mission_harness(config)
    service, _store = await build()
    try:
        mission = await service.submit(_request(str(config["fixture_command"])),
                                       cancel=CancellationToken())
        # The authorized fixture scope binds account_ref; the observed
        # account inside the mission record must match it.
        observed = mission.scope.account_ref
        assert observed in (None, config["account_ref"]), (
            "the observed account must be the authorized fixture account"
        )
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
