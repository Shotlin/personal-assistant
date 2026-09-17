"""WP7 scene freshness: effect-aware local verification state.

One policy for all recipe observations (replaces the conflicting
observation rules): every observation is stamped with a monotonically
increasing navigation epoch and an observation instant, and matching
requires app identity AND a current epoch AND a fresh stamp. Stale or
epoch-invalidated evidence can never validate a (re)submission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

DEFAULT_MAX_AGE_S = 5.0


@dataclass(frozen=True)
class SceneEntry:
    """Master-plan scene entry shape (WP7)."""

    app_instance: str
    window_id: int
    navigation_epoch: int
    observed_at: datetime
    semantic_fields: dict[str, Any]
    evidence_refs: tuple[str, ...]
    url_evidence: str | None = None
    history: tuple[str, ...] = field(default=())

    @staticmethod
    def next_epoch(previous: SceneEntry, candidate: int) -> int:
        """Epochs only move forward; equality is a stale replay, not progress."""
        if candidate <= previous.navigation_epoch:
            raise ValueError("navigation epoch must be monotonic")
        return candidate


def is_fresh(
    entry: SceneEntry,
    *,
    current_epoch: int,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    now: datetime | None = None,
) -> bool:
    """Fresh means: current epoch AND observed within the age bound.

    An unchanged unrelated screenshot proves nothing about input focus:
    identity AND freshness must both hold for evidence to validate an
    effect.
    """
    if entry.navigation_epoch != current_epoch:
        return False
    observed = entry.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    age = (now or datetime.now(tz=UTC)) - observed
    return abs(age.total_seconds()) <= max_age_s


def observation_is_fresh(
    outcome: object,
    *,
    current_epoch: int | None = None,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    now: datetime | None = None,
) -> bool:
    """Freshness policy for a normalized ToolOutcome observation.

    Reads the ``observed_at``/``navigation_epoch`` stamps the executor
    attaches to observations. Unstamped evidence (a driver that provides
    neither field) is NOT treated as fresh: an unverifiable timestamp
    must not validate an effect.
    """
    structured = getattr(outcome, "structured", None)
    if not isinstance(structured, dict):
        return False
    observed_at = structured.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at:
        return False
    try:
        observed = datetime.fromisoformat(observed_at)
    except ValueError:
        return False
    epoch = structured.get("navigation_epoch")
    if current_epoch is not None:
        if not isinstance(epoch, int) or epoch != current_epoch:
            return False
    elif not isinstance(epoch, int):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    age = ((now or datetime.now(tz=UTC)) - observed).total_seconds()
    return abs(age) <= max_age_s
