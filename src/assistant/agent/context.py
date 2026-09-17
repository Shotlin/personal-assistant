"""Per-run identity and policy ceilings (spec sections 11.5 and 17)."""

from __future__ import annotations

from pydantic import BaseModel

MAX_CUA_MUTATING_ACTIONS = 50
MAX_RUN_WALL_CLOCK_SECONDS = 15 * 60.0
MAX_MODEL_RETRIES = 2


class AgentContext(BaseModel):
    """Per-invocation identity carried into the agent graph.

    ``user_id`` scopes the long-term memory namespace; ``chat_id`` names
    the conversation for logging. Passed via LangGraph ``context``.
    """

    user_id: str
    chat_id: str
    assistant_id: str = "personal-assistant"


class CuaBudgetExceeded(RuntimeError):
    """Raised when a run reaches the mutating-action ceiling."""


class RunBudget:
    """Counts mutating CUA actions for one request (spec section 17)."""

    def __init__(self, max_actions: int = MAX_CUA_MUTATING_ACTIONS) -> None:
        self._max = max_actions
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self._max - self._used)

    def consume(self, tool_name: str) -> None:
        """Record one mutating action; raise when the ceiling is reached."""
        self._used += 1
        if self._used > self._max:
            raise CuaBudgetExceeded(
                f"Mutating computer-action ceiling reached ({self._max}); "
                f"refusing further actions after {tool_name!r}. Report partial progress."
            )
