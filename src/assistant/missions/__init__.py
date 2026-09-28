"""Jarvis Phase 1 mission foundation (package 03, file 05).

One durable mission owner above the existing Velo fast path:

- :mod:`assistant.missions.contracts` — the typed ``jarvis.v1`` surface;
- :mod:`assistant.missions.store` — atomic SQLite persistence in the
  embedded ``sani.db``;
- later modules (authority, executor, service, controller, recovery,
  evidence, observer) land with their tasks and are composed in
  :mod:`assistant.core`.

Everything is default-off behind ``JARVIS_MISSIONS_ENABLED``; the existing
shell runs unchanged when the flag is false.
"""

from assistant.missions.store import (
    MissionIdentityCollision,
    MissionStore,
    MissionStoreError,
    ResultConflict,
    StaleControlError,
    UnknownExecution,
)

__all__ = [
    "MissionIdentityCollision",
    "MissionStore",
    "MissionStoreError",
    "ResultConflict",
    "StaleControlError",
    "UnknownExecution",
]
