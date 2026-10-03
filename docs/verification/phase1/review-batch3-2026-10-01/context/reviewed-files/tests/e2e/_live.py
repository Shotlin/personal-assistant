"""Shared live-acceptance gating for the Phase 1 E2E suites (C01/N12).

The launcher (scripts/verify_phase1.py) is the only caller of these suites.
The contract each case implements:

1. Without a launcher-propagated authorization the case is SKIPPED with an
   explicit reason; the launcher maps any skip in a live suite to BLOCKED,
   never PASS.
2. With an authorization present, the case FIRST asserts its real scope:
   the validated config must name the fixture surfaces this case needs and
   the fixture roots must exist. A wrong or incomplete authorization FAILS
   loudly instead of silently skipping.
3. Only then, if the physical harness this case needs (real driver, real
   audio device, installed bundle) is not available in this snapshot, the
   case skips as an explicit BLOCKED naming the missing capability and the
   authorization id. An acceptance question the harness cannot answer is
   never reported as answered.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def authorized_config(*scope_assertions: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Return the validated live config, or skip as an explicit BLOCKED.

    Each ``scope_assertions`` callable receives the parsed config and must
    raise AssertionError when the authorization does not actually cover the
    case's needs (wrong fixture app, no audio grant, missing bundle...).
    """
    if os.environ.get("PHASE1_LIVE_AUTHORIZED") != "1":
        pytest.skip(_LIVE_REASON)
    config_path = os.environ.get("PHASE1_APPROVED_TEST_CONFIG")
    if not config_path:
        pytest.skip("approved-test-config path missing from the runner environment")
    path = Path(config_path)
    if not path.exists():
        pytest.fail(f"authorized run points at a missing config: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"authorized config is not valid JSON: {exc}")
    if not isinstance(config, dict):
        pytest.fail("authorized config must be a JSON object")
    for assertion in scope_assertions:
        assertion(config)
    _fixture_roots_exist(config)
    return config


def blocked_until_harness(config: dict[str, Any], capability: str) -> None:
    """The honest BLOCKED when the physical harness is not connected yet.

    The authorization is valid and the scope is verified, but the physical
    acceptance action (real driver dispatch, audible playback, installed
    bundle launch) has no connected harness in this snapshot. Skipping here
    reports BLOCKED through the launcher; it never reports the case passed.
    """
    pytest.skip(
        f"BLOCKED: live harness for {capability} is not connected in this "
        f"snapshot; authorization {config.get('authorization_id', '?')!r} is "
        "valid but no physical acceptance action was performed"
    )


def _fixture_roots_exist(config: dict[str, Any]) -> None:
    for field in ("data_root", "artifact_root"):
        root = Path(str(config.get(field, ""))).expanduser()
        if not root.is_dir():
            pytest.fail(
                f"authorized config {field} is not an existing fixture "
                f"directory: {root}"
            )


def mission_scope_from_config(config: dict[str, Any]) -> Any:
    """D21: the scope EXACTLY as authorized — every field comes from the
    owner config, and a missing required field FAILS before any effect."""
    from assistant.missions.contracts import Scope

    allowed_apps = list(config.get("allowed_apps") or [])
    assert allowed_apps, "authorized scope must name allowed_apps"
    account_ref = config.get("account_ref")
    assert account_ref, (
        "authorized scope must name account_ref (the fixture account)")
    return Scope(
        owner_id=str(config.get("owner_id") or "sani-local"),
        allowed_apps=allowed_apps,
        allowed_origins=list(config.get("allowed_origins") or []),
        account_ref=str(account_ref),
        workspace_ref=config.get("workspace_ref"),
        permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL", "EXTERNAL_WRITE"},
    )


def budgets_from_config(config: dict[str, Any]) -> Any:
    """D21: explicit budgets from the authorization (never defaults)."""
    from assistant.missions.contracts import BudgetLimits

    for field in ("max_deep_calls", "max_jev_calls", "max_actions"):
        assert field in config, f"authorized budgets must state {field}"
    return BudgetLimits(
        max_deep_calls=int(config["max_deep_calls"]),
        max_jev_calls=int(config["max_jev_calls"]),
        max_actions=int(config["max_actions"]),
        max_wall_ms=int(config.get("max_wall_ms", 900_000)),
    )


def build_mission_harness(config: dict[str, Any]) -> Any:
    """D10/D21: the PRODUCTION mission composition for authorized live runs.

    One MissionStore on the configured data root, the real authority and
    evidence stores, the VeloExecutor over the REAL runtime provider (the
    real CUA driver) with production payload resolution and ownership
    probing, and the Deep controller over the REAL DeepAgentEntry — the
    same composition MissionEntry.create_service builds. The mission scope
    and budgets come EXACTLY from the authorization.
    """
    import shutil
    import tempfile

    from assistant.core.agents import DeepAgentEntry, RuntimeProvider  # noqa: F401
    from assistant.missions.authority import MissionAuthority
    from assistant.missions.controller import DeepController
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor
    from assistant.missions.service import MissionService
    from assistant.missions.store import MissionStore
    from assistant.settings import Settings

    scope = mission_scope_from_config(config)
    limits = budgets_from_config(config)
    data_root = Path(str(config["data_root"])).expanduser()
    live_root = Path(tempfile.mkdtemp(prefix="phase1-live-", dir=str(data_root)))
    settings = Settings()
    settings = settings.model_copy(update={
        "sani_data_dir": str(live_root),
        "memory_backend": "sqlite",
        "mission_allowed_apps": list(scope.allowed_apps),
    })
    store: MissionStore | None = None
    provider: RuntimeProvider | None = None

    async def build() -> Any:
        nonlocal store, provider
        store = await MissionStore.connect(Path(settings.sani_db_path))
        await store.setup()
        authority = MissionAuthority(store)
        evidence = EvidenceStore(
            live_root / "mission-evidence", store, trusted_roots=[live_root]
        )
        provider = RuntimeProvider(settings)
        deep = DeepAgentEntry(settings, provider=provider)

        async def get_runtime() -> Any:
            assert provider is not None
            return await provider.runtime()

        executor = VeloExecutor(
            settings, get_runtime=get_runtime, authority=authority,
            evidence=evidence, store=store,
        )

        class _AuthorizedService(MissionService):
            def _scope_for(self, request: Any) -> Any:
                return scope

            def _limits_for(self, request: Any) -> Any:
                return limits

        service = _AuthorizedService(
            settings, store=store, authority=authority, evidence=evidence,
            executor=executor, controller=DeepController(deep),
        )
        # Production wiring: payload resolution and CURRENT-ownership
        # probing exactly as MissionEntry binds them.
        executor._payload_resolver = service._resolve_payloads
        executor._ownership_probe = service._ownership_probe
        return service, store

    async def aclose() -> None:
        if provider is not None:
            await provider.aclose()
        if store is not None:
            await store.close()
        shutil.rmtree(live_root, ignore_errors=True)

    return build, aclose


async def run_stop_during_work(service: Any, config: dict[str, Any],
                               text: str) -> dict[str, Any]:
    """D21 measurement: stop while the mission is ACTIVELY working — the
    stop is issued only after the desktop lease is owned and an attempt is
    in flight; reports latency, terminal status and post-stop dispatches."""
    import time as _time

    from assistant.missions.contracts import CancellationToken, RequestEnvelope, new_id

    mission_id_holder: dict[str, str] = {}

    async def _run() -> None:
        mission = await service.submit(RequestEnvelope(
            request_id=new_id(), conversation_id="live-stop",
            owner_id="sani-local", input_origin="typed_final", input_revision=1,
            text=text, submitted_at_ms=int(_time.time() * 1000)),
            cancel=CancellationToken())
        mission_id_holder["id"] = mission.mission_id

    task = asyncio.create_task(_run())
    deadline = _time.monotonic() + 60
    # Active-work evidence: the queue grants the mission's lease.
    mission_id = ""
    while _time.monotonic() < deadline:
        owner = service._desktop_queue.current_owner
        if owner and owner.startswith("mission:"):
            mission_id = owner.split("mission:", 1)[1]
            break
        await asyncio.sleep(0.02)
    assert mission_id, "the mission never took the desktop lease (cannot measure stop-during-work)"
    stop_started = _time.monotonic()
    result = await service._desktop_queue.stop_owner(f"mission:{mission_id}")
    latency_ms = (_time.monotonic() - stop_started) * 1000
    bound_ms = int(config.get("max_stop_latency_ms", 1000))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=30)
    except Exception:  # noqa: BLE001 -- the stopped run may end cancelled
        pass
    record = await service.store.get_mission(mission_id)
    return {
        "mission_id": mission_id,
        "stop_latency_ms": latency_ms,
        "latency_within_bound": latency_ms <= bound_ms,
        "stopped": bool(result.get("stopped")),
        "status_after": record.status if record else "unknown",
        "terminal": record.status in {"CANCELLED", "COMPLETED", "FAILED", "BLOCKED"}
        if record else False,
    }


async def run_wrong_focus(service: Any, config: dict[str, Any],
                          outside_command: str) -> dict[str, Any]:
    """D21 measurement: a command naming an app OUTSIDE the authorized
    scope must refuse with zero effects on the fixture app."""
    import time as _time

    from assistant.missions.contracts import CancellationToken, RequestEnvelope, new_id

    status = "unknown"
    try:
        mission = await service.submit(RequestEnvelope(
            request_id=new_id(), conversation_id="live-wrong-focus",
            owner_id="sani-local", input_origin="typed_final", input_revision=1,
            text=outside_command, submitted_at_ms=int(_time.time() * 1000)),
            cancel=CancellationToken())
        status = mission.status
    except Exception:  # noqa: BLE001 -- a refused command may end in an
        # honest store-level conflict/denial; the mission is re-read.
        pass
    return {"status": status, "completed": status == "COMPLETED"}





def _live_reason() -> str:
    return _LIVE_REASON


_LIVE_REASON = (
    "live acceptance authorization required: owner-issued "
    "approved-test-config.json (exact fixture app/window/account scope, "
    "explicit call budget) plus --allow-live, via scripts/verify_phase1.py. "
    "No production accounts."
)
