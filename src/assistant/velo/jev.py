"""JEV decision service -- the ONLY module that knows the JEV provider API.

JEV is a structured classifier, not a text generator (master plan section 5).
One request carries the objective, the resolved target, compact scene facts
and a bounded candidate set built mechanically by the controller; the answer
names one candidate, with confidence and ambiguity information. JEV never
emits shell commands, code, paths, or rewritten dictated text -- payload ids
only -- and a malformed answer raises :class:`JevContractError` and fails
closed. When a required JEV request cannot be made, Velo says so; it never
silently substitutes the general model (master plan section 4).

Provider resolution is ported from the pinned 2026-09-22 integration
(``langchain-typesafe==0.0.1a3``, ``TypeSafeClassifier`` over the System One
contract): ``VELO_PROVIDER=openrouter`` routes through OpenRouter's
``~typesafe/jev-latest``; ``VELO_PROVIDER=typesafe`` goes to the direct
endpoint. Historical logs put single decisions anywhere from hundreds of
milliseconds to multiple seconds, so every decision records its latency and
no code assumes a universal 200 ms answer.
"""

from __future__ import annotations

import logging
import time
import warnings
from typing import Any

from assistant.velo.contracts import (
    DecisionStatus,
    JevContractError,
    JevDecision,
    JevDecisionRequest,
    JevDecisionService,
    JevServiceError,
)

logger = logging.getLogger("assistant.velo.jev")

#: Probability thresholds. Calibrated probabilities are tuned ONLY from real
#: desktop testing (master plan section 5); the defaults are the pinned
#: integration's historical values.
DONE_PROBABILITY = 0.85
ASK_USER_PROBABILITY = 0.70
STOP_PROBABILITY = 0.80
MIN_ACTION_CONFIDENCE = 0.50

#: The API caps a Choice question at 255 criteria; the candidate set is
#: bounded well below that by construction.
_MAX_CHOICE_OPTIONS = 200

#: OpenRouter serves the native System One contract; the pinned client appends
#: /v1/systemone to the base URL itself, so the root stops at /api.
OPENROUTER_BASE_URL = "https://openrouter.ai/api"
OPENROUTER_JEV_MODEL = "~typesafe/jev-latest"
DIRECT_JEV_MODEL = "jev-latest"


class TypeSafeJevService(JevDecisionService):
    """One System One endpoint behind the :class:`JevDecisionService` interface."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DIRECT_JEV_MODEL,
        timeout: float = 30.0,
        base_url: str = "",
    ) -> None:
        if not api_key:
            raise JevServiceError(
                "JEV credential is empty; Velo uses the structured System One "
                "decision API only and never falls back to a free-form LLM, "
                "the Deep Agent, or any other model"
            )
        from langchain_core._api import LangChainBetaWarning
        from langchain_typesafe import TypeSafeClassifier

        with warnings.catch_warnings():
            # The pinned integration is marked beta upstream; the contract
            # tests pin the exact surface Velo depends on.
            warnings.filterwarnings("ignore", category=LangChainBetaWarning)
            classifier_kwargs: dict[str, Any] = {
                "model": model,
                "api_key": api_key,
                "timeout": timeout,
            }
            if base_url:
                # Only a System One-compatible root (Noul/Choice over
                # /v1/systemone); an OpenAI chat-completions surface cannot
                # serve JEV decisions.
                classifier_kwargs["base_url"] = base_url
            self._classifier = TypeSafeClassifier(**classifier_kwargs)
        self.model = model

    @classmethod
    def from_settings(cls, settings: Any) -> TypeSafeJevService:
        """Build from settings, or raise :class:`JevServiceError` when broken.

        ``velo_jev_enabled=False`` is handled by the controller (no service is
        built and Route B is reported unavailable); an *enabled* service with
        missing credentials is a configuration error worth naming.
        """
        provider = settings.velo_provider
        if provider == "openrouter":
            key = settings.openrouter_api_key
            if not key:
                raise JevServiceError(
                    "VELO_PROVIDER=openrouter requires OPENROUTER_API_KEY; "
                    "Velo does not fall back to TypeSafe"
                )
            model = settings.velo_jev_model
            return cls(
                api_key=key,
                model=OPENROUTER_JEV_MODEL if model == DIRECT_JEV_MODEL else model,
                timeout=settings.velo_max_decision_seconds,
                base_url=settings.velo_typesafe_base_url or OPENROUTER_BASE_URL,
            )
        if provider == "typesafe":
            key = settings.typesafe_api_key
            if not key:
                raise JevServiceError(
                    "VELO_PROVIDER=typesafe requires TYPESAFE_API_KEY; "
                    "Velo does not fall back to OpenRouter"
                )
            return cls(
                api_key=key,
                model=settings.velo_jev_model,
                timeout=settings.velo_max_decision_seconds,
                base_url=settings.velo_typesafe_base_url,
            )
        raise JevServiceError(
            f"VELO_PROVIDER must be 'openrouter' or 'typesafe', got {provider!r}"
        )

    async def decide(self, request: JevDecisionRequest) -> JevDecision:
        """One compact request in, one validated decision out."""
        if not request.candidates:
            raise JevContractError("a JEV request without candidates cannot be answered")
        if len(request.candidates) > _MAX_CHOICE_OPTIONS:
            raise JevContractError(
                f"candidate set of {len(request.candidates)} exceeds the API cap"
            )
        started = time.monotonic()
        try:
            answers = await self._classify(request)
        except JevContractError:
            raise
        except JevServiceError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any provider failure is terminal
            raise JevServiceError(f"JEV/TypeSafe request failed: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        decision = self._decision_from(answers, request)
        from dataclasses import replace

        decision = replace(decision, latency_ms=latency_ms)
        logger.info(
            "velo_jev_decision",
            extra={
                "event": "velo_jev_decision",
                "latency_ms": latency_ms,
                "status": decision.status.value,
                "confidence": decision.confidence,
            },
        )
        return decision

    async def _classify(self, request: JevDecisionRequest) -> "JevAnswers":
        from langchain_typesafe import Choice, Noul

        state = self._state(request)
        options = {c.id: c.description for c in request.candidates}
        try:
            response = await self._classifier.ainvoke(
                {
                    "state": state,
                    "questions": {
                        "next_action": Choice(
                            instructions=(
                                "Which single candidate best carries out the user's "
                                "instruction? Pick one option id."
                            ),
                            criteria=options,
                        ),
                        "objective_complete": Noul(
                            instructions=(
                                "Judging only the current state, has the user's "
                                "instruction already been fully carried out?"
                            )
                        ),
                        "needs_user": Noul(
                            instructions=(
                                "Is the instruction too ambiguous to continue safely -- "
                                "several equally plausible targets, a credential gate, "
                                "or a choice only the user can resolve?"
                            )
                        ),
                        "cannot_continue": Noul(
                            instructions=(
                                "Is the instruction impossible to carry out from the "
                                "current state after a fresh look?"
                            )
                        ),
                    },
                }
            )
        except JevContractError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any API failure is terminal
            raise JevServiceError(f"JEV/TypeSafe request failed: {exc}") from exc
        try:
            nouls = {name: float(answer.noul) for name, answer in response.nouls.items()}
            action_answer = response.choices["next_action"]
        except (AttributeError, KeyError, TypeError) as exc:
            raise JevContractError(f"unrecognized JEV response shape: {exc}") from exc
        return JevAnswers(
            nouls=nouls,
            action_option=str(action_answer.choice),
            probabilities={
                str(key): float(value)
                for key, value in (action_answer.probabilities or {}).items()
            },
            confidence=float(action_answer.confidence or 0.0),
        )

    @staticmethod
    def _state(request: JevDecisionRequest) -> dict[str, Any]:
        """Compact state: facts, not screenshots; ids, not generated text."""
        return {
            "objective": request.objective,
            "requested_app": request.requested_app,
            "target": request.target_summary,
            "scene_facts": list(request.scene_facts),
            "exact_user_text_payload_ids": list(request.payload_ids),
            "last_outcome": request.last_outcome,
            "why_a_decision_is_needed": request.reason,
        }

    def _decision_from(
        self, answers: "JevAnswers", request: JevDecisionRequest
    ) -> JevDecision:
        """Map classifier answers onto the decision contract, validated."""
        valid_ids = {candidate.id for candidate in request.candidates}
        nouls = answers.nouls
        if nouls.get("cannot_continue", 0.0) >= STOP_PROBABILITY:
            return JevDecision(
                status=DecisionStatus.STOP,
                confidence=nouls["cannot_continue"],
                applies_to_version=request.task_version,
            )
        if nouls.get("needs_user", 0.0) >= ASK_USER_PROBABILITY:
            return JevDecision(
                status=DecisionStatus.ASK_USER,
                confidence=nouls["needs_user"],
                applies_to_version=request.task_version,
                need_user_choice=True,
            )
        selected = answers.action_option
        if selected not in valid_ids:
            raise JevContractError(
                f"JEV selected {selected!r}, which is not one of the offered candidates"
            )
        if nouls.get("objective_complete", 0.0) >= DONE_PROBABILITY:
            # A completion claim still has to survive the controller's
            # postcondition check; a probability never overrides evidence.
            return JevDecision(
                status=DecisionStatus.DONE,
                confidence=nouls["objective_complete"],
                applies_to_version=request.task_version,
            )
        if answers.confidence < MIN_ACTION_CONFIDENCE:
            # Low confidence is a reason to gather better evidence first --
            # not to ask the user a question the screen could answer.
            return JevDecision(
                status=DecisionStatus.ACT,
                selected_id=selected,
                confidence=answers.confidence,
                applies_to_version=request.task_version,
                need_more_evidence=True,
            )
        return JevDecision(
            status=DecisionStatus.ACT,
            selected_id=selected,
            confidence=answers.confidence,
            applies_to_version=request.task_version,
        )


class JevAnswers:
    """Package-independent view of one classifier response."""

    def __init__(
        self,
        *,
        nouls: dict[str, float],
        action_option: str,
        probabilities: dict[str, float],
        confidence: float,
    ) -> None:
        self.nouls = nouls
        self.action_option = action_option
        self.probabilities = probabilities
        self.confidence = confidence


__all__ = [
    "DIRECT_JEV_MODEL",
    "DONE_PROBABILITY",
    "ASK_USER_PROBABILITY",
    "MIN_ACTION_CONFIDENCE",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_JEV_MODEL",
    "STOP_PROBABILITY",
    "TypeSafeJevService",
]
