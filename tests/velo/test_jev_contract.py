"""JEV decision-contract tests (Velo spec sections 9 and 18).

The real ``JevDecisionEngine`` runs with ``_classify`` stubbed at the
package boundary: parsing, thresholds, and contract validation are tested
exactly as they will run, without network access.
"""

import re
from types import SimpleNamespace

import pytest

from assistant.velo.jev import (
    JevAnswers,
    JevDecisionEngine,
    text_candidates_from_objective,
)
from assistant.velo.types import (
    JevContractError,
    JevServiceError,
    VeloDecisionStatus,
    VeloObjective,
)
from tests.velo.fakes import observation, target

ALLOWED_OPTION_PREFIXES = (
    "CLICK:",
    "TYPE_USER_TEXT:",
    "PRESS_KEY:",
    "HOTKEY:",
    "SCROLL:",
    "LAUNCH_APP:",
    "OBSERVE",
    "WAIT",
)


def make_engine(
    monkeypatch: pytest.MonkeyPatch, answers: JevAnswers
) -> tuple[JevDecisionEngine, list[tuple[dict, dict[str, str]]]]:
    """Engine with ``_classify`` stubbed; returns captured (state, options)."""
    engine = JevDecisionEngine(api_key="test-key", model="jev-latest")
    captured: list[tuple[dict, dict[str, str]]] = []

    async def _stub(state: dict, options: dict[str, str]) -> JevAnswers:
        captured.append((state, options))
        return answers

    monkeypatch.setattr(engine, "_classify", _stub)
    return engine, captured


SCENE = observation(
    target("tok-1", "Rahul", value="2 unread"),
    target("tok-2", "Sayan"),
)
APPS = {"com.google.Chrome": "Chrome"}


async def test_click_decision_validated_against_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, captured = make_engine(
        monkeypatch,
        JevAnswers(nouls={}, action_option="CLICK:tok-1", probabilities={}, confidence=0.96),
    )

    decision = await engine.decide(
        objective=VeloObjective(text="Open Rahul"),
        observation=SCENE,
        recent_steps=[],
        step=1,
        max_steps=20,
        allowed_apps=APPS,
    )

    assert decision.status is VeloDecisionStatus.ACT
    assert decision.target_id == "tok-1"
    assert decision.confidence == 0.96
    state, options = captured[0]
    assert "CLICK:tok-1" in options
    assert state["observation"]["targets"][0]["id"] == "tok-1"


async def test_unknown_target_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = make_engine(
        monkeypatch,
        JevAnswers(nouls={}, action_option="CLICK:tok-99", probabilities={}, confidence=0.9),
    )

    with pytest.raises(JevContractError):
        await engine.decide(
            objective=VeloObjective(text="Open Rahul"),
            observation=SCENE,
            recent_steps=[],
            step=1,
            max_steps=20,
            allowed_apps=APPS,
        )


async def test_action_outside_schema_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for hostile in ("RUN_SHELL:rm -rf /", "OPEN_URL:https://evil.example", "CLICK"):
        engine, _ = make_engine(
            monkeypatch,
            JevAnswers(nouls={}, action_option=hostile, probabilities={}, confidence=0.99),
        )
        with pytest.raises(JevContractError):
            await engine.decide(
                objective=VeloObjective(text="anything"),
                observation=SCENE,
                recent_steps=[],
                step=1,
                max_steps=20,
                allowed_apps=APPS,
            )


async def test_unknown_key_and_hotkey_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for hostile in ("PRESS_KEY:f13", "HOTKEY:cmd+shift+ctrl+q"):
        engine, _ = make_engine(
            monkeypatch,
            JevAnswers(nouls={}, action_option=hostile, probabilities={}, confidence=0.99),
        )
        with pytest.raises(JevContractError):
            await engine.decide(
                objective=VeloObjective(text="anything"),
                observation=SCENE,
                recent_steps=[],
                step=1,
                max_steps=20,
                allowed_apps=APPS,
            )


async def test_exact_user_text_payload_is_offered_and_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = "I will call you later"
    engine, captured = make_engine(
        monkeypatch,
        JevAnswers(
            nouls={},
            action_option="TYPE_USER_TEXT:user_text_1",
            probabilities={},
            confidence=0.93,
        ),
    )

    decision = await engine.decide(
        objective=VeloObjective(
            text="Send Rahul my message", user_text_payloads={"user_text_1": payload}
        ),
        observation=SCENE,
        recent_steps=[],
        step=3,
        max_steps=20,
        allowed_apps=APPS,
    )

    assert decision.payload_id == "user_text_1"
    state, _ = captured[0]
    # Byte-for-byte: the engine sees the payload; it can only pick its id.
    assert state["exact_user_text_payloads"]["user_text_1"] == payload


async def test_payload_id_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = make_engine(
        monkeypatch,
        JevAnswers(
            nouls={},
            action_option="TYPE_USER_TEXT:user_text_7",
            probabilities={},
            confidence=0.9,
        ),
    )

    with pytest.raises(JevContractError):
        await engine.decide(
            objective=VeloObjective(text="Send Rahul my message"),
            observation=SCENE,
            recent_steps=[],
            step=1,
            max_steps=20,
            allowed_apps=APPS,
        )


async def test_launch_app_must_come_from_the_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, captured = make_engine(
        monkeypatch,
        JevAnswers(
            nouls={},
            action_option="LAUNCH_APP:com.google.Chrome",
            probabilities={},
            confidence=0.9,
        ),
    )
    decision = await engine.decide(
        objective=VeloObjective(text="Open Chrome"),
        observation=SCENE,
        recent_steps=[],
        step=1,
        max_steps=20,
        allowed_apps=APPS,
    )
    assert decision.app_name == "com.google.Chrome"
    assert "LAUNCH_APP:com.google.Chrome" in captured[0][1]

    rogue, _ = make_engine(
        monkeypatch,
        JevAnswers(
            nouls={},
            action_option="LAUNCH_APP:com.apple.Finder",
            probabilities={},
            confidence=0.9,
        ),
    )
    with pytest.raises(JevContractError):
        await rogue.decide(
            objective=VeloObjective(text="Open Finder"),
            observation=SCENE,
            recent_steps=[],
            step=1,
            max_steps=20,
            allowed_apps=APPS,
        )


async def test_done_ask_and_stop_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
        ({"objective_complete": 0.9}, VeloDecisionStatus.DONE),
        ({"needs_user": 0.8}, VeloDecisionStatus.ASK_USER),
        ({"cannot_continue": 0.85}, VeloDecisionStatus.STOP),
    ]
    for nouls, expected in cases:
        engine, _ = make_engine(
            monkeypatch,
            JevAnswers(nouls=nouls, action_option="OBSERVE", probabilities={}, confidence=0.9),
        )
        decision = await engine.decide(
            objective=VeloObjective(text="Open Chrome"),
            observation=SCENE,
            recent_steps=[],
            step=1,
            max_steps=20,
            allowed_apps=APPS,
        )
        assert decision.status is expected, nouls


async def test_stop_wins_over_done_and_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = make_engine(
        monkeypatch,
        JevAnswers(
            nouls={"cannot_continue": 0.9, "needs_user": 0.9, "objective_complete": 0.9},
            action_option="CLICK:tok-1",
            probabilities={},
            confidence=0.9,
        ),
    )
    decision = await engine.decide(
        objective=VeloObjective(text="Open Chrome"),
        observation=SCENE,
        recent_steps=[],
        step=1,
        max_steps=20,
        allowed_apps=APPS,
    )
    assert decision.status is VeloDecisionStatus.STOP


async def test_low_confidence_becomes_ask_user(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = make_engine(
        monkeypatch,
        JevAnswers(nouls={}, action_option="CLICK:tok-1", probabilities={}, confidence=0.2),
    )
    decision = await engine.decide(
        objective=VeloObjective(text="Open Chrome"),
        observation=SCENE,
        recent_steps=[],
        step=1,
        max_steps=20,
        allowed_apps=APPS,
    )
    assert decision.status is VeloDecisionStatus.ASK_USER
    assert "confidence" in decision.reason


async def test_malformed_response_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = JevDecisionEngine(api_key="test-key")

    class BrokenClassifier:
        async def ainvoke(self, _input):  # noqa: ANN001
            return SimpleNamespace(nouls=None, choices={})

    monkeypatch.setattr(engine, "_classifier", BrokenClassifier())
    with pytest.raises(JevContractError):
        await engine.decide(
            objective=VeloObjective(text="Open Chrome"),
            observation=SCENE,
            recent_steps=[],
            step=1,
            max_steps=20,
            allowed_apps=APPS,
        )


async def test_service_errors_map_to_jev_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = JevDecisionEngine(api_key="test-key")

    class DownClassifier:
        async def ainvoke(self, _input):  # noqa: ANN001
            raise RuntimeError("connection refused")

    monkeypatch.setattr(engine, "_classifier", DownClassifier())
    with pytest.raises(JevServiceError):
        await engine.decide(
            objective=VeloObjective(text="Open Chrome"),
            observation=SCENE,
            recent_steps=[],
            step=1,
            max_steps=20,
            allowed_apps=APPS,
        )


async def test_engine_requires_typesafe_key_not_openai_key() -> None:
    with pytest.raises(JevServiceError, match="TYPESAFE_API_KEY"):
        JevDecisionEngine(api_key="")


async def test_choice_question_only_offers_schema_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, captured = make_engine(
        monkeypatch,
        JevAnswers(nouls={}, action_option="OBSERVE", probabilities={}, confidence=0.9),
    )
    objective = VeloObjective(
        text="Open Chrome and search WhatsApp Web",
        user_text_payloads={"user_text_1": "hi"},
        text_candidates=("WhatsApp Web", "Chrome"),
    )
    await engine.decide(
        objective=objective,
        observation=SCENE,
        recent_steps=[],
        step=1,
        max_steps=20,
        allowed_apps=APPS,
    )
    options = captured[0][1]
    assert options, "at least one option is always offered"
    pattern = re.compile(
        r"^(CLICK|TYPE_USER_TEXT|PRESS_KEY|HOTKEY|SCROLL|LAUNCH_APP|OBSERVE|WAIT)(:.+)?$"
    )
    assert all(pattern.match(option) for option in options)
    assert "OBSERVE" in options and "WAIT" in options


def test_text_candidates_are_mechanical_slices() -> None:
    assert text_candidates_from_objective("Open Chrome and search WhatsApp Web") == (
        "WhatsApp Web",
        "Chrome",
    )
    assert text_candidates_from_objective(
        'Open Rahul and send exactly: "I will call you later"'
    ) == ("I will call you later", "Rahul")
    assert text_candidates_from_objective("no triggers here") == ()
