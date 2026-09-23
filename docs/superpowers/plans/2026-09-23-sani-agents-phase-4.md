# Sani Agents Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship registry-backed agent selection and attribution with Velo as the new-install default.

**Architecture:** `sani-core` registry supplies roster; native `Settings.agent_mode` supplies selection; React consumes typed native commands/events.

**Tech Stack:** Tauri/Rust, React/TypeScript, existing framed core IPC, SQLite history.

**Spec:** `docs/superpowers/specs/2026-09-23-sani-agents-phase-4-design.md`

## Global Constraints

- Do not create agents, static rosters, auto orchestration, or core restart for selection.
- Preserve explicit `velo`, `deep`, and legacy `auto`; missing `agent_mode` defaults to `velo`.
- Stored message attribution is immutable history.

## Review Focus

- Registry outage shows saved selection unavailable, not fake agents.
- A stale selected id fails honestly instead of rerouting silently.
- A legacy `auto` retains compatibility without becoming the default.

### Task 1: Native migration and validated selection

**Files:** Modify `sani/src-tauri/src/settings.rs`, `main.rs`, `runtime.rs`; test Rust modules.

- [ ] Write tests that deserialize missing `agent_mode` as `velo`, preserve `deep`/`velo`/`auto`, and assert `select_agent("deep")` and `select_agent("velo")` choose matching registry descriptors.
- [ ] Run `cargo test agent_mode runtime --manifest-path sani/src-tauri/Cargo.toml` and confirm the default test fails first.
- [ ] Change only `default_agent_mode()` to `velo`; add a typed native `set_agent_mode` command that reads current `runtime::agents`, rejects unknown non-`auto` ids, saves settings, and emits `settings://changed` without restarting core.
- [ ] Run focused Rust tests plus `cargo test --manifest-path sani/src-tauri/Cargo.toml`.
- [ ] Commit `feat: default Sani agent selection to Velo`.

### Task 2: Registry-backed UI controls

**Files:** Modify `sani/src/lib/tauri.ts`, `app/settings/SettingsContext.tsx`, `components/SettingsDrawer.tsx`, `components/MainConversation.tsx`; create `app/AgentsPage.tsx`.

- [ ] Add a focused client test/mocked command test proving roster comes from `core_agents` and mutation uses `set_agent_mode`.
- [ ] Add typed `AgentDescriptor`/selection helpers; refetch roster on focus/settings event and preserve saved label with unavailable state on failure.
- [ ] Render Agents cards with registry descriptors and friendly Velo/Deep copy, Home selector beside composer, and Quick Settings selector; expose no static selectable list.
- [ ] Run `npm --prefix sani run build` and validate each surface observes the native change event.
- [ ] Commit `feat: add registry-backed Sani agent controls`.

### Task 3: Historical attribution and regression

**Files:** Modify `components/Message.tsx`, main/panel conversation renderers; tests in history/runtime modules.

- [ ] Add tests for assistant attribution retained after changing the selected agent and for historic null attribution rendering neutral Sani.
- [ ] Render stored `agent_name` beside each assistant timestamp in main and panel history; never derive it from settings.
- [ ] Run Rust history/runtime tests and frontend build; manually exercise Velo→Deep selection and reopen history using a test conversation.
- [ ] Commit `feat: show producing Sani agent in history`.
