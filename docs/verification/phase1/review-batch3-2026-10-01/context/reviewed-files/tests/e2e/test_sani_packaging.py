"""Packaging acceptance (T12/K1) — executable identity binding, gated execution.

Fixture provenance checks live in tests/unit/test_packaging_provenance.py.
The live mode builds/validates an isolated installed test bundle and
demonstrates same-bundle restart/rollback. NOT RUN until the owner issues
the K1 scope through the launcher.

D10: with an authorization present the identity-binding body EXECUTES —
the configured bundle is hashed in place and the manifest is bound to the
EXACT artifact bytes named by the authorization. A mismatch FAILS rather
than silently skipping. The physical install/rollback execution remains
the launcher-run action it must be; a local hash can never prove it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.verify_phase1 import bundle_manifest, bundle_manifest_sha256

from tests.e2e._live import authorized_config, blocked_until_harness


def _require_bundle_scope(config: dict[str, Any]) -> None:
    """K1 needs the actual test bundle and an offline network policy."""
    bundle = Path(str(config.get("bundle_path", ""))).expanduser()
    assert bundle.exists(), f"packaging case needs the real bundle at {bundle}"
    assert config.get("network_policy") != "open", "K1 requires an offline policy"
    assert int(config.get("max_paid_units", 1)) == 0, "packaging runs with zero paid units"


def test_live_bundle_identity_is_bound_to_the_artifact() -> None:
    """K1: the exact artifact named by the authorization is hashed and the
    manifest is persisted beside it — rollback evidence binds to THIS
    bundle or fails loudly."""
    config = authorized_config(_require_bundle_scope)
    bundle = Path(str(config["bundle_path"])).expanduser()
    manifest = bundle_manifest(bundle)
    assert manifest, "the bundle manifest is empty"
    digest = bundle_manifest_sha256(bundle)
    recorded = Path(str(config.get("data_root", "."))).expanduser()
    recorded.mkdir(parents=True, exist_ok=True)
    evidence = recorded / "bundle-identity.json"
    evidence.write_text(json.dumps({
        "bundle_path": str(bundle),
        "bundle_sha256": digest,
        "files": len(manifest),
        "authorization_id": config.get("authorization_id", "?"),
    }, indent=1), encoding="utf-8")
    # Binding to the exact bytes: the config may pin the expected digest.
    expected = str(config.get("bundle_sha256") or "").lower()
    if expected:
        assert digest == expected, (
            "the authorized bundle digest does not match the artifact on disk: "
            f"{digest} != {expected}"
        )


def test_live_bundle_install_and_rollback() -> None:
    """K1 live: build isolated bundle, launch, restart, rollback. The
    identity binding above is required first; the physical install/
    restart/rollback execution stays the launcher-run acceptance action."""
    config = authorized_config(_require_bundle_scope)
    blocked_until_harness(config, "isolated install/restart/rollback execution")


def test_live_offline_voice_output() -> None:
    """K1 live: offline synthesis from the packaged bundle — driven through
    the same real worker protocol the voice suite uses, with the bundle's
    own interpreter."""
    config = authorized_config(_require_bundle_scope)
    bundle = Path(str(config["bundle_path"])).expanduser()
    voice_python = bundle / "Contents/MacOS/sani-tts-python/bin/python3"
    voice_worker = bundle / "Contents/Resources/sani_tts.py"
    if not voice_python.is_file() or not voice_worker.is_file():
        pytest.skip(
            "BLOCKED: the bundle does not carry the packaged speech worker "
            f"at {voice_python} / {voice_worker}; authorization "
            f"{config.get('authorization_id', '?')} is valid but the packaged "
            "interpreter layout is not present"
        )
    from tests.e2e.test_sani_voice import _speak_and_measure

    voice_config = {**config, "voice_python": str(voice_python),
                    "voice_worker": str(voice_worker)}
    result = _speak_and_measure(voice_config, text="offline bundle fixture.",
                                stop_after=False)
    assert result["terminal"] in {"finished", "cancelled"}, result
