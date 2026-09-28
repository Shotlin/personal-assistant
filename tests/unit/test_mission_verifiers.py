"""Trusted verifier tests (T04, file 06 U3, RF-03).

Verification is independent of the worker's language: a check passes only
when fresh, post-action evidence satisfies the registered verifier. Text
that merely existed before the action proves nothing.
"""

from __future__ import annotations

import time
from typing import Any

from assistant.missions.contracts import CheckSpec, EvidenceCandidate, new_id
from assistant.missions.evidence import EvidenceStore


def _now() -> int:
    return int(time.time() * 1000)


def _check(marker: str, *, required: bool = True) -> CheckSpec:
    return CheckSpec(
        check_id="destination-loaded",
        verifier_id="page_state",
        verifier_version="1.0.0",
        expected={"markers": [marker]},
        required=required,
    )


async def test_pre_action_text_is_not_verification(tmp_path: Any) -> None:
    """RF-03: the query already sitting in the field before the search
    action cannot prove the search ran. Stale (pre-action) evidence
    fails the freshness gate even when the marker matches."""
    evidence = EvidenceStore(tmp_path / "ev")
    mission_id = new_id()
    started = _now()
    stale = await evidence.put(
        EvidenceCandidate(
            kind="structured_facts",
            payload={"elements": [{"label": "fixture-query", "role": "AXTextField"}]},
            captured_at_ms=started - 600_000,
        ),
        mission_id=mission_id,
    )
    result = await evidence.verify(_check("fixture-query"), item=None, refs=[stale])
    assert result.passed is False, "pre-action evidence must not verify an action"


async def test_fresh_matching_evidence_verifies(tmp_path: Any) -> None:
    evidence = EvidenceStore(tmp_path / "ev")
    fresh = await evidence.put(
        EvidenceCandidate(
            kind="structured_facts",
            payload={"elements": [{"label": "results for fixture-query", "role": "AXStaticText"}]},
            captured_at_ms=_now(),
        ),
        mission_id=new_id(),
    )
    result = await evidence.verify(_check("fixture-query"), item=None, refs=[fresh])
    assert result.passed is True


async def test_unrelated_pause_label_is_not_playback_proof(tmp_path: Any) -> None:
    """RF-03: a Pause label on an unrelated element is not playback evidence
    when the check demands a playback marker tied to the media element."""
    evidence = EvidenceStore(tmp_path / "ev")
    ref = await evidence.put(
        EvidenceCandidate(
            kind="structured_facts",
            payload={"elements": [
                {"label": "Pause autoplay settings", "role": "AXCheckBox"},
            ]},
            captured_at_ms=_now(),
        ),
        mission_id=new_id(),
    )
    result = await evidence.verify(_check("video-player-playback-pause"), item=None, refs=[ref])
    assert result.passed is False


async def test_stale_title_or_distractor_text_does_not_verify(tmp_path: Any) -> None:
    evidence = EvidenceStore(tmp_path / "ev")
    ref = await evidence.put(
        EvidenceCandidate(
            kind="structured_facts",
            payload={"elements": [
                {"label": "old fixture-search title", "role": "AXStaticText"},
                {"label": "totally unrelated distractor", "role": "AXButton"},
            ]},
            captured_at_ms=_now(),
        ),
        mission_id=new_id(),
    )
    result = await evidence.verify(_check("fixture-search-results-loaded"), item=None, refs=[ref])
    assert result.passed is False


async def test_verification_uses_verifier_identity_not_prose(tmp_path: Any) -> None:
    """The verifier identity is structural; the evidence's own wording cannot
    substitute a different verifier."""
    evidence = EvidenceStore(tmp_path / "ev")
    fresh = await evidence.put(
        EvidenceCandidate(
            kind="structured_facts",
            payload={"elements": [{"label": "destination loaded", "role": "AXStaticText"}]},
            captured_at_ms=_now(),
        ),
        mission_id=new_id(),
    )
    forged = CheckSpec(
        check_id="c",
        verifier_id="page_state",
        verifier_version="1.0.0",
        expected={"markers": ["destination loaded"]},
    )
    ok = await evidence.verify(forged, item=None, refs=[fresh])
    assert ok.passed is True
    assert ok.verifier_id == "page_state"
    assert ok.verifier_version == "1.0.0"


async def test_url_origin_requires_exact_match(tmp_path: Any) -> None:
    evidence = EvidenceStore(tmp_path / "ev")
    ref = await evidence.put(
        EvidenceCandidate(
            kind="structured_facts",
            payload={"origin": "https://fixture.local/page"},
            captured_at_ms=_now(),
        ),
        mission_id=new_id(),
    )
    exact = CheckSpec(
        check_id="c",
        verifier_id="url_origin",
        verifier_version="1.0.0",
        expected={"origin": "https://fixture.local/page"},
    )
    wrong = CheckSpec(
        check_id="c2",
        verifier_id="url_origin",
        verifier_version="1.0.0",
        expected={"origin": "https://evil.local/page"},
    )
    assert (await evidence.verify(exact, item=None, refs=[ref])).passed is True
    assert (await evidence.verify(wrong, item=None, refs=[ref])).passed is False


async def test_action_absent_verifier(tmp_path: Any) -> None:
    evidence = EvidenceStore(tmp_path / "ev")
    ref = await evidence.put(
        EvidenceCandidate(
            kind="structured_facts",
            payload={"performed_digests": ["abc" * 11]},
            captured_at_ms=_now(),
        ),
        mission_id=new_id(),
    )
    present = CheckSpec(
        check_id="c",
        verifier_id="action_absent",
        verifier_version="1.0.0",
        expected={"action_digest": "abc" * 11},
    )
    absent = CheckSpec(
        check_id="c2",
        verifier_id="action_absent",
        verifier_version="1.0.0",
        expected={"action_digest": "fff" * 11},
    )
    assert (await evidence.verify(present, item=None, refs=[ref])).passed is False
    assert (await evidence.verify(absent, item=None, refs=[ref])).passed is True
