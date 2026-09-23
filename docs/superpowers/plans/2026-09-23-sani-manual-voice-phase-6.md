# Sani Manual Voice Phase 6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit Finish & Send the sole voice-to-agent handoff.

**Architecture:** Moonshine accumulates segments; Rust retains Listening through pauses and invokes `flush` only from explicit finish; React renders accumulated draft.

**Tech Stack:** Rust state machine, Python sidecar protocol, React overlay/main UI.

**Spec:** `docs/superpowers/specs/2026-09-23-sani-manual-voice-phase-6-design.md`

## Global Constraints

- Silence/VAD/line completion/partial never create an agent run.
- Flush produces at most one final run; discard produces zero.
- Preserve accumulated transcript and never auto-send at long-listening limits.

### Task 1: Sidecar manual accumulator contract

**Files:** Modify `sani_stt.py`; test Python sidecar accumulator.

- [ ] Write failing tests for 2/5/15-second silence and line completion yielding no final, and explicit flush yielding exactly one final.
- [ ] Remove deadline commit as a handoff path while retaining silence notes/segmentation; ensure `discard` clears all pending text.
- [ ] Add a bounded accumulator warning event that does not call final.
- [ ] Run focused Python STT tests.
- [ ] Commit `feat: require explicit Sani voice flush`.

### Task 2: Rust state machine and duplicate guards

**Files:** Modify `app_state.rs`, `speech.rs`; Rust tests.

- [ ] Write tests with fake sidecar events proving pause/possible-end generates zero `begin_turn` calls, flush generates one, discard generates zero, and late finals are ignored.
- [ ] Maintain accumulated stable transcript plus current partial; leave gate open across silence; close only on explicit finish/cancel/limit.
- [ ] Map hotkey while Listening to finish, Escape to discard, and long-limit to safe recording stop plus user action state.
- [ ] Run full Rust tests.
- [ ] Commit `feat: make Sani voice submission manual`.

### Task 3: Main and pill listening UI

**Files:** Modify `MainConversation.tsx`, `OverlayApp.tsx`, `PanelApp.tsx`, `tauri.ts`, styles.

- [ ] Add renderer tests/manual event fixture for accumulated transcript and Finish/Cancel actions.
- [ ] Show “Listening”, accumulated text, Finish & Send, Cancel, and a long-recording warning in main; show compact transcript/Finish affordance in pill.
- [ ] Run frontend build and controlled microphone acceptance: pause, continue, finish, cancel, and hotkey.
- [ ] Commit `feat: show manual Sani voice submission controls`.
