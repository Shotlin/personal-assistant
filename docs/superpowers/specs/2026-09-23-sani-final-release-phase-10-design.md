# Sani Phase 10 — Final Release Design

## Outcome

Phase 10 is stabilization and packaged Mac release verification for Phases 1–9, not a feature phase.

## Scope

Fix only regressions required by approved behavior. Keep the desktop + sani-core + Velo + Deep Agent + CUA + Moonshine + SQLite + Keychain architecture. Do not add agents, orchestration, web/server products, Docker/Postgres, avatar/workflow systems, providers, or CUA engines.

## Verification

Run configured Rust, TypeScript, Python, formatting/lint/type checks; build Sani.app and DMG; test packaged-app flows using isolated data where destructive. Physically verify only the current MacBook and report simulated 13/14/16-inch layouts separately. If no external display exists, state that physical coverage was not performed while retaining automated geometry evidence.

## Release bar

Cloud/permission failures degrade independently. Do not expose raw tracebacks/IPC to normal users. Final evidence distinguishes automated, physical, skipped-with-reason, and failed checks.
