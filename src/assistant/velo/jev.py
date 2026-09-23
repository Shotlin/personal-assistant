"""JEV decision engine -- the ONLY module that knows the JEV provider API.

Pinned integration (tested 2026-09-22): ``langchain-typesafe==0.0.1a3``
(the LangChain integration from the "Building a harness with JEV" blog
post), whose ``TypeSafeClassifier`` is a LangChain ``Runnable`` over the
TypeSafe System One contract. One request carries a ``Choice`` question
(which constrained action to take) and three ``Noul`` questions
(objective complete? ask the user? impossible to continue?).

Provider resolution (Sani master doc "OpenRouter only"): the SAME
System One contract is reachable two ways --
- ``OPENROUTER_API_KEY`` -> ``https://openrouter.ai/api/v1/systemone``
  with model ``~typesafe/jev-latest`` (one-key product story; the model
  route was not yet publicly listed on 2026-09-22, so verify on first use);
- ``TYPESAFE_API_KEY`` -> ``https://api.typesafe.ai`` with model
  ``jev-latest`` (verified working end to end).
``VELO_TYPESAFE_BASE_URL`` overrides both for any System One-compatible
proxy. Provider-specific logic lives ONLY here; the Velo agent loop never
changes with the provider.

JEV is a classifier, not a text generator: every decision option below is
built MECHANICALLY from bounded evidence -- observed element tokens,
stored user payloads, slices of the objective text, a fixed key/hotkey
table, and the capability-manifest app allowlist. JEV selects among them
with probabilities; it can never emit shell commands, code, paths, or any
action outside the Velo schema. Malformed or out-of-contract answers raise
:class:`JevContractError` and the run fails closed -- never a fallback to
a free-form LLM, the Deep Agent, or any other model (Velo spec sections
2, 9, 15).
"""

from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from assistant.velo.types import (
    JevContractError,
    JevServiceError,
    VeloActionKind,
    VeloDecision,
    VeloDecisionStatus,
    VeloObjective,
    VeloObservation,
)

if TYPE_CHECKING:  # pragma: no cover
    from assistant.settings import Settings

logger = logging.getLogger("assistant.velo.jev")

#: Import happens once at module load; every JEV-specific import stays here.
from langchain_typesafe import Choice, Noul, TypeSafeClassifier  # noqa: E402

#: Probability thresholds. Jev probabilities are calibrated; tune ONLY from
#: real desktop testing (Velo spec section 14).
DONE_PROBABILITY = 0.85
ASK_USER_PROBABILITY = 0.70
STOP_PROBABILITY = 0.80
#: Below this Choice confidence the state is treated as a guess -> ASK_USER.
MIN_ACTION_CONFIDENCE = 0.50

#: API cap is 255 criteria; stay well below and keep the option list small.
_MAX_CHOICE_OPTIONS = 200

#: Fixed, bounded key/hotkey candidates (Velo spec section 9: JEV never
#: emits arbitrary key names or combos).
KEY_CANDIDATES: dict[str, str] = {
    "return": "Press Return/Enter to submit or confirm.",
    "escape": "Press Escape to dismiss a dialog or popup.",
    "tab": "Press Tab to move focus to the next field.",
    "space": "Press Space to toggle or activate the focused control.",
}
HOTKEY_CANDIDATES: dict[str, str] = {
    "cmd+l": "Focus the address/search bar in the browser.",
    "cmd+t": "Open a new browser tab.",
    "cmd+w": "Close the current browser tab.",
    "cmd+enter": "Send the composed message (common chat apps).",
}
SCROLL_DIRECTIONS: dict[str, str] = {
    "down": "Scroll the view down to reveal more content.",
    "up": "Scroll the view up to reveal earlier content.",
}

#: Objective spans after these triggers become typed-text candidates.
_TEXT_TRIGGERS = (
    "search for",
    "search",
    "look up",
    "type",
    "enter",
    "go to",
    "open",
)
_QUOTED_SPAN = re.compile(r"[\"'“”‘’](.+?)[\"'“”‘’]")
_MAX_TEXT_CANDIDATES = 8
_MAX_TEXT_CANDIDATE_LENGTH = 80


@dataclass(frozen=True)
class JevAnswers:
    """Package-independent view of one classifier response."""

    nouls: dict[str, float]
    action_option: str
    probabilities: dict[str, float]
    confidence: float


#: OpenRouter serves the native System One contract (POST /api/v1/systemone,
#: verified to route on 2026-09-22). The pinned client appends /v1/systemone
#: to the base URL itself, so the OpenRouter root stops at /api -- otherwise
#: the request double-prefixes /v1 (caught live). The typesafe model route
#: was not yet in the public catalog that day -- re-verify on first live use.
OPENROUTER_BASE_URL = "https://openrouter.ai/api"
OPENROUTER_JEV_MODEL = "~typesafe/jev-latest"

#: TypeSafe's direct model route and the default VELO_JEV_MODEL value.
DIRECT_JEV_MODEL = "jev-latest"


@dataclass(frozen=True)
class JevProvider:
    """Resolved JEV endpoint: one credential, one System One base URL."""

    api_key: str
    base_url: str  # "" = the pinned package default (api.typesafe.ai)
    model: str
    source: str  # typesafe_direct | openrouter | manual_override


def resolve_jev_provider(settings: Settings) -> JevProvider:
    """Pick the explicitly selected JEV endpoint from settings.

    ``VELO_PROVIDER`` is the authority even when both credentials are in the
    child environment (Deep Agent independently uses OpenRouter). Missing or
    invalid selected credentials fail closed; JEV never falls back.
    """
    if settings.velo_provider == "openrouter":
        key = settings.openrouter_api_key
    elif settings.velo_provider == "typesafe":
        key = settings.typesafe_api_key
    else:
        raise JevServiceError(f"Unsupported VELO_PROVIDER {settings.velo_provider!r}")

    if settings.velo_typesafe_base_url:
        if not key:
            raise JevServiceError(
                f"VELO_TYPESAFE_BASE_URL is set but VELO_PROVIDER={settings.velo_provider} "
                "has no configured credential"
            )
        return JevProvider(
            api_key=key,
            base_url=settings.velo_typesafe_base_url,
            model=settings.velo_jev_model,
            source="manual_override",
        )
    if settings.velo_provider == "openrouter":
        if not key:
            raise JevServiceError(
                "VELO_PROVIDER=openrouter requires OPENROUTER_API_KEY; "
                "Velo does not fall back to TypeSafe"
            )
        # The default direct-route name is not a valid OpenRouter model id;
        # map it to OpenRouter's typesafe namespace.
        model = settings.velo_jev_model
        if model == DIRECT_JEV_MODEL:
            model = OPENROUTER_JEV_MODEL
        return JevProvider(
            api_key=key,
            base_url=OPENROUTER_BASE_URL,
            model=model,
            source="openrouter",
        )
    if not key:
        raise JevServiceError(
            "VELO_PROVIDER=typesafe requires TYPESAFE_API_KEY; "
            "Velo does not fall back to OpenRouter"
        )
    return JevProvider(
        api_key=key,
        base_url="",
        model=settings.velo_jev_model,
        source="typesafe_direct",
    )


class JevDecisionEngine:
    """Compact state in, one validated :class:`VeloDecision` out."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "jev-latest",
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

        with warnings.catch_warnings():
            # The pinned 0.0.1a3 integration is marked beta upstream; the
            # contract tests pin the exact surface Velo depends on.
            warnings.filterwarnings("ignore", category=LangChainBetaWarning)
            classifier_kwargs: dict[str, Any] = {
                "model": model,
                "api_key": api_key,
                "timeout": timeout,
            }
            if base_url:
                # Only a System One-compatible root (Noul/Choice/Score over
                # /v1/systemone); an OpenAI chat-completions surface cannot
                # serve JEV decisions.
                classifier_kwargs["base_url"] = base_url
            self._classifier = TypeSafeClassifier(**classifier_kwargs)
        self.model = model

    @classmethod
    def from_settings(cls, settings: Settings, *, timeout: float = 30.0) -> JevDecisionEngine:
        """Build the engine from the resolved provider (Sani Stage B)."""
        provider = resolve_jev_provider(settings)
        logger.info(
            "velo_jev_provider_resolved",
            extra={"event": "velo_jev_provider_resolved", "source": provider.source},
        )
        return cls(
            api_key=provider.api_key,
            model=provider.model,
            timeout=timeout,
            base_url=provider.base_url,
        )

    async def decide(
        self,
        *,
        objective: VeloObjective,
        observation: VeloObservation,
        recent_steps: list[dict[str, str]],
        step: int,
        max_steps: int,
        allowed_apps: dict[str, str],
    ) -> VeloDecision:
        """Ask one JEV request; validate the answer into a VeloDecision."""
        state = self._state(objective, observation, recent_steps, step, max_steps)
        options = self._action_options(objective, observation, allowed_apps)
        try:
            answers = await self._classify(state, options)
        except JevContractError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any API failure is terminal
            raise JevServiceError(f"JEV/TypeSafe request failed: {exc}") from exc
        decision = self._decision_from(answers)
        return _validate_decision(decision, objective, observation, allowed_apps)

    # ------------------------------------------------------------------
    # package boundary (the only place the classifier is invoked)
    # ------------------------------------------------------------------

    async def _classify(self, state: dict[str, Any], options: dict[str, str]) -> JevAnswers:
        response = await self._classifier.ainvoke(
            {
                "state": state,
                "questions": {
                    "next_action": Choice(
                        instructions=(
                            "Which single next action best progresses the user "
                            "objective in the state? Pick one option id."
                        ),
                        criteria=options,
                    ),
                    "objective_complete": Noul(
                        instructions=(
                            "Judging only the current state, has the user "
                            "objective already been fully achieved?"
                        )
                    ),
                    "needs_user": Noul(
                        instructions=(
                            "Is the state too ambiguous to continue safely -- "
                            "e.g. several equally plausible targets, a "
                            "credential/login gate, or a confirmation dialog "
                            "that only the user can resolve?"
                        )
                    ),
                    "cannot_continue": Noul(
                        instructions=(
                            "Is the objective impossible to achieve from the "
                            "current state -- the requested target simply is "
                            "not available after a fresh observation?"
                        )
                    ),
                },
            }
        )
        try:
            nouls = {name: float(answer.noul) for name, answer in response.nouls.items()}
            action_answer = response.choices["next_action"]
        except (AttributeError, KeyError, TypeError) as exc:
            raise JevContractError(f"unrecognized JEV response shape: {exc}") from exc
        return JevAnswers(
            nouls=nouls,
            action_option=str(action_answer.choice),
            probabilities={
                str(key): float(value) for key, value in (action_answer.probabilities or {}).items()
            },
            confidence=float(action_answer.confidence or 0.0),
        )

    # ------------------------------------------------------------------
    # compact state (Velo spec sections 7-8)
    # ------------------------------------------------------------------

    def _state(
        self,
        objective: VeloObjective,
        observation: VeloObservation,
        recent_steps: list[dict[str, str]],
        step: int,
        max_steps: int,
    ) -> dict[str, Any]:
        payloads: dict[str, str] = {}
        for payload_id, text in objective.user_text_payloads.items():
            payloads[payload_id] = text
        for position, candidate in enumerate(objective.text_candidates, start=1):
            payloads[f"text_{position}"] = candidate
        return {
            "objective": objective.text,
            "exact_user_text_payloads": payloads,
            "step": step,
            "max_steps": max_steps,
            "observation": observation.compact(),
            "recent_steps": recent_steps,
        }

    # ------------------------------------------------------------------
    # mechanical option building (no reasoning here, no rules engine)
    # ------------------------------------------------------------------

    def _action_options(
        self,
        objective: VeloObjective,
        observation: VeloObservation,
        allowed_apps: dict[str, str],
    ) -> dict[str, str]:
        options: dict[str, str] = {}

        for target in observation.targets:
            if not target.element_token:
                continue
            description = f"Click the {target.role}"
            description += f" labeled '{target.label}'" if target.label else ""
            if target.value:
                description += f" (current value: {target.value})"
            options[f"CLICK:{target.id}"] = description
            if len(options) >= _MAX_CHOICE_OPTIONS:
                return self._with_universal_options(options)

        for payload_id, text in objective.user_text_payloads.items():
            options[f"TYPE_USER_TEXT:{payload_id}"] = (
                f"Type the stored exact user text '{text}' into the focused "
                "field, byte for byte, without any change."
            )
        for position, candidate in enumerate(objective.text_candidates, start=1):
            options[f"TYPE_USER_TEXT:text_{position}"] = (
                f"Type the exact objective phrase '{candidate}' into the "
                "focused field, byte for byte, without any change."
            )

        for bundle_id, name in allowed_apps.items():
            options[f"LAUNCH_APP:{bundle_id}"] = f"Launch the {name} application."

        for key, description in KEY_CANDIDATES.items():
            options[f"PRESS_KEY:{key}"] = description
        for combo, description in HOTKEY_CANDIDATES.items():
            options[f"HOTKEY:{combo}"] = description
        for direction, description in SCROLL_DIRECTIONS.items():
            options[f"SCROLL:{direction}"] = description
        return self._with_universal_options(options)

    def _with_universal_options(self, options: dict[str, str]) -> dict[str, str]:
        options.setdefault(
            "OBSERVE", "Re-observe the screen without acting (state may have changed)."
        )
        options.setdefault("WAIT", "Wait briefly for an app to settle, then re-observe.")
        return dict(list(options.items())[:_MAX_CHOICE_OPTIONS])

    # ------------------------------------------------------------------
    # contract validation (Velo spec section 9)
    # ------------------------------------------------------------------

    def _decision_from(self, answers: JevAnswers) -> VeloDecision:
        stop_p = answers.nouls.get("cannot_continue", 0.0)
        ask_p = answers.nouls.get("needs_user", 0.0)
        done_p = answers.nouls.get("objective_complete", 0.0)

        if stop_p >= STOP_PROBABILITY:
            return VeloDecision(
                status=VeloDecisionStatus.STOP,
                confidence=stop_p,
                reason="JEV: objective impossible from the current state",
            )
        if ask_p >= ASK_USER_PROBABILITY:
            return VeloDecision(
                status=VeloDecisionStatus.ASK_USER,
                confidence=ask_p,
                reason="JEV: state too ambiguous to continue safely",
            )
        if done_p >= DONE_PROBABILITY:
            return VeloDecision(
                status=VeloDecisionStatus.DONE,
                confidence=done_p,
                reason="JEV: objective fully achieved in the current state",
            )

        option = answers.action_option.strip()
        if answers.confidence < MIN_ACTION_CONFIDENCE:
            return VeloDecision(
                status=VeloDecisionStatus.ASK_USER,
                confidence=answers.confidence,
                reason=(
                    f"JEV action confidence {answers.confidence:.2f} below "
                    f"{MIN_ACTION_CONFIDENCE}; refusing to guess"
                ),
            )
        return self._parse_action_option(option, answers)

    def _parse_action_option(self, option: str, answers: JevAnswers) -> VeloDecision:
        action_name, _, argument = option.partition(":")
        action_name = action_name.strip().upper()
        argument = argument.strip()
        try:
            action = VeloActionKind(action_name)
        except ValueError as exc:
            raise JevContractError(
                f"JEV proposed action outside the Velo schema: {option!r}"
            ) from exc
        if action not in {
            VeloActionKind.LAUNCH_APP,
            VeloActionKind.CLICK,
            VeloActionKind.TYPE_USER_TEXT,
            VeloActionKind.PRESS_KEY,
            VeloActionKind.HOTKEY,
            VeloActionKind.SCROLL,
            VeloActionKind.OBSERVE,
            VeloActionKind.WAIT,
        }:
            raise JevContractError(
                f"JEV proposed terminal action through the choice question: {option!r}"
            )
        decision = VeloDecision(
            status=VeloDecisionStatus.ACT,
            action=action,
            confidence=answers.confidence,
        )
        if action is VeloActionKind.CLICK:
            decision = replace(decision, target_id=_required_argument(option, argument))
        elif action is VeloActionKind.TYPE_USER_TEXT:
            decision = replace(decision, payload_id=_required_argument(option, argument))
        elif action is VeloActionKind.PRESS_KEY:
            key = _required_argument(option, argument)
            if key not in KEY_CANDIDATES:
                raise JevContractError(f"JEV proposed unknown key: {option!r}")
            decision = replace(decision, key=key)
        elif action is VeloActionKind.HOTKEY:
            combo = _required_argument(option, argument)
            if combo not in HOTKEY_CANDIDATES:
                raise JevContractError(f"JEV proposed unknown hotkey: {option!r}")
            decision = replace(decision, combo=combo)
        elif action is VeloActionKind.SCROLL:
            direction = _required_argument(option, argument)
            if direction not in SCROLL_DIRECTIONS:
                raise JevContractError(f"JEV proposed unknown scroll direction: {option!r}")
            decision = replace(decision, direction=direction)
        elif action is VeloActionKind.LAUNCH_APP:
            decision = replace(decision, app_name=_required_argument(option, argument))
        return decision


def _required_argument(option: str, argument: str) -> str:
    if not argument:
        raise JevContractError(f"JEV action option is missing its argument: {option!r}")
    return argument


def _validate_decision(
    decision: VeloDecision,
    objective: VeloObjective,
    observation: VeloObservation,
    allowed_apps: dict[str, str],
) -> VeloDecision:
    """Reject ids that were never offered as options (Velo spec section 18).

    The executor re-checks mechanically; failing here keeps malformed JEV
    answers from ever reaching CUA.
    """
    if decision.status is not VeloDecisionStatus.ACT or decision.action is None:
        return decision
    if decision.action is VeloActionKind.CLICK and observation.target(decision.target_id) is None:
        raise JevContractError(f"JEV referenced unknown target id: {decision.target_id!r}")
    if (
        decision.action is VeloActionKind.TYPE_USER_TEXT
        and objective.payload_text(decision.payload_id) is None
    ):
        raise JevContractError(f"JEV referenced unknown payload id: {decision.payload_id!r}")
    if (
        decision.action is VeloActionKind.LAUNCH_APP
        and _resolve_app(decision.app_name, allowed_apps) is None
    ):
        raise JevContractError(f"JEV referenced app outside the allowlist: {decision.app_name!r}")
    return decision


def _resolve_app(app_name: str, allowed_apps: dict[str, str]) -> str | None:
    if app_name in allowed_apps:
        return app_name
    lowered = app_name.lower()
    for bundle_id, name in allowed_apps.items():
        if name.lower() == lowered or bundle_id.lower() == lowered:
            return bundle_id
    return None


def text_candidates_from_objective(objective_text: str) -> tuple[str, ...]:
    """Mechanically slice candidate typed-text spans out of the objective.

    Quoted spans first, then spans after trigger words. This is data
    extraction from the user's own sentence -- never generation -- and it
    is what makes flows like 'Open Chrome and search WhatsApp Web' typeable
    while keeping JEV a classifier (Velo spec section 23, user text rule).
    """
    candidates: list[str] = []

    for match in _QUOTED_SPAN.finditer(objective_text):
        _append_candidate(candidates, match.group(1))

    lowered = objective_text.lower()
    for trigger in sorted(_TEXT_TRIGGERS, key=len, reverse=True):
        start = 0
        while True:
            index = lowered.find(trigger, start)
            if index < 0:
                break
            remainder = objective_text[index + len(trigger) :].strip(" :,-")
            if remainder.lower().startswith("for "):
                remainder = remainder[4:].strip(" :,-")
            remainder = re.split(r"\.\s", remainder, maxsplit=1)[0]
            remainder = remainder.split(" and ", 1)[0].strip(" :,-")
            remainder = remainder.rstrip(".!?")
            _append_candidate(candidates, remainder)
            start = index + len(trigger)

    return tuple(candidates[:_MAX_TEXT_CANDIDATES])


def _append_candidate(candidates: list[str], span: str) -> None:
    span = span.strip()
    if not span or len(span) > _MAX_TEXT_CANDIDATE_LENGTH:
        return
    if span.lower() in {candidate.lower() for candidate in candidates}:
        return
    candidates.append(span)
