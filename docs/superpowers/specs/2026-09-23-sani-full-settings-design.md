# Sani Full Settings System — Phase 3 Design

## Intent

Build a complete Settings experience inside the existing Sani main desktop
application while retaining the overlay's Quick Settings drawer. Both surfaces
read and mutate one authoritative native settings domain; neither holds a
second persisted settings model. Phase 3 exposes only real, already-supported
configuration and never stores API keys in `settings.json`.

## Current-State Basis

`settings.rs` already persists general, microphone, launch-at-login, theme,
STT status/configuration, Deep Agent/OpenRouter and Velo provider/model values,
main-window state, and overlay-layout state. `main.rs` currently exposes a
small `get_settings` / `save_settings_cmd` projection. `onboarding.rs` owns
the real AI config and Keychain operations: OpenRouter and TypeSafe keys are
stored under service-specific macOS Keychain entries and are only returned as
presence booleans. `SettingsDrawer` in the conversation overlay is a limited
consumer of the same bridge. `sani_core.rs` reads the persisted non-secret
provider/model choices and injects only retrieved key values into its child
environment.

## Architecture Decision

Extend the native settings boundary into a single typed snapshot plus typed
patch commands, and have both Full Settings and Quick Settings consume it.
This is preferred to copying drawer state into the main renderer because it
keeps Keychain presence, runtime-derived microphone/STT state, validation, and
side effects (hotkey rebinding, audio reset, autostart) authoritative. The
existing AI configuration and credential commands become part of this same
facade rather than creating parallel provider logic.

## Navigation and Scope

The main Settings route has a left category list and one content pane:

- General: product behavior and non-sensitive defaults already supported.
- AI & Models: Deep Agent and Velo/JEV provider/model choices plus key status.
- Voice: existing STT readiness/model information and voice behavior already
  exposed by Sani; no model downloader or manager.
- Microphone: selected input, enumerated inputs, permission state, and link to
  the existing macOS privacy pane.
- Computer Control: existing Velo/JEV enablement/configuration status only;
  no CUA redesign.
- Appearance: existing theme and overlay-layout entry point/status.
- Shortcuts: existing global shortcut capture and validation.
- Startup: launch-at-login state and explanatory macOS behavior.
- Storage: local history/settings location and safe actions already supported;
  no conversation/log expansion or destructive data cleanup in this phase.

Quick Settings remains an overlay drawer for fast microphone, theme,
launch-at-login, and shortcut changes. It opens Full Settings in the main
window for categories it does not own. It must use the exact same snapshot and
patch commands, with native events or refetch after successful mutation so both
surfaces immediately converge.

## AI & Models Contract

Deep Agent has one supported provider: `openrouter`, with a user-entered model
ID. The OpenRouter key is shared with Velo when Velo uses OpenRouter.

Velo/JEV has exactly two supported providers: `openrouter` and `typesafe`.
The user selects provider and model ID explicitly. Choosing OpenRouter requires
the shared OpenRouter Keychain key; choosing TypeSafe requires the TypeSafe
Keychain key. A missing or invalid key makes that selected provider unavailable
for use and presents a clear actionable status. It must never silently switch
providers or reuse the other provider's model.

The full UI may request validation for a newly entered key, then call the
existing `store_provider_key` command only after explicit Save. Keys are
write-only UI fields: never returned in a snapshot, rendered after save,
written to JSON, included in events, logs, diagnostics, or child arguments.
Key status reports only absent, stored, validating, connected label (where the
provider supplies one), invalid, or offline.

## Native Settings Facade

Introduce a full `SettingsSnapshot` with the existing persisted values plus
runtime metadata: microphone list, microphone permission, STT readiness,
provider-key presence/status, overlay layout status, and non-secret storage
metadata. Use explicitly typed patches separated by side effect:

```text
get_full_settings() -> SettingsSnapshot
save_general_settings(patch)
save_ai_model_settings(patch) -> AiConfig
store_provider_key(provider, key) -> KeyStatusSummary
validate_provider_key(provider, key) -> KeyStatus
save_voice_settings(patch)
save_microphone_settings(patch)
save_shortcut_settings(patch)
save_startup_settings(patch)
```

Each command validates its own finite enum/value domain before mutation. It
uses the existing settings writer and corresponding live side effects: hotkey
registration, audio-handle reset, autostart enable/disable, and runtime restart
only where existing code already needs it. Provider/model fields do not change
credentials. `settings://changed` carries only a fresh non-secret snapshot or
version signal after a successful save.

## UX and Error Rules

The main pane loads a snapshot once, maintains per-section drafts, validates
before Save, and does not optimistically claim a system side effect succeeded.
Unsaved section changes can be discarded when switching categories; no secrets
are retained after a key save/cancel. Native errors are mapped to concise
actionable messages. Permission buttons open existing System Settings panes;
they do not claim macOS permission was granted until a fresh snapshot says so.

## Testing and Acceptance

Rust tests cover legacy JSON migration, patch isolation, rejecting unsupported
AI providers, no Velo fallback, Keychain-service selection, absent-key status,
and side effects for mic/hotkey/autostart. Renderer tests cover category
navigation, shared snapshot refresh in quick/full surfaces, disabled provider
states, write-only key controls, and no secret rendering. Manual macOS
acceptance covers changing an existing setting from each surface and observing
the other update, OpenRouter/TypeSafe selection with only status exposed,
microphone permissions, launch-at-login behavior, hotkey rebinding, and
Retina/responsive main-window settings layout.

## Non-Goals

No agents, avatars, STT download manager, manual submission redesign,
conversation/log expansion, CUA redesign, provider implementation beyond
existing OpenRouter/TypeSafe support, or physical local-data deletion is added.
