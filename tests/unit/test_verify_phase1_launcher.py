"""Launcher validation tests (R01, file 06): RP10 + harness integrity.

The acceptance launcher must REFUSE: missing/expired config, wildcards,
unknown fields, wrong types, a config signed for a different revision or
dirty state, a stale bundle — and must never label an all-skipped or
missing-case suite PASS (RP10).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from scripts.verify_phase1 import (
    _evaluate_pytest_status,
    bundle_manifest,
    bundle_manifest_sha256,
    dirty_diff_sha256,
    source_manifest,
    source_manifest_sha256,
    validate_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path, **overrides: Any) -> Path:
    bundle = tmp_path / "bundle.tar"
    bundle.write_bytes(b"fixture bundle bytes")
    config: dict[str, Any] = {
        "schema_version": 1,
        "authorization_id": "auth-fixture-001",
        "expires_at": time.time() + 3600,
        "repo_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, cwd=REPO_ROOT,
        ).stdout.strip(),
        "dirty_diff_sha256": dirty_diff_sha256(),
        "bundle_path": str(bundle),
        "bundle_sha256": bundle_manifest_sha256(bundle),
        "data_root": str(tmp_path / "data"),
        "artifact_root": str(tmp_path / "artifacts"),
        "allowed_apps": ["com.fixture.browser"],
        "allowed_windows": ["fixture-window-1"],
        "allowed_origins": ["https://fixture.local"],
        "account_ref": "acct-fixture",
        "workspace_ref": "ws-fixture",
        "allowed_effects": ["READ_ONLY", "REPEATABLE_LOCAL"],
        "max_deep_calls": 4,
        "max_jev_calls": 4,
        "max_paid_units": 0,
        "audio_allowed": False,
        "network_policy": "offline",
        "cleanup_manifest": {"fixture_files": []},
    }
    config.update(overrides)
    path = tmp_path / "approved-test-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_valid_config_accepted(tmp_path: Path) -> None:
    config = validate_config(_config(tmp_path))
    assert config["authorization_id"] == "auth-fixture-001"


def test_missing_config_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(tmp_path / "absent.json")


def test_missing_field_refused(tmp_path: Path) -> None:
    path = _config(tmp_path)
    config = json.loads(path.read_text())
    del config["authorization_id"]
    path.write_text(json.dumps(config))
    with pytest.raises(SystemExit):
        validate_config(path)


def test_unknown_field_refused(tmp_path: Path) -> None:
    """Strict schema: unknown keys reject rather than being ignored."""
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, admin_override=True))


def test_wrong_field_type_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, max_deep_calls="unlimited"))


def test_wildcard_scope_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, allowed_apps=["*"]))


def test_empty_scope_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, allowed_origins=[]))


def test_expired_authorization_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, expires_at=time.time() - 10))


def test_paid_units_above_zero_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, max_paid_units=3))


def test_stale_revision_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, repo_sha="0" * 40))


def test_changed_dirty_source_refused(tmp_path: Path) -> None:
    """The authorization binds to tracked+untracked content: a changed dirty
    hash is rejected even when HEAD matches."""
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, dirty_diff_sha256="0" * 64))


def test_mismatched_bundle_hash_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, bundle_sha256="a" * 64))


def test_open_network_policy_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        validate_config(_config(tmp_path, network_policy="open"))


def test_bundle_directory_produces_tree_manifest(tmp_path: Path) -> None:
    """A macOS .app bundle is a directory: the manifest covers its files."""
    app = tmp_path / "Sani.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("plist")
    (app / "Contents" / "MacOS" / "sani").write_bytes(b"binary")
    manifest = bundle_manifest(app)
    assert set(manifest) == {"Contents/Info.plist", "Contents/MacOS/sani"}
    assert len(bundle_manifest_sha256(app)) == 64


# -- RP10: all-skipped / missing-case suites are BLOCKED, never PASS ---------


def test_all_skipped_suite_is_blocked() -> None:
    """RP10 regression: the skipped-voice probe must map to BLOCKED."""
    status, reason = _evaluate_pytest_status(
        {"tests": 2, "failures": 0, "errors": 0, "skipped": 2}
    )
    assert status == "BLOCKED", reason
    assert "skipped" in reason.lower()


def test_partially_skipped_suite_is_blocked() -> None:
    status, _ = _evaluate_pytest_status(
        {"tests": 5, "failures": 0, "errors": 0, "skipped": 1}
    )
    assert status == "BLOCKED"


def test_zero_case_suite_is_blocked() -> None:
    status, reason = _evaluate_pytest_status(
        {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    )
    assert status == "BLOCKED", "missing required cases cannot pass"


def test_failing_suite_is_fail() -> None:
    status, _ = _evaluate_pytest_status(
        {"tests": 5, "failures": 1, "errors": 0, "skipped": 0}
    )
    assert status == "FAIL"


def test_clean_suite_is_pass() -> None:
    status, reason = _evaluate_pytest_status(
        {"tests": 5, "failures": 0, "errors": 0, "skipped": 0}
    )
    assert status == "PASS", reason


# -- source binding: tracked AND untracked files ------------------------------


def test_source_manifest_includes_untracked_implementation() -> None:
    manifest = source_manifest()
    assert "scripts/verify_phase1.py" in manifest
    assert any(key.startswith("src/assistant/missions/") for key in manifest), (
        "untracked mission modules must be part of the evidence binding"
    )


def test_source_manifest_sha_is_content_bound() -> None:
    manifest = source_manifest()
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    assert source_manifest_sha256() == hashlib.sha256(canonical.encode()).hexdigest()


# -- config propagation to the isolated runner --------------------------------


def test_live_env_propagates_authorization(tmp_path: Path) -> None:
    """The validated config must actually reach the runner environment."""
    from scripts.verify_phase1 import _live_env

    config_path = _config(tmp_path)
    env = _live_env(config_path)
    assert env["PHASE1_APPROVED_TEST_CONFIG"] == str(config_path)
    assert env["PHASE1_LIVE_AUTHORIZED"] == "1"


def test_fixture_env_is_isolated_allowlist() -> None:
    """Fixture runs must not inherit ambient credentials or live settings."""
    from scripts.verify_phase1 import _fixture_env

    env = _fixture_env(None)
    assert env["CUA_ENABLED"] == "false"
    assert env["OPENROUTER_API_KEY"] == "fixture-not-a-secret"
    assert "PHASE1_LIVE_AUTHORIZED" not in env


def test_suite_names_resolve_to_existing_files() -> None:
    """Every advertised suite maps to files that exist (RP10 companion)."""
    from scripts.verify_phase1 import REPO_ROOT as root
    from scripts.verify_phase1 import SUITE_FILES

    for suite, (_kind, paths) in SUITE_FILES.items():
        if suite in {"rust", "renderer"}:
            continue
        for relative in paths:
            assert (root / relative).exists(), f"{suite}: missing {relative}"


# -- N12/C01: fresh JUnit, process exit, expected inventory -------------------


def test_nonzero_exit_cannot_pass_on_stale_junit(tmp_path: Path) -> None:
    """NP12 regression: a child that exits 7 must FAIL even when a previous
    passing JUnit report sits at the expected path."""
    from scripts.verify_phase1 import run_suite

    stale = tmp_path / "stale"
    stale.mkdir()
    (stale / "child.xml").write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>'
    )
    gate = run_suite(
        "child",
        [sys.executable, "-c", "raise SystemExit(7)", "pytest"],
        environment_kind="F",
        evidence_dir=stale,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert gate["status"] == "FAIL", gate["observed"]
    assert gate["exit_code"] == 7


def test_pytest_report_must_be_fresh(tmp_path: Path) -> None:
    """A green JUnit from a previous run is deleted before the run; a run
    that writes none cannot pass."""
    from scripts.verify_phase1 import run_suite

    stale = tmp_path / "fresh"
    stale.mkdir()
    (stale / "suite.xml").write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
    )
    # Not a real pytest invocation (no -m pytest): falls through the plain
    # child branch, whose nonzero exit is a FAIL.
    gate = run_suite(
        "suite",
        [sys.executable, "-c", "raise SystemExit(3)"],
        environment_kind="F",
        evidence_dir=stale,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert gate["status"] == "FAIL"
    assert not (stale / "suite.xml").exists(), "stale report must be cleared first"


def test_missing_expected_cases_is_blocked(tmp_path: Path) -> None:
    """A green fresh report that omits a required case is BLOCKED."""
    from scripts.verify_phase1 import run_suite

    evidence = tmp_path / "inv"
    evidence.mkdir()
    (evidence / "live.xml").write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.e2e.test_sani_voice" name="test_live_voice_round_trip"/>'
        "</testsuite></testsuites>"
    )
    # Simulate a fresh passing report by running a trivially successful
    # "pytest" child that re-writes the same report path.
    report = evidence / "live.xml"
    command = [
        sys.executable,
        "-c",
        f"open({str(report)!r}, 'w').write("
        "'<testsuites><testsuite tests=\"1\" failures=\"0\" errors=\"0\" skipped=\"0\">"
        "<testcase classname=\"t\" name=\"test_live_voice_round_trip\"/>"
        "</testsuite></testsuites>'); raise SystemExit(0)",
        "-m",
        "pytest",
    ]
    gate = run_suite(
        "live",
        command,
        environment_kind="L",
        evidence_dir=evidence,
        env={"PATH": "/usr/bin:/bin"},
        expected_cases=("test_live_voice_round_trip", "test_live_speech_stop_and_stt_coexistence"),
    )
    assert gate["status"] == "BLOCKED", gate["observed"]
    assert "test_live_speech_stop_and_stt_coexistence" in gate["observed"]


def test_integration_gate_includes_composition_suite() -> None:
    """N12: the 8 composition cases must be part of the normal gate."""
    from scripts.verify_phase1 import SUITE_FILES

    integration = SUITE_FILES["integration"][1]
    assert "tests/integration/test_mission_composition.py" in integration


def test_live_suites_declare_expected_case_inventory() -> None:
    from scripts.verify_phase1 import EXPECTED_LIVE_CASES, LIVE_SUITES

    for suite in LIVE_SUITES:
        assert EXPECTED_LIVE_CASES.get(suite), f"{suite} declares no expected cases"
