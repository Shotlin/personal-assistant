"""Single-agent harness profile (spec sections 11.1-11.2).

Exactly one agent: the general-purpose subagent is disabled (no ``task``
tool) and the host-shell ``execute`` tool is excluded. Registration is
keyed by the resolved provider so it binds to whatever model the factory
built; an exact ``provider:identifier`` key takes precedence.
"""

from __future__ import annotations

from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
from deepagents._models import get_model_identifier, get_model_provider

# Pinned deepagents 0.7.15: used only to fail fast when a registered
# profile would not actually bind to the built model.
from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model
from langchain_core.language_models.chat_models import BaseChatModel

FALLBACK_PROVIDER_KEY = "assistant"
EXECUTED_TOOLS_EXCLUDED = frozenset({"execute"})


class AgentProfileError(RuntimeError):
    """Raised when the single-agent profile cannot bind to the model."""


SINGLE_AGENT_PROFILE = HarnessProfile(
    excluded_tools=EXECUTED_TOOLS_EXCLUDED,
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
)


def _provider_keys(model: BaseChatModel) -> list[str]:
    provider = get_model_provider(model)
    if not provider:
        raise AgentProfileError(
            "Cannot resolve a provider key for the model "
            f"({type(model).__module__}.{type(model).__name__}); the single-agent "
            "harness profile would silently not apply."
        )
    identifier = get_model_identifier(model)
    keys = [provider]
    if identifier:
        keys.insert(0, f"{provider}:{identifier}")
    return keys


def register_single_agent_profile(model: BaseChatModel) -> list[str]:
    """Register the single-agent profile for the model and verify it binds.

    Returns the registry keys that were registered (exact key first).
    """
    keys = _provider_keys(model)
    for key in keys:
        register_harness_profile(key, SINGLE_AGENT_PROFILE)

    resolved = _harness_profile_for_model(model, None)
    if "execute" not in resolved.excluded_tools:
        raise AgentProfileError(
            f"Harness profile did not bind for provider keys {keys}: "
            f"resolved excluded_tools={set(resolved.excluded_tools)}"
        )
    gp = resolved.general_purpose_subagent
    if gp is not None and gp.enabled:
        raise AgentProfileError(
            f"Harness profile did not disable the general-purpose subagent for keys {keys}"
        )
    return keys
