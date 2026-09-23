# Sani Performance Phase 9 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record and display safe real timing evidence, then optimize only measured bottlenecks.

**Architecture:** Native monotonic stage recorder writes safe per-run rows into existing SQLite; diagnostics queries these rows and never estimates missing metrics.

**Tech Stack:** Rust `Instant`, rusqlite, React, existing runtime/STT/core events.

**Spec:** `docs/superpowers/specs/2026-09-23-sani-performance-phase-9-design.md`

## Global Constraints

- Do not persist payload text/audio/screenshots/clipboard/credentials.
- No polling loops or duplicate runtimes; no speed claims without before/after records.

### Task 1: Native timing recorder

**Files:** Create `performance.rs`; modify `app_state.rs`, `runtime.rs`, `history.rs`, `main.rs`; tests.

- [ ] Write tests for monotonic stage order, cancellation terminal timing, and serialized records excluding private text/key fields.
- [ ] Define stages: listen-ready, first-partial, finish-final, run-start, first-progress, first-token, first-Velo-decision, first-CUA-action, completion/cancel/failure.
- [ ] Record only when events occur; persist safe timing records linked to run id.
- [ ] Run Rust tests and commit `feat: record Sani runtime performance`.

### Task 2: Diagnostics Performance UI

**Files:** Create `app/DiagnosticsPage.tsx`; modify `MainApp.tsx`, `tauri.ts`, styles.

- [ ] Add query/UI tests for actual values and “Not measured”.
- [ ] Add native recent-performance and subsystem-health commands; render user-facing durations and expandable safe detail.
- [ ] Run frontend build and representative local run capture.
- [ ] Commit `feat: add Sani performance diagnostics`.

### Task 3: Evidence-based optimization decision

**Files:** Modify only measured bottleneck owners; update README evidence.

- [ ] Capture baseline records for a representative voice and text run.
- [ ] If a bottleneck is established, write a targeted failing/performance regression test, implement one event-driven optimization, and compare same-stage records.
- [ ] If no valid bottleneck evidence exists, make no speculative optimization and document measurement-only outcome.
- [ ] Commit `perf: optimize measured Sani bottleneck` only when comparison supports it.
