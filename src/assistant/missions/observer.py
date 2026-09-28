"""The Phase 1 Observer: deterministic, read-only, zero authority (file 03 §10).

The Observer consumes a read-only event export and writes recommendations to
a separate append-only store. It has:

- no tools, no model calls, no write access to missions, evidence, policy,
  or execution thresholds;
- no experiment plane: the experiment budget is exactly zero and the
  counter cannot go above zero because no runner exists;
- deterministic rules only — repeated failures, no-progress, single
  high-impact failures, unsupported completions;
- hostile-payload tolerance: recommendations carry redacted, bounded text
  and structurally cannot request activation (the contract rejects it).

Trace-chain honesty: the hash chain detects modification but same-user
rewriting of the whole chain is OUT OF THREAT-MODEL PROTECTION and this is
disclosed in the schema doc — never advertised as immutability.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from assistant.missions.contracts import ObserverRecommendation, TraceEvent, new_id
from assistant.observability.logging import redact

logger = logging.getLogger("assistant.missions.observer")

#: Deterministic pattern ids. "single:" marks explicitly single-instance
#: recommendations (a single high-impact failure may generate one).
PATTERN_REPEATED_FAILURE = "repeated_failure"
PATTERN_SINGLE_HIGH_IMPACT = "single:high_impact_failure"
PATTERN_NO_PROGRESS = "repeated_no_progress"
PATTERN_UNSUPPORTED_COMPLETION = "unsupported_completion"


class RecommendationStore:
    """Append-only JSONL sink, separate from the mission event chain."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._root / "recommendations.jsonl"

    def append(self, recommendation: ObserverRecommendation) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                recommendation.model_dump_json(exclude={"safe_payload": True}, exclude_none=True)
                + "\n"
            )

    def prune_expired(self, now_ms: int, retention_ms: int) -> list[str]:
        """C07: delete observer copies older than the retention bound.

        The recommendations file is rewritten without expired records and a
        tombstone line lands in tombstones.jsonl naming what was removed —
        user deletion covers observer copies (file 03 §10).
        """
        if not self.path.exists():
            return []
        cutoff = now_ms - retention_ms
        kept: list[str] = []
        removed_ids: list[str] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = ObserverRecommendation.model_validate_json(line)
            except Exception:  # noqa: BLE001 -- an unreadable record is removed too
                removed_ids.append("unreadable")
                continue
            created = int(getattr(record, "created_at_ms", 0) or 0)
            if created and created >= cutoff:
                kept.append(line)
            else:
                removed_ids.append(str(record.recommendation_id))
        if removed_ids:
            self.path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            with (self._root / "tombstones.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"tombstone": True, "at_ms": now_ms,
                         "removed_recommendations": removed_ids[:64]}
                    )
                    + "\n"
                )
        return removed_ids

    def read_all(self) -> list[ObserverRecommendation]:
        if not self.path.exists():
            return []
        items: list[ObserverRecommendation] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            items.append(ObserverRecommendation.model_validate_json(line))
        return items


class Observer:
    """Analyzes trace events; changes nothing in production."""

    def __init__(self, sink: RecommendationStore | None = None) -> None:
        self._sink = sink

    async def analyze(self, events: list[TraceEvent]) -> list[ObserverRecommendation]:
        """Run every deterministic rule over one mission's ordered events."""
        recommendations: list[ObserverRecommendation] = []
        failures = self._supported_failures(events)
        by_category = Counter(f["category"] for f in failures)
        for category, count in sorted(by_category.items()):
            if count >= 2:
                supporting = [f["event_id"] for f in failures if f["category"] == category]
                recommendations.append(
                    self._recommendation(
                        PATTERN_REPEATED_FAILURE,
                        events,
                        mission_ids=sorted({f["mission_id"] for f in failures
                                            if f["category"] == category}),
                        supporting=supporting,
                        hypothesis=f"{count} comparable {category} failures share a pattern",
                        confidence=min(0.9, 0.4 + 0.1 * count),
                    )
                )
            elif category in {"VERIFICATION_FAILED", "UNKNOWN_EFFECT"}:
                supporting = [f["event_id"] for f in failures if f["category"] == category]
                recommendations.append(
                    self._recommendation(
                        PATTERN_SINGLE_HIGH_IMPACT,
                        events,
                        mission_ids=sorted({f["mission_id"] for f in failures
                                            if f["category"] == category}),
                        supporting=supporting,
                        hypothesis=(
                            f"a single {category} failure blocked a mission; "
                            "explicitly single-instance"
                        ),
                        confidence=0.5,
                    )
                )
        stalls = self._supported_stalls(events)
        if stalls >= 2:
            recommendations.append(
                self._recommendation(
                    PATTERN_NO_PROGRESS,
                    events,
                    mission_ids=sorted({e.mission_id for e in events}),
                    supporting=[e.event_id for e in events
                                if e.kind == "recovery" and e.safe_payload.get("stale_result")],
                    hypothesis=f"{stalls} no-progress stall events observed",
                    confidence=0.6,
                )
            )
        unsupported = self._unsupported_completions(events)
        if unsupported:
            recommendations.append(
                self._recommendation(
                    PATTERN_UNSUPPORTED_COMPLETION,
                    events,
                    mission_ids=sorted({e.mission_id for e in events}),
                    supporting=[e.event_id for e in unsupported],
                    hypothesis=(
                        "an outcome claimed completion while a required check "
                        "was missing or failed"
                    ),
                    confidence=0.8,
                )
            )
        for recommendation in recommendations:
            if self._sink is not None:
                self._sink.append(recommendation)
        return recommendations

    # -- rules ------------------------------------------------------------------

    def _supported_failures(self, events: list[TraceEvent]) -> list[dict[str, Any]]:
        """Outcome events that failed, with their category and evidence."""
        supported: list[dict[str, Any]] = []
        for event in events:
            if event.kind != "outcome":
                continue
            category = event.safe_payload.get("failure_category")
            status = event.safe_payload.get("status")
            if status in {"FAILED", "BLOCKED"} and category:
                supported.append(
                    {
                        "event_id": event.event_id,
                        "mission_id": event.mission_id,
                        "category": category,
                    }
                )
        return supported

    def _supported_stalls(self, events: list[TraceEvent]) -> int:
        stalls = 0
        for event in events:
            if event.kind == "recovery" and event.safe_payload.get("stale_result"):
                stalls += 1
            if (
                event.kind == "recovery"
                and event.safe_payload.get("reason") == "restart_reconciliation"
            ):
                stalls += 1
        return stalls

    def _unsupported_completions(self, events: list[TraceEvent]) -> list[TraceEvent]:
        """Outcome COMPLETED where the payload's own checks did not all pass."""
        unsupported: list[TraceEvent] = []
        for event in events:
            if event.kind != "outcome":
                continue
            if event.safe_payload.get("status") != "COMPLETED":
                continue
            checks = event.safe_payload.get("checks")
            if isinstance(checks, list) and any(not c.get("passed") for c in checks if isinstance(c,
                 dict)):
                unsupported.append(event)
        return unsupported

    # -- output -----------------------------------------------------------------

    def _recommendation(
        self,
        pattern_id: str,
        events: list[TraceEvent],
        *,
        mission_ids: list[str],
        supporting: list[str],
        hypothesis: str,
        confidence: float,
    ) -> ObserverRecommendation:
        # Redact hostile text before it can reach the store; the contract
        # additionally rejects authority-requesting content outright.
        safe_hypothesis = redact(hypothesis[:4000])
        return ObserverRecommendation(
            recommendation_id=new_id(),
            pattern_id=pattern_id,
            mission_ids=mission_ids[:10],
            supporting_event_ids=supporting[:10],
            hypothesis=safe_hypothesis,
            confidence=confidence,
            scope="observation_only",
            created_at_ms=events[-1].occurred_at_ms if events else 0,
        )


__all__ = [
    "Observer",
    "PATTERN_NO_PROGRESS",
    "PATTERN_REPEATED_FAILURE",
    "PATTERN_SINGLE_HIGH_IMPACT",
    "PATTERN_UNSUPPORTED_COMPLETION",
    "RecommendationStore",
]
