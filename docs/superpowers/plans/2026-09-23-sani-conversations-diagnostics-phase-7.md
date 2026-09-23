# Sani Conversations and Diagnostics Phase 7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver persistent local conversations, execution details, and technical retention.

**Architecture:** Extend `sani-history.db` migration-safely; native history commands own all persistence and React renders queries/events.

**Tech Stack:** rusqlite, Tauri/Rust, React.

**Spec:** `docs/superpowers/specs/2026-09-23-sani-conversations-diagnostics-phase-7-design.md`

## Global Constraints

- No remote analytics, second DB, secret/audio/screenshot/clipboard persistence.
- Conversation deletion remains explicit; technical retention never deletes chat or agent memory.

### Task 1: History/activity schema and commands

**Files:** Modify `history.rs`, `runtime.rs`, `main.rs`; add Rust tests.

- [ ] Write migration tests from prior SQLite schema and tests that safe activity records link to runs without secret fields.
- [ ] Add `run_activity` table/indexes and typed `RunDetail`/`ActivityRecord`; persist start/progress/finish metadata emitted by runtime.
- [ ] Add list/get execution-detail commands and conversation list preview query, preserving existing command semantics.
- [ ] Run Rust history/runtime tests and full suite.
- [ ] Commit `feat: persist Sani execution details locally`.

### Task 2: Conversations and details UI

**Files:** Create `app/ConversationsPage.tsx`, `components/ExecutionDetails.tsx`; modify `MainApp.tsx`, `tauri.ts`, `Message.tsx`, styles.

- [ ] Add client fixtures for selected conversation, preview, nullable historical attribution, and unavailable detail state.
- [ ] Render native conversation list/select/delete confirmation, messages/timestamps/stored attribution, and optional non-secret Activity drawer.
- [ ] Ensure no React history cache becomes authoritative after a native event.
- [ ] Run frontend build and restart persistence acceptance using disposable history.
- [ ] Commit `feat: add Sani conversations and execution details`.

### Task 3: Retention controls

**Files:** Modify `settings.rs`, `onboarding.rs`, `history.rs`, `FullSettings.tsx`; tests.

- [ ] Write tests for 7/14/30-day activity pruning, explicit clear, and chat rows remaining intact.
- [ ] Persist a technical-retention enum; implement safe prune/clear commands against activity tables only.
- [ ] Render Storage retention choice and explicit clear confirmation with result count.
- [ ] Run Rust/frontend tests and commit `feat: add Sani technical retention controls`.
