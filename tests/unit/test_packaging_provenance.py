"""Packaging provenance checks (T12, fixture mode — no live resources).

Source/bundle manifest binding, provenance inputs, and default flags are
validated offline; the live install/rollback flow is K1-gated.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_release_manifest_inputs_exist() -> None:
    """Provenance inputs must exist: tauri config, release scripts, notices."""
    assert (REPO_ROOT / "sani" / "src-tauri" / "tauri.conf.json").is_file()
    assert (REPO_ROOT / "sani" / "scripts" / "release-mac.sh").is_file()
    assert (REPO_ROOT / "THIRD_PARTY_NOTICES.md").is_file()


def test_mission_tts_modules_covered_by_source_manifest() -> None:
    """The source manifest must include untracked mission/TTS modules."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from verify_phase1 import source_manifest

    manifest = source_manifest()
    for required in (
        "src/assistant/missions/service.py",
        "src/assistant/missions/store.py",
        "sani/src-tauri/python/sani_tts.py",
        "sani/src-tauri/src/missions.rs",
        "scripts/verify_phase1.py",
    ):
        assert required in manifest, f"untracked implementation missing from manifest: {required}"


def test_feature_flags_default_off_in_source() -> None:
    """Default flags must be off; acceptance never silently enables features."""
    settings_src = (REPO_ROOT / "src" / "assistant" / "settings.py").read_text()
    assert "jarvis_missions_enabled: bool = False" in settings_src
    assert "sani_tts_enabled: bool = False" in settings_src
