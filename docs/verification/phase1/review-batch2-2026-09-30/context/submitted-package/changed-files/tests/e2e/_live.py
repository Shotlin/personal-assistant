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


def build_mission_harness(config: dict[str, Any]) -> Any:
    """D10: the PRODUCTION mission composition for authorized live runs.

    One MissionStore on the configured data root, the real authority and
    evidence stores, the VeloExecutor over the REAL runtime provider (the
    real CUA driver), and the one MissionService. This is the exact
    composition sani-core builds at startup — no test double sits between
    the mission and the driver. Callers get an async factory plus an
    ``aclose``.
    """
    import shutil
    import tempfile

    from assistant.core.agents import RuntimeProvider
    from assistant.missions.authority import MissionAuthority
    from assistant.missions.controller import DeepController
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor
    from assistant.missions.service import MissionService
    from assistant.missions.store import MissionStore
    from assistant.settings import Settings

    data_root = Path(str(config["data_root"])).expanduser()
    live_root = Path(tempfile.mkdtemp(prefix="phase1-live-", dir=str(data_root)))
    settings = Settings()
    settings = settings.model_copy(update={
        "sani_data_dir": str(live_root),
        "memory_backend": "sqlite",
        "mission_allowed_apps": list(config.get("allowed_apps") or []),
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

        async def get_runtime() -> Any:
            assert provider is not None
            return await provider.runtime()

        executor = VeloExecutor(
            settings, get_runtime=get_runtime, authority=authority,
            evidence=evidence, store=store,
        )
        service = MissionService(
            settings, store=store, authority=authority, evidence=evidence,
            executor=executor, controller=DeepController(None),
        )
        return service, store

    async def aclose() -> None:
        if provider is not None:
            await provider.aclose()
        if store is not None:
            await store.close()
        shutil.rmtree(live_root, ignore_errors=True)

    return build, aclose


def _live_reason() -> str:
    return _LIVE_REASON


_LIVE_REASON = (
    "live acceptance authorization required: owner-issued "
    "approved-test-config.json (exact fixture app/window/account scope, "
    "explicit call budget) plus --allow-live, via scripts/verify_phase1.py. "
    "No production accounts."
)
