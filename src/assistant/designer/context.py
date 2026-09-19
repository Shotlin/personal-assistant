"""Context policies: budgets, prepared context, and token accounting (P6, R08, R15, R16, Fix 8).

Implements context budgets, ceiling enforcement, and non-billable context
preparation adhering to frozen plan requirements:
- Context defaults: 12 recent turns, 32k estimated input, 2048 output cap,
  16 model attempts, 60 tool calls, 15-min timeout within operator ceilings.
- Fix 8 token vocabulary: ``estimated_context_tokens`` is strictly separated
  from ``provider_reported_input_tokens``, and unknown is ``None`` (never 0).
- Non-billable context preview: zero LLM monitoring calls.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from assistant.designer.compiler import ContextPolicy, ExecutionConfig


class BudgetExceeded(RuntimeError):
    """Raised when execution reaches a context policy or resource ceiling."""

    def __init__(self, resource: str, limit: int | float, used: int | float) -> None:
        self.resource = resource
        self.limit = limit
        self.used = used
        super().__init__(
            f"Budget ceiling reached for {resource!r}: used {used} (limit {limit})"
        )


@dataclass(frozen=True)
class OperatorCeilings:
    """Hard upper bounds enforced by the gateway operator (R16)."""

    max_recent_turns: int = 50
    max_estimated_input_tokens: int = 128_000
    max_output_token_cap: int = 4_096
    max_model_attempts: int = 32
    max_tool_calls: int = 120
    max_run_wall_clock_seconds: int = 1_800


DEFAULT_OPERATOR_CEILINGS = OperatorCeilings()


def apply_operator_ceilings(
    policy: ContextPolicy,
    ceilings: OperatorCeilings | None = None,
) -> ContextPolicy:
    """Clamp a requested ContextPolicy to the operator ceilings (R16)."""
    c = ceilings or DEFAULT_OPERATOR_CEILINGS
    return ContextPolicy(
        recent_turns=min(max(1, policy.recent_turns), c.max_recent_turns),
        estimated_input_tokens=min(
            max(256, policy.estimated_input_tokens), c.max_estimated_input_tokens
        ),
        output_token_cap=min(
            max(64, policy.output_token_cap), c.max_output_token_cap
        ),
        max_model_attempts=min(
            max(1, policy.max_model_attempts), c.max_model_attempts
        ),
        max_tool_calls=min(
            max(0, policy.max_tool_calls), c.max_tool_calls
        ),
        run_wall_clock_seconds=min(
            max(10, policy.run_wall_clock_seconds), c.max_run_wall_clock_seconds
        ),
    )


def estimate_tokens(text: str) -> int:
    """Deterministic, local, non-billable token estimation (Fix 8).

    Never calls an external provider or tokenizer model. Approximates
    roughly 4 characters per token with minimum 1 token for non-empty text.
    """
    if not text:
        return 0
    # Standard ~4 characters per token heuristic plus word boundary weight
    char_count = len(text)
    words = len(text.split())
    # Blend characters and word counts to give realistic estimates across languages/code
    est = (char_count // 4 + words) // 2
    return max(1, est)


@dataclass
class BudgetLedger:
    """Tracks resource consumption against ContextPolicy during a run (R15, Fix 8).

    Separates estimated tokens from provider-reported tokens:
    - ``estimated_context_tokens`` is a local integer estimate.
    - ``provider_reported_input_tokens`` is ``int | None``; ``None`` represents
      unreported/unknown and is never treated as 0 (Fix 8).
    """

    policy: ContextPolicy
    start_time: float = field(default_factory=time.monotonic)
    model_attempts_used: int = 0
    tool_calls_used: int = 0
    estimated_context_tokens: int = 0
    provider_reported_input_tokens: int | None = None
    provider_reported_output_tokens: int | None = None

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    def check_wall_clock(self, now: float | None = None) -> None:
        elapsed = (time.monotonic() if now is None else now) - self.start_time
        if elapsed > self.policy.run_wall_clock_seconds:
            raise BudgetExceeded(
                "run_wall_clock_seconds", self.policy.run_wall_clock_seconds, elapsed
            )

    def record_model_attempt(
        self,
        *,
        reported_input_tokens: int | None = None,
        reported_output_tokens: int | None = None,
        now: float | None = None,
    ) -> None:
        """Record one model provider attempt and update token accounting."""
        self.check_wall_clock(now=now)
        self.model_attempts_used += 1
        if self.model_attempts_used > self.policy.max_model_attempts:
            raise BudgetExceeded(
                "max_model_attempts",
                self.policy.max_model_attempts,
                self.model_attempts_used,
            )

        if reported_input_tokens is not None:
            if self.provider_reported_input_tokens is None:
                self.provider_reported_input_tokens = reported_input_tokens
            else:
                self.provider_reported_input_tokens += reported_input_tokens

        if reported_output_tokens is not None:
            if self.provider_reported_output_tokens is None:
                self.provider_reported_output_tokens = reported_output_tokens
            else:
                self.provider_reported_output_tokens += reported_output_tokens

    def record_tool_call(self, tool_name: str = "", *, now: float | None = None) -> None:
        """Record one tool invocation."""
        self.check_wall_clock(now=now)
        self.tool_calls_used += 1
        if self.tool_calls_used > self.policy.max_tool_calls:
            raise BudgetExceeded(
                "max_tool_calls",
                self.policy.max_tool_calls,
                self.tool_calls_used,
            )

    def set_estimated_input_tokens(self, estimated: int) -> None:
        self.estimated_context_tokens = estimated
        if estimated > self.policy.estimated_input_tokens:
            raise BudgetExceeded(
                "estimated_input_tokens",
                self.policy.estimated_input_tokens,
                estimated,
            )


@dataclass(frozen=True)
class ContextTokenBreakdown:
    system_tokens: int
    skills_tokens: int
    history_tokens: int
    total_estimated_tokens: int


@dataclass(frozen=True)
class PreparedContext:
    """The formatted, budget-truncated context payload for execution (R08, R16)."""

    system_prompt: str
    skills_instructions: str
    retained_messages: list[dict[str, Any]]
    token_breakdown: ContextTokenBreakdown
    total_turns_provided: int
    turns_retained: int
    truncated: bool


def prepare_context(
    config: ExecutionConfig,
    messages: list[dict[str, Any]],
    skill_definitions: dict[str, str] | None = None,
    *,
    ceilings: OperatorCeilings | None = None,
) -> PreparedContext:
    """Construct the prepared context bounded by the agent's ContextPolicy.

    Applies recent_turns truncation and non-billable token calculation.
    """
    effective_policy = apply_operator_ceilings(config.context, ceilings)
    skills_defs = skill_definitions or {}

    # 1. System prompt (safety prefix + custom prompt)
    safety_prefix = "You are a secure, capable AI assistant."
    system_parts = [safety_prefix]
    if config.prompt.custom_text:
        system_parts.append(config.prompt.custom_text)
    full_system_prompt = "\n\n".join(system_parts)

    # 2. Skill instructions
    skill_blocks: list[str] = []
    for skill in config.skills:
        content = skills_defs.get(skill.id, f"Skill {skill.id} ({skill.source}) active.")
        skill_blocks.append(f"### Skill: {skill.id}\n{content}")
    skills_instructions = "\n\n".join(skill_blocks)

    # 3. Turns & message history truncation
    user_turns = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    total_turns = len(user_turns)

    if total_turns > effective_policy.recent_turns:
        # Retain messages starting from the cutoff user turn
        cutoff_turn_idx = total_turns - effective_policy.recent_turns
        cutoff_msg_idx = user_turns[cutoff_turn_idx]
        retained_messages = list(messages[cutoff_msg_idx:])
        turns_retained = effective_policy.recent_turns
        truncated = True
    else:
        retained_messages = list(messages)
        turns_retained = total_turns
        truncated = False

    # 4. Token estimation
    system_tokens = estimate_tokens(full_system_prompt)
    skills_tokens = estimate_tokens(skills_instructions)
    history_tokens = sum(
        estimate_tokens(str(m.get("content", ""))) for m in retained_messages
    )
    total_tokens = system_tokens + skills_tokens + history_tokens

    breakdown = ContextTokenBreakdown(
        system_tokens=system_tokens,
        skills_tokens=skills_tokens,
        history_tokens=history_tokens,
        total_estimated_tokens=total_tokens,
    )

    return PreparedContext(
        system_prompt=full_system_prompt,
        skills_instructions=skills_instructions,
        retained_messages=retained_messages,
        token_breakdown=breakdown,
        total_turns_provided=total_turns,
        turns_retained=turns_retained,
        truncated=truncated,
    )
