# Sani Final Release Phase 10 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:verification-before-completion before release claims. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize and verify the packaged Mac Sani application through the Phase 1–9 acceptance matrix.

**Architecture:** No product architecture changes; fixes are confined to observed regressions in existing native/renderer/core systems.

**Tech Stack:** Rust/Cargo, TypeScript/Vite, Python/pytest, Tauri macOS bundler.

**Spec:** `docs/superpowers/specs/2026-09-23-sani-final-release-phase-10-design.md`

## Global Constraints

- No new product feature, agent, provider, backend, DB, workflow, avatar, or CUA engine.
- Report physical, simulated, unavailable, and automated evidence separately.

### Task 1: Full automated quality gate

**Files:** Modify only regression owners and `README.md` for verified results.

- [ ] Run `cargo fmt --check`, `cargo test`, `cargo check`, frontend typecheck/build, configured Python tests/lint/typecheck.
- [ ] Classify every failure with command output; repair only roadmap regressions and rerun the owning plus full suite.
- [ ] Verify settings, layout, agents, STT, manual voice, history, provider, CUA, and performance test groups explicitly.
- [ ] Commit `fix: resolve Sani release regressions` if needed.

### Task 2: Package and isolated acceptance

**Files:** Modify `README.md` with observed results only.

- [ ] Build `Sani.app` and DMG with `npm run tauri -- build`; launch the packaged app using isolated test data where needed.
- [ ] Execute the Phase 10 acceptance list: window/overlay geometry, settings convergence/secrets, agent selection/history, voice manual contract, CUA safe action, diagnostics, offline degradation, cancellation, and no duplicate sidecars.
- [ ] Verify 13/14/16-inch logical viewports and current-machine physical Retina/titlebar/resize; state external-display availability accurately.
- [ ] Commit `docs: verify Sani final release acceptance`.

### Task 3: Final evidence review

**Files:** Modify release notes/README only when evidence supports it.

- [ ] Re-run final commands after last change and inspect clean git status.
- [ ] Ensure normal-user errors are friendly and raw technical details remain diagnostic-only.
- [ ] Report passed, failed, skipped, and physically unavailable acceptance checks distinctly; do not make unsupported release claims.
- [ ] Commit `docs: record Sani release evidence`.
