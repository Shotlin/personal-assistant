"""Run-scoped usage accounting (master plan WP1 / section 9.2).

Records every provider request exactly once, keyed by a unique call id.
Unknown values are preserved as unknown -- never folded into zero. The
ledger aggregates only the CURRENT run; it never rescans past messages.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger("assistant.observability.usage")

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens")


class UsageLedger:
    """Accumulate per-call provider usage for one run."""

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, int | Decimal | None]] = {}

    def record(
        self,
        call_id: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        cost_usd: Decimal | None = None,
    ) -> None:
        """Record one provider call. The first record for a call id wins."""
        if call_id in self._calls:
            logger.debug("usage_duplicate_ignored", extra={"call_id": call_id})
            return
        self._calls[call_id] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cost_usd": cost_usd,
        }

    @property
    def call_count(self) -> int:
        return len(self._calls)

    def snapshot(self) -> dict[str, Any]:
        """Current-run totals.

        Token totals sum the known values; calls with an unknown value are
        counted separately (unknown usage is not zero). ``cost_usd`` is a
        Decimal sum when every call reported a cost, otherwise ``None``.
        """
        totals: dict[str, Any] = {
            "calls": len(self._calls),
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_input_tokens": 0,
            "cost_usd": Decimal("0"),
        }
        unknown_counters = {
            "input_tokens": "unknown_input_calls",
            "output_tokens": "unknown_output_calls",
            "reasoning_tokens": "unknown_reasoning_calls",
            "cached_input_tokens": "unknown_cached_input_calls",
        }
        for counter in unknown_counters.values():
            totals[counter] = 0
        totals["unknown_cost_calls"] = 0

        cost_known = True
        for record in self._calls.values():
            for field, counter in unknown_counters.items():
                value = record[field]
                if isinstance(value, int):
                    totals[field] += value
                else:
                    totals[counter] += 1
            cost = record["cost_usd"]
            if isinstance(cost, Decimal):
                totals["cost_usd"] += cost
            else:
                cost_known = False
                totals["unknown_cost_calls"] += 1

        if not cost_known:
            totals["cost_usd"] = None
        return totals


class LedgerCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler feeding one run's :class:`UsageLedger`.

    Attached at the provider boundary so usage is aggregated from actual
    provider responses (never by rescanning graph messages). Call ids are
    ``{prefix}-{n}`` in completion order.
    """

    def __init__(self, ledger: UsageLedger, prefix: str) -> None:
        self.ledger = ledger
        self.prefix = prefix
        self._sequence = 0

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:  # noqa: ANN003
        self._sequence += 1
        call_id = f"{self.prefix}-{self._sequence}"

        input_tokens = output_tokens = reasoning_tokens = cached = None
        llm_output = getattr(response, "llm_output", None) or {}
        token_usage = llm_output.get("token_usage") or {}
        if isinstance(token_usage, dict):
            input_tokens = token_usage.get("prompt_tokens")
            output_tokens = token_usage.get("completion_tokens")
            reasoning_raw = token_usage.get("reasoning_tokens")
            reasoning_tokens = int(reasoning_raw) if isinstance(reasoning_raw, int) else None

        # Prefer the richer per-generation usage metadata when present.
        for generation in getattr(response, "generations", []) or []:
            for gen in generation or []:
                meta = getattr(gen, "usage_metadata", None)
                message_obj = getattr(gen, "message", None)
                if meta is None and message_obj is not None:
                    meta = getattr(message_obj, "usage_metadata", None)
                if isinstance(meta, dict):
                    input_tokens = int(meta.get("input_tokens") or 0) or None
                    output_tokens = int(meta.get("output_tokens") or 0) or None
                    details = meta.get("output_token_details") or {}
                    reasoning_raw = details.get("reasoning")
                    if isinstance(reasoning_raw, int):
                        reasoning_tokens = reasoning_raw
                    in_details = meta.get("input_token_details") or {}
                    cached_raw = in_details.get("cache_read")
                    if isinstance(cached_raw, int):
                        cached = cached_raw
                    break

        self.ledger.record(
            call_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_input_tokens=cached,
            cost_usd=None,  # provider cost stays unknown until reconciled
        )
