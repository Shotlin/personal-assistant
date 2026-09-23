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

Quick Settings supports the same Phase-3-owned fast controls: Deep Agent
model, Velo provider/model, microphone, theme, global shortcut, and retained
launch-at-login. API-key editing remains Full Settings only. It opens Full
Settings for all other categories and uses the exact same snapshot and patch
commands, so both surfaces converge without duplicate React persistence.

## AI & Models Contract

Deep Agent has one supported provider: `openrouter`, with a user-entered model
ID. The OpenRouter key is shared with Velo when Velo uses OpenRouter.

Velo/JEV has exactly two supported providers: `openrouter` and `typesafe`.
The user selects provider and model ID explicitly. `velo_provider` is
authoritative end-to-end: Sani passes `VELO_PROVIDER=openrouter` or
`VELO_PROVIDER=typesafe` to sani-core; Python Settings persists that explicit
choice; and the JEV resolver selects its route from it, never credential
precedence. OpenRouter uses the shared OpenRouter Keychain key; TypeSafe uses
the TypeSafe Keychain key. Both keys may reach the core because Deep Agent
needs OpenRouter independently, but credential presence never decides Velo's
provider. A selected provider without a usable key makes Velo unavailable with
an actionable status and no fallback.

The full UI may request validation for a newly entered key, then call the
existing `store_provider_key` command only after explicit Save. Keys are
write-only UI fields: never returned in a snapshot, rendered after save,
written to JSON, included in events, logs, diagnostics, or child arguments.
Snapshots report only `absent` or `stored`. Connected, invalid, and offline
are returned only by an explicit candidate-key validation or a new
`validate_stored_provider_key(provider)` command that reads Keychain material
internally and never returns it. Settings opening does not validate over the
network automatically.

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
validate_stored_provider_key(provider) -> KeyStatus
save_voice_settings(patch)
save_microphone_settings(patch)
save_shortcut_settings(patch)
save_startup_settings(patch)
```

Each command validates its own finite enum/value domain before mutation.
`reasoning_provider` is strictly `openrouter`; `velo_provider` is strictly
`openrouter | typesafe`, and unsupported legacy values remain readable but are
never presented as selectable Phase-3 choices. It
uses the existing settings writer and corresponding live side effects: hotkey
registration, audio-handle reset, autostart enable/disable, and runtime restart
where AI settings change. AI application is deterministic: reject an AI patch
while a run is active with “Finish the current task before changing AI
settings.” Otherwise validate, persist, controlled-restart sani-core, wait for
ready, then emit `settings://changed` and report `Ready`. The UI states are
Saved, Applying, Ready, and Failed to apply; it never reports Saved as Applied
while the running core retains old configuration. A failed new runtime does not
fall back silently. Provider/model fields do not change credentials.

## UX and Error Rules

The Voice page shows only current STT model and ready/preparing/unavailable
runtime information. It exposes neither `stt_turn_end_ms` nor silence tuning,
downloads, model management, or manual submission behavior. The Computer
Control page uses actual system-permission/core status to show Ready/Needs
attention, Accessibility and Screen Recording allowance (including restart
requirements), and existing macOS permission actions without internal CUA/MCP
jargon. Appearance contains theme, a layout summary, and a route to the Phase
2 Layout editor; it never duplicates that editor. Storage is read-only local
data, settings, and history/database location information with a local-only
explanation—no delete, clear, repair, export, import, or cleanup actions.

The main pane loads a snapshot once, maintains per-section drafts, validates
before Save, and does not optimistically claim a system side effect succeeded.
Unsaved section changes can be discarded when switching categories; no secrets
are retained after a key save/cancel. Native errors are mapped to concise
actionable messages. Permission buttons open existing System Settings panes;
they do not claim macOS permission was granted until a fresh snapshot says so.

## Testing and Acceptance

Rust tests cover legacy JSON migration, patch isolation, rejecting unsupported
AI providers, explicit Velo provider propagation/no fallback, Keychain-service
selection, stored-versus-network-validation status, safe deferred/rejected AI
reload, and side effects for mic/hotkey/autostart. Renderer tests cover category
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
