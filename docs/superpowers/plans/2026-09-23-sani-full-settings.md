# Sani Full Settings System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one truthful Full and Quick Settings system backed by native state, secure Keychain credentials, and applied runtime configuration.

**Architecture:** Rust supplies a single non-secret snapshot and typed mutation commands. Both React settings surfaces consume that facade. AI changes validate, persist, explicitly configure Velo, then safely reload sani-core before reporting Ready.

**Tech Stack:** Tauri 2/Rust, React/TypeScript, existing Keychain helpers, sani-core Python Settings/JEV resolver.

**Spec:** `docs/superpowers/specs/2026-09-23-sani-full-settings-design.md`

## Global Constraints

- Deep Agent provider is fixed to `openrouter`; Velo provider is only `openrouter | typesafe`.
- `VELO_PROVIDER` must determine JEV routing, never credential precedence; unavailable selected credentials mean unavailable, never fallback.
- API keys remain write-only macOS Keychain data; never snapshot, event, log, diagnostics, error, argv, or JSON.
- AI/model and provider-key mutations reject while a run is active. They persist desired state, controlled-restart core, wait for ready, and report truthful applied status.
- Desired configuration and applied runtime state are distinct: persisted selections remain visible after a reload failure as `failed_to_apply`; there is no silent rollback or provider/model fallback. A later launch may retry the desired configuration.
- Quick and Full Settings are separate Tauri WebViews. Each independently instantiates the same SettingsContext implementation; both consume native snapshot/typed commands/`settings://changed`, and native state is the only authority.
- Voice is status-only; Storage is read-only; Appearance links to Phase 2 Layout; no Phase 4+ features.

## Review Focus

- Both keys present + TypeSafe selected must route TypeSafe: Task 1.
- Core restart failure must retain desired state but must not claim it applies: Task 2.
- Stored key must not be called connected until explicit validation: Task 3.
- A Quick Settings change must appear in Full Settings without duplicate persistence: Task 4.
- Legacy unsupported reasoning provider must not appear selectable: Task 2.

### Task 1: Make Velo provider explicit end-to-end

**Files:** Modify `sani/src-tauri/src/sani_core.rs`, Python runtime settings/JEV resolver under `src/assistant/`; test existing Rust/Python provider tests.

- [ ] Write failing tests with both credentials present and `VELO_PROVIDER=typesafe`, asserting direct TypeSafe route; assert `openrouter` uses only System One route and absent selected key returns unavailable.
- [ ] Run the focused provider tests and verify current credential-precedence behavior fails them.
- [ ] Add strict `VELO_PROVIDER` environment propagation from `settings.velo_provider`; add Python explicit provider setting; route JEV solely from it and reject unknown values.
- [ ] Run focused Rust/Python tests and the selected core test suite.
- [ ] Commit: `feat: honor Sani Velo provider selection`.

### Task 2: Native full-settings facade and applied AI reload

**Files:** Modify `sani/src-tauri/src/main.rs`, `settings.rs`, `sani_core.rs`, `app_state.rs`; tests in their `#[cfg(test)]` modules.

- [ ] Write failing tests for fixed reasoning provider, invalid Velo provider rejection, active-run AI patch rejection, and state sequence `saved -> applying -> ready|failed_to_apply`.
- [ ] Run `cd sani/src-tauri && cargo test settings sani_core`; verify failures.
- [ ] Define `SettingsSnapshot`, strict typed patch structs, and `get_full_settings`; include only non-secret persisted values plus mic/STT/permission/core/storage metadata and key presence (`absent|stored`).
- [ ] Implement AI patch transaction: reject active run, validate, persist the desired configuration, stop/restart core, await bounded ready handshake, and return applied status. On failure retain the desired persisted configuration, set `failed_to_apply`, avoid fallback/rollback, and let a later launch retry it.
- [ ] Expose non-secret runtime status that distinguishes `saved`, `applying`, `ready`, and `failed_to_apply`; only `ready` asserts the running core corresponds to persisted AI configuration. Emit a non-secret `settings://changed` snapshot/version after mutations and runtime state transitions. Run `cargo test && cargo check`.
- [ ] Commit: `feat: apply Sani settings through native facade`.

### Task 3: Secure credential storage, validation, and runtime application

**Files:** Modify `sani/src-tauri/src/onboarding.rs`, `main.rs`; tests in onboarding/settings modules.

- [ ] Write failing tests for `validate_stored_provider_key`: absent -> absent; stored secret is read internally; result never contains key material. Add tests that add/replace/delete provider keys reject active runs and use the reload transaction.
- [ ] Run focused tests; verify command absence.
- [ ] Implement stored-key validation using existing Keychain service mapping and validation clients. Keep candidate validation separate; return only `absent|connected|invalid|offline` for stored validation.
- [ ] Make add/replace/delete credential mutations use the Task 2 runtime policy: optional explicit candidate validation, active-run rejection, Keychain store/delete, controlled core restart, bounded ready check, and truthful `saved|applying|ready|failed_to_apply` result. Writing a Keychain record alone must never be reported active.
- [ ] Verify logs/events/serialized response cannot include input key or stored secret; run focused and full Rust tests.
- [ ] Commit: `feat: validate stored Sani provider keys safely`.

### Task 4: Shared React settings client and Quick Settings expansion

**Files:** Modify `sani/src/lib/tauri.ts`, `sani/src/components/SettingsDrawer.tsx`; create `sani/src/app/settings/SettingsContext.tsx`; modify `PanelApp.tsx`.

- [ ] Add typed snapshot, statuses, patch commands, and `settings://changed` listener; write renderer tests or a small pure-client test for refresh after event.
- [ ] Implement one provider/context implementation that refetches after mutation/event and exposes no key value after a save/cancel. Mount independent instances in the main and overlay renderers; never use cross-WebView React state as authority.
- [ ] Refactor Quick Settings to consume it; add Deep Agent model and Velo provider/model controls, retain mic/theme/hotkey/login, and an Open Full Settings action. Keep key editing absent.
- [ ] Run `cd sani && npm run build`; manually confirm Quick mutation refreshes snapshot rather than local persisted state.
- [ ] Commit: `feat: unify Sani quick settings`.

### Task 5: Full Settings main-window experience

**Files:** Create `sani/src/app/settings/FullSettings.tsx` and category components; modify `MainApp.tsx`, `main.css`.

- [ ] Build category navigation for General, AI & Models, Voice, Microphone, Computer Control, Appearance, Shortcuts, Startup, Storage using the Task 4 context.
- [ ] AI page: fixed OpenRouter Deep Agent provider/model, explicit Velo provider/model, key presence, candidate/stored validation, write-only key entry, Applying/Ready/Failed state.
- [ ] Implement status-only Voice, authoritative Computer Control permissions/status/actions, Appearance link to Layout, and read-only Storage paths. Do not add excluded controls.
- [ ] Add keyboard/focus/error behavior and responsive desktop layout; run `npm run build`.
- [ ] Commit: `feat: add Sani full settings`.

### Task 6: Regression, package, and macOS acceptance

**Files:** Modify `sani/README.md` only for verified behavior.

- [ ] Run `npm run build`, `cargo test --manifest-path src-tauri/Cargo.toml`, `cargo check --manifest-path src-tauri/Cargo.toml`, and the selected Python tests.
- [ ] Build with `npm run tauri -- build`; test Quick/Full convergence, explicit Velo selection with both key states, safe active-run rejection, provider validation, hotkey/mic/autostart changes, permissions, Retina layout, and read-only Storage.
- [ ] Document observed behavior only; commit `docs: verify Sani full settings`.

## Plan Self-Review

Tasks 1–3 close the runtime/provider/key truthfulness gaps; Tasks 4–5 deliver both surfaces over one facade; Task 6 verifies the specified platform behavior. The plan contains no Phase 4/5 implementation. `SettingsSnapshot`, key status, and provider enums are native-first across all tasks; secrets are never typed into snapshot/event contracts.
