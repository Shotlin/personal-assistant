# Sani Phase 9 — Performance Instrumentation Design

## Outcome

Sani records and displays truthful local timing evidence for voice, core dispatch, first progress/token/decision/action, completion, failure, and cancellation.

## Architecture

Native monotonic timing owns measurement; wall time is used only for display. Safe timing records are persisted in Sani-owned SQLite and linked by run id. Events contain ids, stage names, status, and durations—never transcript content, audio, credentials, screenshots, or clipboard data.

## Experience and optimization

Diagnostics → Performance renders recorded values and “Not measured” when a stage was absent. Health shows current independent subsystem truth. Optimization is permitted only after representative measurements identify a bottleneck; it must preserve one core, one STT sidecar, and event-driven behavior.

## Tests

Ordering, cancellation, safe serialization, missing-value UI, no polling/duplicate runtime regression, and representative run capture are required. No performance claim is made without before/after records.
