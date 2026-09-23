# Sani Voice Models Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add truthful Moonshine model installation, selection, recovery, and removal.

**Architecture:** One native STT catalog controls sidecar invocation/cache state; React never defines models or progress.

**Tech Stack:** Rust/Tauri, existing `sani_stt.py`, Moonshine cache, React.

**Spec:** `docs/superpowers/specs/2026-09-23-sani-voice-models-phase-5-design.md`

## Global Constraints

- Exact ids: tiny/base/small/medium streaming English; default Small.
- No fake bytes, percent, installs, or removal outside owned cache.
- Failed change restores a previous ready model; never restart full app.

### Task 1: Native model catalog and cache inspection

**Files:** Modify `sani/src-tauri/python/sani_stt.py`, `src/speech.rs`, `src/settings.rs`; test sidecar/Rust parser.

- [ ] Write tests asserting exact catalog order/ids, `small-streaming-en` missing default, and cache status derives from sidecar-owned paths.
- [ ] Run focused sidecar/Rust tests and confirm the catalog is not duplicated.
- [ ] Define sidecar machine-readable `models`/cache response and Rust `VoiceModelSnapshot`; expose `voice_models` native command.
- [ ] Run selected STT tests and `cargo test --manifest-path sani/src-tauri/Cargo.toml`.
- [ ] Commit `feat: expose Sani voice model catalog`.

### Task 2: Install/switch/recover transaction

**Files:** Modify `speech.rs`, `app_state.rs`, `main.rs`; add tests.

- [ ] Write tests for selected-model ready success, failed new-model load restoring prior model, and microphone capture surviving restart.
- [ ] Implement install events only from downloader/sidecar signals; implement switch as prior-ready capture → start selected → bounded ready → persist, otherwise restore prior and emit friendly failure.
- [ ] Add `install_voice_model` and `use_voice_model` commands that reject active voice capture safely.
- [ ] Run focused tests and full Rust suite.
- [ ] Commit `feat: switch Sani voice models safely`.

### Task 3: Voice UI and safe removal

**Files:** Modify `app/settings/FullSettings.tsx`, `lib/tauri.ts`, `styles/main.css`; add native removal command/tests.

- [ ] Write tests that active model removal is rejected and non-owned paths are never targeted.
- [ ] Render four catalog cards, actual installed/active/progress state, Install/Use/Remove, and friendly failure/retry state.
- [ ] Implement explicit `remove_voice_model` confirmation and owned-cache-only delete.
- [ ] Run frontend build, STT/Rust tests, and manual local model switch.
- [ ] Commit `feat: add Sani voice model manager`.
