"""Packaging acceptance (T12/K1) — live-only install/rollback flow.

Fixture provenance checks live in tests/unit/test_packaging_provenance.py.
The live mode builds/validates an isolated installed test bundle and
demonstrates same-bundle restart/rollback. NOT RUN until the owner issues
the K1 scope through the launcher.

C01/N12: with an authorization present, the case verifies the bundle and
offline constraints it needs (bundle exists, network_policy not open, no
paid units) before reporting the explicit harness BLOCKED — a provenance
mismatch fails instead of silently skipping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.e2e._live import authorized_config, blocked_until_harness


def _require_bundle_scope(config: dict[str, Any]) -> None:
    """K1 needs the actual test bundle and an offline network policy."""
    bundle = Path(str(config.get("bundle_path", ""))).expanduser()
    assert bundle.exists(), f"packaging case needs the real bundle at {bundle}"
    assert config.get("network_policy") != "open", "K1 requires an offline policy"
    assert int(config.get("max_paid_units", 1)) == 0, "packaging runs with zero paid units"


def test_live_bundle_install_and_rollback() -> None:
    """K1 live: build isolated bundle, launch, restart, rollback. Gated."""
    config = authorized_config(_require_bundle_scope)
    blocked_until_harness(config, "isolated install/restart/rollback execution")


def test_live_offline_voice_output() -> None:
    """K1 live: offline synthesis from the packaged bundle. Gated."""
    config = authorized_config(_require_bundle_scope)
    blocked_until_harness(config, "packaged-worker offline synthesis")
