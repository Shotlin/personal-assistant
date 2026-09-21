"""Velo -- standalone quick-control agent (JEV + CUA driver).

Phase 1 boundary: this package is self-contained. It shares the
repository's bounded CUA infrastructure and settings, but it NEVER imports
the Deep Agent, Sani, an OpenAI/OpenRouter client, or any other LLM
fallback. If Velo cannot continue safely it returns ASK_USER / STOPPED /
FAILED (Velo spec sections 1-2).
"""

from assistant.velo.agent import VeloAgent
from assistant.velo.cua_adapter import VeloCuaAdapter, allowed_apps_from_manifest
from assistant.velo.types import (
    JevContractError,
    JevServiceError,
    VeloActionKind,
    VeloCuaError,
    VeloDecision,
    VeloDecisionStatus,
    VeloError,
    VeloLimits,
    VeloObjective,
    VeloObservation,
    VeloResult,
    VeloStatus,
)

__all__ = [
    "JevContractError",
    "JevServiceError",
    "VeloActionKind",
    "VeloAgent",
    "VeloCuaAdapter",
    "VeloCuaError",
    "VeloDecision",
    "VeloDecisionStatus",
    "VeloError",
    "VeloLimits",
    "VeloObjective",
    "VeloObservation",
    "VeloResult",
    "VeloStatus",
    "allowed_apps_from_manifest",
]
