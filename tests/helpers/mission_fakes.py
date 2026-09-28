"""Phase 1 mission fixtures: deterministic doubles for the Jarvis foundation.

Everything here is isolated by construction (file 06 environment F): no
provider, driver, microphone, desktop, or production database is touched.
The fakes give each suite the seams the implementation plan names for T01:

- ``CallCounter`` — model/driver call counting shared by every oracle.
- ``FakeClock`` — injected monotonic clock so deadlines and budgets are
  deterministic instead of wall-clock races.
- ``EffectSink`` — records every simulated external effect so tests can
  assert that a denial left the sink *empty* and that an accepted effect
  happened exactly once.
- ``CrashHook`` — raises at a named transaction boundary, for the
  crash-at-every-boundary invariant tests.
- ``EvidenceCollector`` — sanitized evidence intake that refuses raw
  secrets, mirroring the production EvidenceStore contract.

Synthetic desktop/account/state sequences live in ``tests/fixtures/missions``
as JSON: redacted by construction (no real bundle ids beyond Apple's public
ones, no accounts, screen text is fixture text), so they can be committed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "missions"

#: Marker strings that look like secrets; the evidence pipeline must never
#: persist these. Tests embed them in payloads and then assert absence.
SECRET_CANARIES = (
    "sk-fixture-canary-000",
    "FAKE_BEARER_TOKEN_9f2c",
    "password=fixture-not-a-real-secret",
)


class CallCounter:
    """Counts simulated model/driver calls per component."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def bump(self, component: str) -> int:
        self.counts[component] = self.counts.get(component, 0) + 1
        return self.counts[component]

    def total(self, component: str) -> int:
        return self.counts.get(component, 0)

    def reset(self) -> None:
        self.counts.clear()


class FakeClock:
    """Deterministic monotonic clock; ``advance`` moves virtual time."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@dataclass
class Effect:
    """One simulated external effect (typed, not a string)."""

    kind: str
    target: str
    payload_digest: str = ""
    sequence: int = 0


class EffectSink:
    """Records external effects; assertions read ``effects`` directly.

    A test asserts ``sink.effects == []`` for denials, and exactly one
    entry for an accepted action. Nothing here performs a real effect.
    """

    def __init__(self) -> None:
        self.effects: list[Effect] = []
        self._sequence = 0

    def perform(self, kind: str, target: str, payload_digest: str = "") -> Effect:
        self._sequence += 1
        effect = Effect(
            kind=kind, target=target, payload_digest=payload_digest, sequence=self._sequence
        )
        self.effects.append(effect)
        return effect

    @property
    def count(self) -> int:
        return len(self.effects)


class CrashHook:
    """Raises ``SimulatedCrash`` when a named boundary is reached.

    Tests inject ``hook.arm("before_intent_commit")`` into the production
    seam under test; when the seam calls ``hook.check(name)`` at that
    boundary the exception simulates a process kill at exactly that point.
    """

    def __init__(self) -> None:
        self.armed: str | None = None
        self.hit: list[str] = []

    def arm(self, boundary: str) -> None:
        self.armed = boundary

    def check(self, boundary: str) -> None:
        self.hit.append(boundary)
        if self.armed == boundary:
            raise SimulatedCrash(f"simulated crash at {boundary}")


class SimulatedCrash(RuntimeError):
    """A test-injected process death, not a real fault."""


@dataclass
class CollectedEvidence:
    """One sanitized evidence item that reached the collector."""

    kind: str
    redacted: bool
    sha256: str
    payload: dict[str, Any] = field(default_factory=dict)


class EvidenceCollector:
    """Sanitized evidence intake: refuses payloads that contain canaries.

    Mirrors the production contract ("sanitize before disk"): a payload
    carrying a canary string is withheld (recorded as ``redacted=True``
    with no payload content), never stored.
    """

    def __init__(self) -> None:
        self.items: list[CollectedEvidence] = []
        self.withheld: list[str] = []

    def put(self, kind: str, payload: dict[str, Any], digest: str = "") -> CollectedEvidence:
        text = json.dumps(payload, default=str)
        for canary in SECRET_CANARIES:
            if canary in text:
                self.withheld.append(kind)
                return CollectedEvidence(kind=kind, redacted=True, sha256=digest)
        record = CollectedEvidence(
            kind=kind, redacted=False, sha256=digest, payload=payload
        )
        self.items.append(record)
        return record

    def raw_items(self) -> list[CollectedEvidence]:
        return list(self.items)


def load_sequence(name: str) -> dict[str, Any]:
    """Load one synthetic sequence fixture from ``tests/fixtures/missions``."""
    path = FIXTURES_DIR / f"{name}.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def wall_clock_ms() -> int:
    """UTC epoch milliseconds, for timestamps inside fixture payloads."""
    return int(time.time() * 1000)
