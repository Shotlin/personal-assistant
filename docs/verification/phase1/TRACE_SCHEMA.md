# Phase 1 trace schema (T11)

The mission trace lives in `sani.db` (`mission_events`) and projects into
the UI as a read-only view. One correlated trace per mission
(`trace_id == mission_id`), hash-chained in sequence order.

## Event record (`jarvis.v1`, `assistant.missions.contracts.TraceEvent`)

| Field | Meaning |
|---|---|
| `event_id` | UUID, unique |
| `sequence` | Global AUTOINCREMENT order across all missions |
| `trace_id` / `mission_id` | Mission correlation (RSI-01) |
| `plan_version`, `control_epoch` | Which plan revision / control generation produced the event |
| `step_id`, `execution_id` | Nullable; present for step-scoped events |
| `kind` | `request, plan, action, outcome, correction, recovery, approval, control, budget, verification, observer` |
| `safe_payload` | Bounded, redacted facts (no prompts, no payloads, no screenshots) |
| `evidence_ids` | Links into `mission_evidence` (sanitized-only) |
| `component_versions`, `skill_versions` | Build/skill provenance recorded with events |
| `occurred_at_ms` | UTC epoch milliseconds |
| `previous_hash`, `event_hash` | SHA-256 chain: `event_hash = H(body ∪ previous_hash)` |

## Chain guarantees and their limits (disclosed, not oversold)

- Any single-event edit, deletion, or reordering is **detected**
  (`MissionStore.verify_chain`, `tests/integration/test_mission_restart.py`).
- The chain is **not** cryptographic immutability: an attacker with the
  user's own privileges can rewrite the entire chain. This is out of
  threat-model protection and is never advertised otherwise.

## What major intents/outcomes reconstruct (RSI-01/02)

`request` (goal digest, origin, revision) → `plan` (version, step ids,
reason) → `action` (dispatch intent + packet digest; `dispatch: sent`) →
`outcome` (typed status, effect_outcome incl. UNKNOWN, failure_category)
→ `verification` (final gate status). Corrections append `correction`
events carrying the ORIGINAL goal plus the revision (RSI-04); approvals
append `approval` events with the exact action digest and expiry.

## Cost/latency and unknown flags (A12, RSI-13)

Per-run provider metering attaches at the provider boundary
(`LedgerCallbackHandler` wired in `DeepAgentEntry`): call counts, token
totals, and `unknown_*_calls` counters. Unknown usage/cost is preserved as
UNKNOWN — never folded into zero; `cost_usd` is null unless every call
reported a price. Mission-level budget counters live in
`missions.usage_json` (reserved/consumed per resource) and survive
restart.

## Observer output (RSI-05..08, RSI-23)

`ObserverRecommendation` records are append-only JSONL in a separate
store (default `<data>/observer-recs/`), structurally barred from
requesting authority (contract-level rejection) and carrying:
`pattern_id`, resolved `supporting_event_ids`, bounded redacted
`hypothesis`, `confidence ∈ [0,1]`, and `status="OBSERVATION_ONLY"`.
Deterministic patterns: `repeated_failure` (≥2 supported comparable),
`single:high_impact_failure` (explicitly single-instance),
`repeated_no_progress`, `unsupported_completion`.

## Retention

Standard metadata retention is proposed at 30 days and redacted evidence
at 7 days (owner-adjustable at implementation; never broadened by model
instruction). Deletion covers expired owned evidence plus orphaned assets
and observer copies, preserves explicit investigation holds, and writes a
deletion tombstone. (Retention enforcement wiring is recorded as a P1
debt item in the handoff.)
