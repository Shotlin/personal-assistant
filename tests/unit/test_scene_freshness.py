"""WP7: scene freshness contract (master plan regression behaviors).

- A navigation change invalidates prior element tokens / URL evidence:
  read_state stamps each observation with a monotonically increasing
  navigation epoch; evidence from an older epoch is stale.
- An unchanged unrelated screenshot does not prove current input focus:
  evidence carries the observed foreground app and a freshness instant;
  matching requires BOTH the app identity AND a fresh stamp.
- The scene entry shape follows the master plan: (app_instance,
  window_id, navigation_epoch, observed_at, semantic_fields,
  evidence_refs).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from assistant.runtime.scene import SceneEntry, is_fresh


def _entry(**overrides: object) -> SceneEntry:
    base: dict[str, object] = {
        "app_instance": "chrome",
        "window_id": 1,
        "navigation_epoch": 3,
        "observed_at": datetime.now(tz=UTC),
        "semantic_fields": {"foreground_app": "chrome"},
        "evidence_refs": ("get_desktop_state",),
    }
    base.update(overrides)
    return SceneEntry(**base)  # type: ignore[arg-type]


def test_epoch_bump_invalidates_cached_tokens() -> None:
    old = _entry(navigation_epoch=3, url_evidence="https://example.com/a")
    newer = _entry(navigation_epoch=4)
    assert newer.navigation_epoch > old.navigation_epoch
    assert not is_fresh(old, current_epoch=newer.navigation_epoch)


def test_same_epoch_stale_by_age() -> None:
    stale = _entry(observed_at=datetime.now(tz=UTC) - timedelta(seconds=10))
    assert not is_fresh(stale, current_epoch=stale.navigation_epoch, max_age_s=5.0)


def test_fresh_entry_matches_identity_and_epoch() -> None:
    entry = _entry()
    assert is_fresh(entry, current_epoch=entry.navigation_epoch, max_age_s=5.0)


def test_epoch_must_be_monotonic() -> None:
    entry = _entry()
    with pytest.raises(ValueError, match="monotonic"):
        SceneEntry.next_epoch(entry, candidate=entry.navigation_epoch)


def test_failed_recipe_cannot_reuse_completed_submission_evidence() -> None:
    # Regression: a failed recipe must not re-execute a completed
    # submission. Submission evidence is bound to its epoch; after any
    # epoch bump the old evidence cannot validate a retry.
    completed = _entry(semantic_fields={"submitted": True})
    assert not is_fresh(completed, current_epoch=completed.navigation_epoch + 1)
