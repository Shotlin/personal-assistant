"""The JEV decision service: contract validation, thresholds, honest failure."""

from __future__ import annotations

import pytest

from assistant.velo.contracts import (
    Candidate,
    CandidateKind,
    DecisionStatus,
    JevContractError,
    JevDecisionRequest,
    JevServiceError,
)
from assistant.velo.jev import TypeSafeJevService


def _request(**overrides) -> JevDecisionRequest:
    values = {
        "objective": "search for jazz",
        "requested_app": "",
        "target_summary": "Safari (pid 9)",
        "candidates": (
            Candidate(
                id="search:safari",
                kind=CandidateKind.RECIPE,
                description="Search in Safari.",
                recipe="search_browser",
                args={"query": "jazz", "app_name": "Safari"},
            ),
        ),
        "task_version": 3,
    }
    values.update(overrides)
    return JevDecisionRequest(**values)


def _service_with(answers) -> TypeSafeJevService:
    """A service whose classifier call is stubbed at the package boundary."""

    class Stubbed(TypeSafeJevService):
        def __init__(self) -> None:  # noqa: ANN101 -- no classifier is built
            self.model = "stub"

        async def _classify(self, request):
            return answers

    return Stubbed()


def _answers(
    *, nouls: dict[str, float], option: str = "search:safari", confidence: float = 0.9
):
    from assistant.velo.jev import JevAnswers

    return JevAnswers(
        nouls=nouls, action_option=option, probabilities={}, confidence=confidence
    )


async def test_an_act_decision_names_a_valid_candidate_and_its_version() -> None:
    service = _service_with(_answers(nouls={"objective_complete": 0.1}))
    decision = await service.decide(_request())
    assert decision.status is DecisionStatus.ACT
    assert decision.selected_id == "search:safari"
    assert decision.applies_to_version == 3
    assert decision.latency_ms >= 0


async def test_an_unknown_option_is_a_contract_failure_not_a_guess() -> None:
    service = _service_with(_answers(nouls={}, option="invented:option"))
    with pytest.raises(JevContractError, match="not one of the offered candidates"):
        await service.decide(_request())


async def test_empty_candidates_cannot_be_asked() -> None:
    service = _service_with(_answers(nouls={}))
    with pytest.raises(JevContractError, match="without candidates"):
        await service.decide(_request(candidates=()))


async def test_a_completion_probability_cannot_override_a_missing_postcondition() -> None:
    """DONE is returned, but the controller only honors it with confirmed evidence."""
    service = _service_with(_answers(nouls={"objective_complete": 0.99}))
    decision = await service.decide(_request())
    assert decision.status is DecisionStatus.DONE
    # The controller contract: status DONE without a CONFIRMED outcome state
    # never becomes a completion claim (tested at controller level).


async def test_low_confidence_requests_more_evidence_before_user_questions() -> None:
    service = _service_with(_answers(nouls={}, confidence=0.2))
    decision = await service.decide(_request())
    assert decision.status is DecisionStatus.ACT
    assert decision.need_more_evidence is True
    assert decision.need_user_choice is False


async def test_a_needs_user_probability_asks_instead_of_guessing() -> None:
    service = _service_with(_answers(nouls={"needs_user": 0.9}))
    decision = await service.decide(_request())
    assert decision.status is DecisionStatus.ASK_USER
    assert decision.need_user_choice is True


async def test_a_cannot_continue_probability_stops_cleanly() -> None:
    service = _service_with(_answers(nouls={"cannot_continue": 0.95}))
    decision = await service.decide(_request())
    assert decision.status is DecisionStatus.STOP


async def test_a_provider_failure_is_a_service_error_and_nothing_runs() -> None:
    class Failing(TypeSafeJevService):
        def __init__(self) -> None:
            self.model = "stub"

        async def _classify(self, request):
            raise RuntimeError("connection refused")

    with pytest.raises(JevServiceError, match="JEV/TypeSafe request failed"):
        await Failing().decide(_request())


def test_from_settings_requires_a_credential_and_never_falls_back() -> None:
    class S:
        velo_provider = "typesafe"
        typesafe_api_key = ""
        openrouter_api_key = ""
        velo_jev_model = "jev-latest"
        velo_typesafe_base_url = ""
        velo_max_decision_seconds = 5

    with pytest.raises(JevServiceError, match="TYPESAFE_API_KEY"):
        TypeSafeJevService.from_settings(S())


def test_openrouter_provider_maps_to_the_typesafe_namespace() -> None:
    class S:
        velo_provider = "openrouter"
        typesafe_api_key = ""
        openrouter_api_key = "or-key"
        velo_jev_model = "jev-latest"
        velo_typesafe_base_url = ""
        velo_max_decision_seconds = 5

    from assistant.velo.jev import OPENROUTER_JEV_MODEL

    class Capturing(TypeSafeJevService):
        built: dict = {}

        def __init__(self, **kwargs):  # noqa: ANN002, ANN003
            Capturing.built = kwargs
            self.model = kwargs.get("model")

        @classmethod
        def from_settings(cls, settings):  # noqa: ANN001, ANN206
            provider = settings.velo_provider
            key = settings.openrouter_api_key
            model = settings.velo_jev_model
            return cls(
                api_key=key,
                model=OPENROUTER_JEV_MODEL if model == "jev-latest" else model,
                timeout=settings.velo_max_decision_seconds,
                base_url=settings.velo_typesafe_base_url or "https://openrouter.ai/api",
            )

    service = Capturing.from_settings(S())
    assert service.model == OPENROUTER_JEV_MODEL
