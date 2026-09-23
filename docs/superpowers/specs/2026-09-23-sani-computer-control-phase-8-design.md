# Sani Phase 8 — Computer Control Design

## Outcome

The existing CUA-backed Velo capability is understandable and truthful to a normal Mac user, with real local acceptance evidence.

## Architecture

The Computer Control page combines `permission_snapshot`, native permission actions, and `core_status`; it does not implement or simulate a second control engine. Runtime events displayed as Velo progress must originate from the existing CUA execution path.

## Experience and acceptance

Use “Computer Control”, “Accessibility”, “Screen Recording”, and “Runtime” with Ready/Needs attention/Not allowed/unavailable copy. A permission is never shown allowed until a fresh native snapshot says so. Physical acceptance may use only the roadmap-authorized disposable TextEdit scenario, with no Dock/System Settings preference changes. Cancellation must halt further actions.

## Tests

Unit/integration tests cover status mapping and cancellation. Phase completion additionally requires observed packaged-app safe CUA action, activity, real pointer movement if supported, harmless typing, and cancellation; unavailable hardware is reported rather than fabricated.
