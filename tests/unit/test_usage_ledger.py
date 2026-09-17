"""Unit tests for run-scoped usage accounting (master plan WP1)."""

from decimal import Decimal

from assistant.observability.usage import UsageLedger


def test_usage_counts_each_provider_call_once():
    ledger = UsageLedger()
    for call_id, input_tokens, output_tokens in [("a", 100, 20), ("b", 200, 30)]:
        ledger.record(
            call_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=5,
            cached_input_tokens=0,
            cost_usd=Decimal("0.001"),
        )
    ledger.record(
        "b", input_tokens=200, output_tokens=30, reasoning_tokens=5,
        cached_input_tokens=0, cost_usd=Decimal("0.001"),
    )
    totals = ledger.snapshot()
    assert totals["input_tokens"] == 300
    assert totals["output_tokens"] == 50
    assert totals["reasoning_tokens"] == 10
    assert totals["cost_usd"] == Decimal("0.002")
    assert totals["calls"] == 2


def test_unknown_usage_is_unknown_not_zero():
    ledger = UsageLedger()
    ledger.record("a", input_tokens=100, output_tokens=None, cost_usd=None)
    ledger.record("b", input_tokens=50, output_tokens=10, cost_usd=Decimal("0.01"))
    totals = ledger.snapshot()
    assert totals["input_tokens"] == 150
    assert totals["output_tokens"] == 10
    assert totals["unknown_output_calls"] == 1
    assert totals["unknown_cost_calls"] == 1
    assert totals["cost_usd"] is None


def test_reasoning_is_not_double_counted():
    # Provider-reported output already includes reasoning; the ledger keeps
    # reasoning as its own category and never adds it on top of output.
    ledger = UsageLedger()
    ledger.record("a", input_tokens=10, output_tokens=100, reasoning_tokens=40)
    totals = ledger.snapshot()
    assert totals["output_tokens"] == 100
    assert totals["reasoning_tokens"] == 40


def test_cached_input_reported_separately():
    ledger = UsageLedger()
    ledger.record("a", input_tokens=500, cached_input_tokens=300, output_tokens=10)
    totals = ledger.snapshot()
    assert totals["input_tokens"] == 500
    assert totals["cached_input_tokens"] == 300
