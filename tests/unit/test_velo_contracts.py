"""Velo contracts: no-progress tracking, honest outcomes, target identity."""

from __future__ import annotations

import pytest

from assistant.velo.contracts import (
    ActionOutcome,
    AppIdentity,
    NoProgressTracker,
    OutcomeState,
    PostconditionKind,
    ProgressKind,
    Route,
    TaskState,
)


def test_only_confirmed_outcomes_support_completion_claims() -> None:
    assert OutcomeState.CONFIRMED.supports_completion
    for state in (
        OutcomeState.ACCEPTED,
        OutcomeState.DISPATCHED,
        OutcomeState.NO_EFFECT,
        OutcomeState.UNKNOWN,
        OutcomeState.FAILED,
        OutcomeState.CANCELLED,
    ):
        assert not state.supports_completion, f"{state} must not claim completion"


def test_an_action_accepted_by_the_tool_is_not_the_task_succeeding() -> None:
    outcome = ActionOutcome(tool="click", state=OutcomeState.DISPATCHED)
    assert not outcome.state.supports_completion


def test_identity_matching_spans_name_bundle_and_word_forms() -> None:
    safari = AppIdentity(name="Safari", bundle_id="com.apple.Safari")
    assert safari.identity_matches("safari")
    assert safari.identity_matches("Safari")
    assert safari.identity_matches("com.apple.Safari")
    assert safari.identity_matches("com.apple.safari")
    assert not safari.identity_matches("chrome")


def test_identity_matching_tolerates_display_and_bundle_shapes() -> None:
    chrome = AppIdentity(name="Google Chrome", bundle_id="com.google.Chrome")
    assert chrome.identity_matches("chrome")
    assert chrome.identity_matches("google chrome")
    assert chrome.identity_matches("Chrome.app")


def test_no_progress_tracker_stops_after_repeated_unchanged_observations() -> None:
    tracker = NoProgressTracker(max_steps_without_change=3)
    assert tracker.register(ProgressKind.OBSERVATION, "digest-a")  # baseline
    assert tracker.register(ProgressKind.WAIT, "digest-a")
    assert tracker.register(ProgressKind.OBSERVATION, "digest-a")
    assert not tracker.register(ProgressKind.WAIT, "digest-a"), "third repeat must stop"
    assert tracker.exhausted


def test_no_progress_counts_across_alternating_kinds_not_just_mutations() -> None:
    """The historical loop's OBSERVE/WAIT branches dodged the mutation check."""
    tracker = NoProgressTracker(max_steps_without_change=4)
    sequence = [
        (ProgressKind.ACTION, "same"),  # baseline
        (ProgressKind.OBSERVATION, "same"),
        (ProgressKind.WAIT, "same"),
        (ProgressKind.OBSERVATION, "same"),
        (ProgressKind.RECOVERY, "same"),
    ]
    results = [tracker.register(kind, digest) for kind, digest in sequence]
    assert results == [True, True, True, True, False]


def test_no_progress_alternating_distinct_digests_is_not_automatically_progress() -> None:
    """A,B,A,B must trip the ceiling: alternation is not progress (A09 fix).

    Oracle change, with counterexample: the previous version of this test
    documented the old last-digest-only tracker under which A,B,A,B ran
    forever -- the alternating-loop defect (audit A09, RF-12) proven by
    ``tests/unit/test_phase1_oracles.py::test_alternating_observations_cannot_loop_forever``.
    The windowed tracker now counts any digest seen within the look-back
    window as no progress, while real change (an unseen digest) still
    resets the count.
    """
    tracker = NoProgressTracker(max_steps_without_change=4)
    digests = ["a", "b"]
    results = [tracker.register(ProgressKind.OBSERVATION, digests[i % 2]) for i in range(8)]
    assert not results[5], "the 6th register (4 repeats) must trip the breaker"
    # The recovery cap still bounds blind recovery churn independently.
    tracker2 = NoProgressTracker(max_recovery_attempts=2)
    assert tracker2.register(ProgressKind.RECOVERY, "a")
    assert tracker2.register(ProgressKind.RECOVERY, "b")
    assert not tracker2.register(ProgressKind.RECOVERY, "c")


def test_real_change_resets_the_no_progress_count() -> None:
    tracker = NoProgressTracker(max_steps_without_change=3)
    assert tracker.register(ProgressKind.ACTION, "one")
    assert tracker.register(ProgressKind.OBSERVATION, "one")
    assert tracker.register(ProgressKind.ACTION, "one")
    assert tracker.register(ProgressKind.ACTION, "two"), "a changed digest resets the count"


def test_a_missing_digest_is_neither_progress_nor_a_crash() -> None:
    tracker = NoProgressTracker(max_steps_without_change=1)
    assert not tracker.register(ProgressKind.WAIT, "")


def test_task_state_enforces_budget_and_deadline() -> None:
    import time

    task = TaskState(instruction="open Safari", max_actions=2)
    task.check_usable()
    task.used_actions = 2
    with pytest.raises(Exception, match="budget"):
        task.check_usable()

    task2 = TaskState(instruction="x", deadline=time.monotonic() - 1)
    with pytest.raises(Exception, match="time"):
        task2.check_usable()


def test_task_invalidation_bumps_the_version() -> None:
    task = TaskState(instruction="x")
    before = task.version
    task.invalidate()
    assert task.version == before + 1


def test_routes_are_the_three_documented_paths() -> None:
    assert {route.value for route in Route} == {"local", "jev", "plan"}


def test_postcondition_kinds_cover_the_documented_objectives() -> None:
    expected = {
        "app_running",
        "app_foreground",
        "navigated",
        "searched",
        "text_in_field",
        "playback_started",
        "viewport_changed",
    }
    assert {kind.value for kind in PostconditionKind} == expected
