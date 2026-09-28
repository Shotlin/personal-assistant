"""Evidence pipeline tests (T03, file 06 U2): sanitize before any sink.

Canaries prove the negative: after a denial or a withheld capture, no raw
secret-shaped content exists on disk, in the database, or in any returned
payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assistant.missions.contracts import (
    CheckSpec,
    EvidenceCandidate,
    Scope,
    new_id,
)
from assistant.missions.evidence import (
    EvidenceStore,
    looks_secret_shaped,
    sanitize_text,
)
from assistant.missions.store import MissionStore
from tests.helpers.mission_fakes import SECRET_CANARIES


@pytest.fixture()
async def store(tmp_path: Path) -> Any:
    s = await MissionStore.connect(tmp_path / "evidence.db")
    await s.setup()
    yield s
    await s.close()


@pytest.fixture()
def evidence_root(tmp_path: Path) -> Path:
    return tmp_path / "evidence-root"


def _candidate(**overrides: Any) -> EvidenceCandidate:
    fields: dict[str, Any] = {
        "kind": "structured_facts",
        "payload": {"marker": "fixture-content"},
        "captured_at_ms": 1769472000000,
    }
    fields.update(overrides)
    return EvidenceCandidate(**fields)


async def test_safe_payload_stored_and_hashed(store: MissionStore, evidence_root: Path) -> None:
    evidence = EvidenceStore(evidence_root, store)
    ref = await evidence.put(
        _candidate(), mission_id=new_id(), execution_id=None
    )
    assert ref.redaction_status == "SAFE"
    loaded = evidence.load(ref)
    assert loaded is not None and loaded["payload"]["marker"] == "fixture-content"
    stored = await store.get_evidence(ref.mission_id)
    assert [r.evidence_id for r in stored] == [ref.evidence_id]


async def test_secret_payload_withheld(store: MissionStore, evidence_root: Path) -> None:
    evidence = EvidenceStore(evidence_root, store)
    secret_payload = {
        "page_text": f"the key is {SECRET_CANARIES[0]} keep it safe",
    }
    ref = await evidence.put(
        _candidate(payload=secret_payload), mission_id=new_id()
    )
    assert ref.redaction_status == "WITHHELD"
    assert ref.relative_path is None and ref.inline_facts is None
    # Nothing reached the disk: the root stays empty.
    assert not any(evidence_root.rglob("*.json")) if evidence_root.exists() else True


async def test_images_withheld_by_default(store: MissionStore, evidence_root: Path) -> None:
    evidence = EvidenceStore(evidence_root, store)
    ref = await evidence.put(
        _candidate(kind="screenshot", payload={"png": "<raw-bytes-not-really>"}),
        mission_id=new_id(),
    )
    assert ref.redaction_status == "WITHHELD"


async def test_disk_write_failure_degrades_to_withheld(
    store: MissionStore, tmp_path: Path
) -> None:
    """A failing evidence root must not crash the mission or leak content."""
    blocked_file = tmp_path / "blocked-root-file"
    blocked_file.write_text("not a directory")
    evidence = EvidenceStore(blocked_file, store)
    ref = await evidence.put(_candidate(), mission_id=new_id())
    # The write failed: the ref degrades to withheld (never crashes, never
    # claims stored content).
    assert ref.redaction_status == "WITHHELD"
    assert evidence.load(ref) is None


def test_artifact_verifier_rejects_traversal(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path / "root")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret artifact")
    import asyncio

    async def run() -> bool:
        check = CheckSpec(
            check_id="c1",
            verifier_id="artifact_readable",
            verifier_version="1.0.0",
            expected={
                "path": str(tmp_path / "root" / ".." / "outside.txt"),
                "allowed_roots": [str(tmp_path / "root")],
            },
        )
        result = await evidence.verify(check, item=None)
        return result.passed

    assert asyncio.run(run()) is False


def test_artifact_verifier_rejects_symlink(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path / "root")
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "real.txt"
    target.write_text("data")
    (root / "link.txt").symlink_to(target)

    import asyncio

    async def run() -> bool:
        check = CheckSpec(
            check_id="c1",
            verifier_id="artifact_readable",
            verifier_version="1.0.0",
            expected={"path": str(root / "link.txt"), "allowed_roots": [str(root)]},
        )
        result = await evidence.verify(check, item=None)
        return result.passed

    assert asyncio.run(run()) is False


def test_artifact_verifier_accepts_real_file_with_hash(tmp_path: Path) -> None:
    import hashlib

    evidence = EvidenceStore(tmp_path / "root")
    root = tmp_path / "root"
    root.mkdir()
    artifact = root / "out.txt"
    artifact.write_text("fixture output")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    import asyncio

    async def run() -> tuple[bool, bool]:
        good = CheckSpec(
            check_id="c1",
            verifier_id="artifact_readable",
            verifier_version="1.0.0",
            expected={"path": str(artifact), "sha256": digest, "types": [".txt"]},
        )
        corrupt = CheckSpec(
            check_id="c2",
            verifier_id="artifact_readable",
            verifier_version="1.0.0",
            expected={"path": str(artifact), "sha256": "0" * 64},
        )
        empty = tmp_path / "root" / "empty.txt"
        empty.write_text("")
        empty_check = CheckSpec(
            check_id="c3",
            verifier_id="artifact_readable",
            verifier_version="1.0.0",
            expected={"path": str(empty)},
        )
        return (
            (await evidence.verify(good, item=None)).passed,
            (await evidence.verify(corrupt, item=None)).passed
            or (await evidence.verify(empty_check, item=None)).passed,
        )

    good, bad = asyncio.run(run())
    assert good is True
    assert bad is False, "corrupt or empty artifacts must fail acceptance (TC-24)"


def test_unknown_verifier_fails_closed() -> None:
    import asyncio

    evidence = EvidenceStore("/tmp/fixture-evidence-root")

    async def run() -> CheckSpec:
        check = CheckSpec(
            check_id="c",
            verifier_id="model_self_assessment",
            verifier_version="9",
            expected={},
        )
        result = await evidence.verify(check, item=None)
        assert result.passed is False
        return check

    asyncio.run(run())


def test_page_state_verifier_and_freshness(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path / "root")

    from assistant.missions.contracts import EvidenceRef

    ref = EvidenceRef(
        evidence_id=new_id(),
        mission_id=new_id(),
        kind="structured_facts",
        inline_facts=None,
        relative_path="unused",
        sha256="a" * 64,
        captured_at_ms=1,
    )
    # A stale ref is rejected before any marker check.
    import asyncio

    async def run() -> tuple[bool, str]:
        check = CheckSpec(
            check_id="c",
            verifier_id="page_state",
            verifier_version="1.0.0",
            expected={"markers": ["fixture"]},
        )
        result = await evidence.verify(check, item=None, refs=[ref])
        return result.passed, result.reason

    passed, reason = asyncio.run(run())
    assert passed is False
    assert "stale" in reason.lower()


def test_sanitize_and_secret_shape_helpers() -> None:
    assert sanitize_text("api_key=abc123") != "api_key=abc123"
    assert looks_secret_shaped("sk-abcdef1234567890")
    assert not looks_secret_shaped("just a sentence about keys")


def test_scope_defaults_refuse_unknown_identity() -> None:
    scope = Scope(owner_id="owner")
    assert scope.account_ref is None
    # An empty scope permits no effect at all (file 03 §4).
    assert not scope.permits_effect("READ_ONLY")
    assert not scope.permits_effect("EXTERNAL_WRITE")


def test_evidence_json_shape_roundtrip(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path / "root")

    import asyncio

    async def run() -> dict[str, Any] | None:
        ref = await evidence.put(_candidate(), mission_id=new_id())
        return evidence.load(ref)

    loaded = asyncio.run(run())
    assert loaded is not None
    assert json.loads(json.dumps(loaded))["kind"] == "structured_facts"


# -- C07/N06: retention sweep, holds, tombstones, orphans ---------------------


async def test_retention_deletes_expired_evidence_with_tombstone(
    store: MissionStore, tmp_path: Any
) -> None:
    """C07: expired evidence is deleted, its file removed, and a tombstone
    event lands on the mission trace."""
    import time as time_module

    from assistant.missions.contracts import (
        BudgetLimits,
        RequestEnvelope,
        Scope,
        StepSpec,
        new_id,
    )
    from assistant.missions.evidence import EvidenceStore

    evidence_root = tmp_path / "ev"
    evidence = EvidenceStore(evidence_root, store)
    request = RequestEnvelope(
        request_id=new_id(), conversation_id="c", owner_id="owner",
        input_origin="typed_final", input_revision=1, text="fixture",
        submitted_at_ms=int(time_module.time() * 1000),
    )
    mission = await store.claim_request(
        request, "d" * 64, goal="fixture", scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(
        mission.mission_id, 0,
        [StepSpec(step_id="s1", ordinal=1, objective="fixture", recipe_id="open_app",
                  scope=Scope(owner_id="owner"), budget=BudgetLimits())],
        [],
    )
    from assistant.missions.contracts import EvidenceCandidate

    now = int(time_module.time() * 1000)
    ref = await evidence.put(
        EvidenceCandidate(kind="structured_facts", payload={"k": "v"},
                          captured_at_ms=now),
        mission_id=mission.mission_id,
    )
    # Force the ref expired.
    store._conn.execute(
        "UPDATE mission_evidence SET expires_at_ms=? WHERE evidence_id=?",
        (now - 1, ref.evidence_id),
    )
    result = await store.enforce_retention(now + 1)
    assert result["deleted_evidence"] == 1
    assert evidence.sweep_deleted_files(result["deleted_paths"]) == 1
    assert not (evidence_root / ref.relative_path).exists()
    events = await store.get_events(mission.mission_id)
    tombstones = [e for e in events if e.kind == "retention"]
    assert tombstones and tombstones[-1].safe_payload.get("tombstone") is True
    assert await store.verify_chain(mission.mission_id)


async def test_retention_preserves_investigation_holds(
    store: MissionStore, tmp_path: Any
) -> None:
    """C07: evidence on a held mission survives the sweep untouched."""
    import time as time_module

    from assistant.missions.contracts import (
        BudgetLimits,
        EvidenceCandidate,
        RequestEnvelope,
        Scope,
        new_id,
    )
    from assistant.missions.evidence import EvidenceStore

    evidence = EvidenceStore(tmp_path / "ev", store)
    request = RequestEnvelope(
        request_id=new_id(), conversation_id="c", owner_id="owner",
        input_origin="typed_final", input_revision=1, text="fixture",
        submitted_at_ms=int(time_module.time() * 1000),
    )
    mission = await store.claim_request(
        request, "d" * 64, goal="fixture", scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    now = int(time_module.time() * 1000)
    ref = await evidence.put(
        EvidenceCandidate(kind="structured_facts", payload={"k": "v"}, captured_at_ms=now),
        mission_id=mission.mission_id,
    )
    store._conn.execute(
        "UPDATE mission_evidence SET expires_at_ms=? WHERE evidence_id=?",
        (now - 1, ref.evidence_id),
    )
    result = await store.enforce_retention(now + 1, hold_missions={mission.mission_id})
    assert result["deleted_evidence"] == 0, "held evidence is preserved"
    row = store._conn.execute(
        "SELECT deleted FROM mission_evidence WHERE evidence_id=?", (ref.evidence_id,)
    ).fetchone()
    assert int(row[0]) == 0


async def test_retention_removes_orphan_rows_and_files(
    store: MissionStore, tmp_path: Any
) -> None:
    """C07: evidence rows whose mission vanished are orphans — deleted with
    a retention-log record — and unreferenced files are removed."""
    import time as time_module

    from assistant.missions.evidence import EvidenceStore

    evidence_root = tmp_path / "ev"
    evidence = EvidenceStore(evidence_root, store)
    now = int(time_module.time() * 1000)
    orphan_file = evidence_root / "ghost-mission" / "ghost.json"
    orphan_file.parent.mkdir(parents=True)
    orphan_file.write_text('{"kind": "structured_facts"}', encoding="utf-8")
    await evidence.put(
        __import__(
            "assistant.missions.contracts", fromlist=["EvidenceCandidate"]
        ).EvidenceCandidate(kind="structured_facts", payload={"k": "v"}, captured_at_ms=now),
        mission_id="nonexistent-mission",
    )
    # put_evidence succeeded even though the mission row is absent (no FK on
    # this insert path), so the row itself is an orphan.
    result = await store.enforce_retention(now + 1)
    assert result["orphan_evidence"] >= 1
    assert evidence.sweep_deleted_files(result["deleted_paths"]) >= 0
    removed = evidence.remove_orphan_files(set(await evidence.live_relative_paths()))
    assert removed >= 1, "the unreferenced ghost file must go"
    logged = store._conn.execute(
        "SELECT kind FROM mission_retention_log"
    ).fetchall()
    assert any(str(r[0]) == "orphan_evidence" for r in logged)
