"""Tests for Context Policies, Budgets, and Token Vocabulary (P6, R08, R15, R16, Fix 8)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.designer.test_graph import edge, make_graph, node

from assistant.designer.compiler import CONTEXT_DEFAULTS, ContextPolicy, compile_execution_config
from assistant.designer.context import (
    BudgetExceeded,
    BudgetLedger,
    OperatorCeilings,
    apply_operator_ceilings,
    estimate_tokens,
    prepare_context,
)
from assistant.designer.schemas import parse_graph_document


def _sample_graph(custom_prompt: str = "Test instructions", **context_kwargs: int) -> dict:
    ctx_cfg = {**CONTEXT_DEFAULTS, **context_kwargs}
    return make_graph(
        nodes=[
            node("agent", "root"),
            node("model", "node", config={"model_id": "gpt-4o", "provider": "openai"}),
            node("prompt", "node", config={"text": custom_prompt}),
            node("skill", "node", config={"source": "gateway", "id": "search-skill"}),
            node("context", "node", config=ctx_cfg),
        ],
        edges=[
            edge("agent-root", "model-node", "model"),
            edge("agent-root", "prompt-node", "prompt"),
            edge("agent-root", "skill-node", "skill"),
            edge("agent-root", "context-node", "context"),
        ],
    )


# ---------------------------------------------------------------------------
# 1. Operator Ceilings & Token Estimation
# ---------------------------------------------------------------------------


def test_apply_operator_ceilings() -> None:
    custom_ceilings = OperatorCeilings(
        max_recent_turns=20,
        max_estimated_input_tokens=64_000,
        max_output_token_cap=2_048,
        max_model_attempts=10,
        max_tool_calls=30,
        max_run_wall_clock_seconds=600,
    )
    # Requested policy exceeds ceilings
    requested = ContextPolicy(
        recent_turns=100,
        estimated_input_tokens=200_000,
        output_token_cap=8_000,
        max_model_attempts=50,
        max_tool_calls=100,
        run_wall_clock_seconds=3_600,
    )
    clamped = apply_operator_ceilings(requested, custom_ceilings)
    assert clamped.recent_turns == 20
    assert clamped.estimated_input_tokens == 64_000
    assert clamped.output_token_cap == 2_048
    assert clamped.max_model_attempts == 10
    assert clamped.max_tool_calls == 30
    assert clamped.run_wall_clock_seconds == 600


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    short_text = "Hello world"
    est_short = estimate_tokens(short_text)
    assert est_short > 0

    long_text = "The quick brown fox jumps over the lazy dog. " * 50
    est_long = estimate_tokens(long_text)
    assert est_long > est_short * 10


# ---------------------------------------------------------------------------
# 2. BudgetLedger Ceilings and Fix 8 Token Accounting
# ---------------------------------------------------------------------------


def test_budget_ledger_model_attempts_ceiling() -> None:
    policy = ContextPolicy(max_model_attempts=2)
    ledger = BudgetLedger(policy=policy)

    ledger.record_model_attempt()
    assert ledger.model_attempts_used == 1

    ledger.record_model_attempt()
    assert ledger.model_attempts_used == 2

    with pytest.raises(BudgetExceeded) as exc_info:
        ledger.record_model_attempt()
    assert exc_info.value.resource == "max_model_attempts"


def test_budget_ledger_tool_calls_ceiling() -> None:
    policy = ContextPolicy(max_tool_calls=2)
    ledger = BudgetLedger(policy=policy)

    ledger.record_tool_call("tool1")
    ledger.record_tool_call("tool2")
    assert ledger.tool_calls_used == 2

    with pytest.raises(BudgetExceeded) as exc_info:
        ledger.record_tool_call("tool3")
    assert exc_info.value.resource == "max_tool_calls"


def test_budget_ledger_wall_clock_limit() -> None:
    policy = ContextPolicy(run_wall_clock_seconds=10)
    ledger = BudgetLedger(policy=policy, start_time=100.0)

    # Simulated time within limit
    ledger.record_model_attempt(now=105.0)
    assert ledger.model_attempts_used == 1

    # Simulated time past limit
    with pytest.raises(BudgetExceeded) as exc_info:
        ledger.record_model_attempt(now=115.0)
    assert exc_info.value.resource == "run_wall_clock_seconds"


def test_budget_ledger_fix_8_token_vocabulary() -> None:
    """Fix 8: estimated_context_tokens is strictly separated from
    provider_reported_input_tokens, and unknown is None (NOT 0)."""
    policy = ContextPolicy(estimated_input_tokens=10_000)
    ledger = BudgetLedger(policy=policy)

    # Initial state: estimated is 0, provider reported tokens are None (unknown)
    assert ledger.estimated_context_tokens == 0
    assert ledger.provider_reported_input_tokens is None
    assert ledger.provider_reported_output_tokens is None

    # Model attempt without provider reported metrics (provider didn't report)
    ledger.record_model_attempt()
    assert ledger.provider_reported_input_tokens is None
    assert ledger.provider_reported_output_tokens is None

    # Local estimation
    ledger.set_estimated_input_tokens(1_250)
    assert ledger.estimated_context_tokens == 1_250
    # Still None: local estimate never overwrites or conflates with reported
    assert ledger.provider_reported_input_tokens is None

    # Model attempt with reported metrics (e.g. OpenAI usage response)
    ledger.record_model_attempt(reported_input_tokens=1_200, reported_output_tokens=150)
    assert ledger.provider_reported_input_tokens == 1_200
    assert ledger.provider_reported_output_tokens == 150
    assert ledger.estimated_context_tokens == 1_250

    # Accumulation
    ledger.record_model_attempt(reported_input_tokens=500, reported_output_tokens=50)
    assert ledger.provider_reported_input_tokens == 1_700
    assert ledger.provider_reported_output_tokens == 200


def test_budget_ledger_estimated_input_tokens_ceiling() -> None:
    policy = ContextPolicy(estimated_input_tokens=1_000)
    ledger = BudgetLedger(policy=policy)

    ledger.set_estimated_input_tokens(900)
    with pytest.raises(BudgetExceeded) as exc_info:
        ledger.set_estimated_input_tokens(1_500)
    assert exc_info.value.resource == "estimated_input_tokens"


# ---------------------------------------------------------------------------
# 3. PreparedContext Generation
# ---------------------------------------------------------------------------


def test_prepare_context_truncation() -> None:
    # Context policy with 2 recent turns
    graph = parse_graph_document(_sample_graph(recent_turns=2))
    config = compile_execution_config(agent_id="test-agent", revision_id="rev-1", graph=graph)

    # 4 turns of conversation
    messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "reply 1"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "reply 2"},
        {"role": "user", "content": "turn 3"},
        {"role": "assistant", "content": "reply 3"},
        {"role": "user", "content": "turn 4"},
        {"role": "assistant", "content": "reply 4"},
    ]

    prepared = prepare_context(
        config,
        messages,
        skill_definitions={"search-skill": "Documentation for search skill."},
    )

    assert prepared.total_turns_provided == 4
    assert prepared.turns_retained == 2
    assert prepared.truncated is True
    # Retained turns should start at turn 3
    assert len(prepared.retained_messages) == 4
    assert prepared.retained_messages[0]["content"] == "turn 3"
    assert prepared.retained_messages[-1]["content"] == "reply 4"

    assert "Test instructions" in prepared.system_prompt
    assert "Documentation for search skill." in prepared.skills_instructions
    assert prepared.token_breakdown.total_estimated_tokens > 0
    assert prepared.token_breakdown.system_tokens > 0
    assert prepared.token_breakdown.skills_tokens > 0
    assert prepared.token_breakdown.history_tokens > 0


def test_prepare_context_no_truncation() -> None:
    graph = parse_graph_document(_sample_graph(recent_turns=10))
    config = compile_execution_config(agent_id="test-agent", revision_id="rev-1", graph=graph)

    messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "reply 1"},
    ]

    prepared = prepare_context(config, messages)
    assert prepared.total_turns_provided == 1
    assert prepared.turns_retained == 1
    assert prepared.truncated is False
    assert len(prepared.retained_messages) == 2


# ---------------------------------------------------------------------------
# 4. Context Preview API Endpoint
# ---------------------------------------------------------------------------


async def test_context_preview_endpoint(api: AsyncClient) -> None:
    # 1. Create agent
    create_res = await api.post("/designer/api/v1/agents", json={"name": "Context Agent"})
    assert create_res.status_code == 200
    agent = create_res.json()
    agent_id = agent["agent_id"]

    # 2. Preview with graph in payload
    sample_g = _sample_graph(custom_prompt="Custom preview prompt", recent_turns=5)
    preview_res = await api.post(
        f"/designer/api/v1/agents/{agent_id}/context-preview",
        json={
            "graph": sample_g,
            "sample_messages": [
                {"role": "user", "content": "Hello!"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        },
    )
    assert preview_res.status_code == 200
    data = preview_res.json()
    assert data["agent_id"] == agent_id
    assert "Custom preview prompt" in data["system_prompt"]
    assert data["total_turns_provided"] == 1
    assert data["turns_retained"] == 1
    assert data["truncated"] is False
    assert data["token_breakdown"]["total_estimated_tokens"] > 0
    assert data["budget"]["recent_turns_limit"] == 5
    assert data["budget"]["max_model_attempts"] == CONTEXT_DEFAULTS["max_model_attempts"]


async def test_context_preview_requires_actor_permission(anonymous_api: AsyncClient) -> None:
    res = await anonymous_api.post(
        "/designer/api/v1/agents/some-id/context-preview",
        json={"sample_messages": []},
    )
    assert res.status_code == 401
