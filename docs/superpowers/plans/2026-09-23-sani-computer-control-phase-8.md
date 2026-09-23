# Sani Computer Control Phase 8 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide truthful Computer Control UX and evidence-backed real CUA acceptance.

**Architecture:** Existing permission commands and `core_status` remain authoritative; UI maps them to user language and renders real runtime events.

**Tech Stack:** Tauri/Rust, existing sani-core/Velo/CUA, React.

**Spec:** `docs/superpowers/specs/2026-09-23-sani-computer-control-phase-8-design.md`

## Global Constraints

- Never simulate actions/cursor/progress or claim permissions before native refresh.
- Physical tests use only the authorized disposable TextEdit workflow; never alter Dock/System Settings preferences.

### Task 1: Typed health snapshot and page

**Files:** Modify `onboarding.rs`, `sani_core.rs`, `tauri.ts`, `FullSettings.tsx` or create `ComputerControlPage.tsx`; tests.

- [ ] Write status mapping tests for ready, unavailable, permission-required, and restart-required responses.
- [ ] Add one typed native computer-control snapshot combining fresh permissions and safe core system status; convert raw failures to friendly copy.
- [ ] Render Computer Control, Accessibility, Screen Recording, Runtime, refresh, request, and Open System Settings actions.
- [ ] Run Rust/frontend checks and commit `feat: show truthful Sani computer control status`.

### Task 2: Real-event audit and cancellation

**Files:** Modify `runtime.rs` and Velo/core event adapters only if needed; tests.

- [ ] Write tests mapping actual CUA/Velo lifecycle events to activity, and that cancel ends event emission after terminal cancellation.
- [ ] Preserve actual event source/timestamp/action labels; do not add UI-only progress emitters.
- [ ] Run Velo/core cancellation suite.
- [ ] Commit `fix: preserve real Sani computer control activity`.

### Task 3: Packaged local acceptance

**Files:** Modify `sani/README.md` only with observed evidence.

- [ ] Build package and perform authorized TextEdit pointer/open/type/select/close-without-save flow through Velo; record only pass/fail evidence, no sensitive content.
- [ ] Run a cancellable safe multi-step action and confirm no post-cancel activity.
- [ ] If a prerequisite/real pointer support is absent, document exact observed limitation and do not mark physical acceptance complete.
- [ ] Commit `docs: verify Sani computer control acceptance`.
