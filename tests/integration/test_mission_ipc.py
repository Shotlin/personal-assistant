"""Mission IPC surface integration (T07, file 06 I1).

Handshake, mission.* methods, malformed-field rejection, and control-during-
active-stream — over the real framed dispatch with the mission registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from assistant.core.app import SaniCoreApp
from tests.integration.test_mission_core import _mission_registry, _Sidecar


@pytest.fixture()
async def sidecar(tmp_path: Path) -> Any:
    registry, _settings, mission_provider = _mission_registry(tmp_path)
    app = SaniCoreApp(registry, status_provider=None, mission_provider=mission_provider)
    sc = _Sidecar(app)
    yield sc
    await sc.close()


async def _roundtrip(
    sidecar: Any, request_id: str, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    await sidecar.send(
        {"type": "request", "id": request_id, "method": method, "params": params}
    )
    while True:
        frame = await sidecar.read()
        if frame.get("type") == "response" and frame.get("id") == request_id:
            return frame
        # Events from unrelated runs may interleave; keep reading.


async def test_agents_list_advertises_protocol_and_features(sidecar: Any) -> None:
    response = await _roundtrip(sidecar, "l1", "agents.list", {})
    assert response["ok"] is True
    assert response["result"]["protocol_version"] == 2
    assert "missions.v1" in response["result"]["features"]


async def test_mission_get_unknown_id_errors(sidecar: Any) -> None:
    response = await _roundtrip(sidecar, "g1", "mission.get", {"mission_id": "nope"})
    assert response["ok"] is False
    assert "unknown mission" in response["error"]


async def test_mission_control_malformed_fields_reject(sidecar: Any) -> None:
    """Unknown fields and bad values reject (strict authority records)."""
    response = await _roundtrip(
        sidecar, "c1", "mission.control",
        {"mission_id": "nope", "expected_plan_version": 0, "expected_control_epoch": 1,
         "kind": "PAUSE", "admin_override": True},
    )
    assert response["ok"] is False
    assert "invalid mission.control" in response["error"]


async def test_mission_control_unknown_kind_rejects(sidecar: Any) -> None:
    response = await _roundtrip(
        sidecar, "c2", "mission.control",
        {"mission_id": "nope", "expected_plan_version": 0, "expected_control_epoch": 1,
         "kind": "NUKE"},
    )
    assert response["ok"] is False


async def test_mission_surface_disabled_without_provider() -> None:
    """A core without missions answers mission.* with a clean error."""
    registry, _settings, _provider = _mission_registry(Path("/tmp/fixture-mission-ipc"))
    app = SaniCoreApp(registry, status_provider=None, mission_provider=None)
    session = _BareSession()
    from assistant.core.protocol import Request

    await app._dispatch(  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
        Request(id="m", method="mission.get", params={"mission_id": "x"}),
    )
    assert session.frames[0]["ok"] is False
    assert "not enabled" in session.frames[0]["error"]


class _BareSession:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> None:
        self.frames.append(payload)


async def test_mission_events_durable_cursor(sidecar: Any) -> None:
    """Events read from the durable sequence after a reconnect cursor."""
    created = await _create_mission(sidecar)
    mission_id = created["mission_id"]
    first = await _roundtrip(
        sidecar, "e1", "mission.events", {"mission_id": mission_id, "after_sequence": 0}
    )
    assert first["ok"] is True
    events = first["result"]["events"]
    assert events and first["result"]["chain_ok"] is True
    cursor = first["result"]["cursor"]
    # Reconnect-style read from the cursor yields no duplicates.
    second = await _roundtrip(
        sidecar, "e2", "mission.events", {"mission_id": mission_id, "after_sequence": cursor}
    )
    assert second["ok"] is True
    seen_ids = {e["event_id"] for e in events}
    for event in second["result"]["events"]:
        assert event["event_id"] not in seen_ids, "terminal/replay events must dedup"


async def _create_mission(sidecar: Any) -> dict[str, Any]:
    response = await _roundtrip(
        sidecar, "mk", "run.start",
        {"agent_id": "velo", "text": "open safari", "thread_id": "c-events",
         "run_id": "run-events"},
    )
    assert response["ok"] is True
    return response["result"]
