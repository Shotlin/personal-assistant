"""Agent-loop tests with a mock CUA and scripted JEV (Velo spec section 18)."""

from assistant.velo.agent import VeloAgent
from assistant.velo.types import VeloActionKind, VeloLimits, VeloObjective, VeloStatus
from tests.velo.fakes import (
    FakeCua,
    ScriptedJev,
    act,
    ask_user,
    done,
    observation,
    target,
)


def build_agent(fake_cua: FakeCua, jev: ScriptedJev, **limit_overrides) -> VeloAgent:
    limits = VeloLimits(max_steps=10, **limit_overrides)
    return VeloAgent(fake_cua, jev, limits, allowed_apps={"com.google.Chrome": "Chrome"})


async def test_a_open_browser_observe_decide_launch_observe_done() -> None:
    fake_cua = FakeCua(
        observations=[
            observation(),  # desktop before launch
            observation(target("t1", "New Tab"), foreground_app="Google Chrome"),
        ]
    )
    jev = ScriptedJev(
        act(VeloActionKind.LAUNCH_APP, app_name="com.google.Chrome"),
        done(),
    )
    agent = build_agent(fake_cua, jev)

    result = await agent.run(VeloObjective(text="Open Chrome"))

    assert result.status is VeloStatus.DONE
    assert fake_cua.call_kinds("execute") == [("execute", "LAUNCH_APP:com.google.Chrome")]
    assert fake_cua.call_kinds("observe"), "fresh observation after the launch"
    assert result.metrics.decision_count == 2
    assert result.metrics.action_count == 1
    assert result.steps[0].action_result is not None
    assert result.steps[0].action_result.status == "ok"


async def test_b_multi_step_browser_flow() -> None:
    fake_cua = FakeCua(
        observations=[
            observation(),
            observation(target("t1", "New Tab"), foreground_app="Google Chrome"),
            observation(target("t1", "address bar"), foreground_app="Google Chrome"),
            observation(
                target("t1", "WhatsApp Web"),
                foreground_app="Google Chrome",
                url="https://web.whatsapp.com",
            ),
        ]
    )
    jev = ScriptedJev(
        act(VeloActionKind.LAUNCH_APP, app_name="com.google.Chrome"),
        act(VeloActionKind.CLICK, target_id="t1"),
        act(VeloActionKind.TYPE_USER_TEXT, payload_id="text_1"),
        act(VeloActionKind.PRESS_KEY, key="return"),
        done(),
    )
    agent = build_agent(fake_cua, jev)
    objective = VeloObjective(
        text="Open Chrome and search WhatsApp Web",
        text_candidates=("WhatsApp Web",),
    )

    result = await agent.run(objective)

    assert result.status is VeloStatus.DONE
    assert result.metrics.action_count == 4
    assert result.metrics.decision_count == 5
    assert [signature for kind, signature in fake_cua.calls if kind == "execute"] == [
        "LAUNCH_APP:com.google.Chrome",
        "CLICK:t1",
        "TYPE_USER_TEXT:text_1",
        "PRESS_KEY:return",
    ]


async def test_c_exact_user_text_is_carried_byte_for_byte() -> None:
    payload = "I will call you later"
    fake_cua = FakeCua(
        observations=[
            observation(target("t_msg", "Rahul"), foreground_app="WhatsApp"),
            observation(
                target("t_msg", "Rahul"),
                target("t_input", "message input", role="textarea"),
                foreground_app="WhatsApp",
                focused_element="message input",
            ),
        ]
    )
    jev = ScriptedJev(
        act(VeloActionKind.CLICK, target_id="t_msg"),
        act(VeloActionKind.TYPE_USER_TEXT, payload_id="user_text_1"),
        done(),
    )
    agent = build_agent(fake_cua, jev)
    objective = VeloObjective(
        text="Open Rahul and send my message", user_text_payloads={"user_text_1": payload}
    )

    result = await agent.run(objective)

    assert result.status is VeloStatus.DONE
    state = jev.calls[0]
    # The engine receives the exact payload as data; JEV may only select
    # its id -- the loop never rewrites user text (Velo spec section 23).
    assert state["objective"].user_text_payloads["user_text_1"] == payload
    # The payload id selection reached the adapter untouched.
    assert [signature for kind, signature in fake_cua.calls if kind == "execute"] == [
        "CLICK:t_msg",
        "TYPE_USER_TEXT:user_text_1",
    ]
    assert jev.calls[1]["observation"].target("t_input") is not None


async def test_metrics_are_measured_not_claimed() -> None:
    fake_cua = FakeCua(
        observations=[
            observation(),
            observation(target("t1", "ok"), foreground_app="Google Chrome"),
        ]
    )
    jev = ScriptedJev(act(VeloActionKind.CLICK, target_id="t1"), done())
    agent = build_agent(fake_cua, jev)

    result = await agent.run(VeloObjective(text="Open Chrome"))

    metrics = result.metrics
    assert metrics.screenshot_count == 0
    assert metrics.decision_count == 2
    assert metrics.action_count == 1
    assert metrics.step_count == 2
    assert metrics.total_run_ms >= 0
    assert metrics.jev_decision_ms >= 0
    assert metrics.observe_ms >= 0
    assert metrics.verify_ms >= 0
    as_dict = metrics.as_dict()
    assert set(as_dict) == {
        "observe_ms",
        "jev_decision_ms",
        "cua_action_ms",
        "verify_ms",
        "total_run_ms",
        "decision_count",
        "action_count",
        "screenshot_count",
        "step_count",
    }


async def test_cancellation_stops_before_the_next_action() -> None:
    fake_cua = FakeCua()
    jev = ScriptedJev(act(VeloActionKind.CLICK, target_id="t1"))
    agent = build_agent(fake_cua, jev)

    def cancel_after_first_action() -> bool:
        return len(fake_cua.call_kinds("execute")) >= 1

    result = await agent.run(
        VeloObjective(text="Click around"), cancel_check=cancel_after_first_action
    )

    assert result.status is VeloStatus.STOPPED
    assert "cancel" in result.reason.lower()
    assert len(fake_cua.call_kinds("execute")) == 1  # no new action after the flag


async def test_desktop_run_cancellation_is_reported_as_stopped() -> None:
    fake_cua = FakeCua()
    jev = ScriptedJev(act(VeloActionKind.CLICK, target_id="t1"))
    agent = build_agent(fake_cua, jev)

    def cancel_mid_run(_line: str) -> None:
        fake_cua.cancelled = True

    result = await agent.run(VeloObjective(text="Click around"), on_progress=cancel_mid_run)

    assert result.status is VeloStatus.STOPPED
    assert fake_cua.cancelled


async def test_ask_user_terminates_the_run() -> None:
    fake_cua = FakeCua(
        observations=[
            observation(target("t1", "Rahul"), target("t2", "Rahul Office")),
            observation(target("t1", "Rahul"), target("t2", "Rahul Office")),
        ]
    )
    jev = ScriptedJev(ask_user("Two contacts named Rahul are visible."))
    agent = build_agent(fake_cua, jev)

    result = await agent.run(VeloObjective(text="Open Rahul"))

    assert result.status is VeloStatus.ASK_USER
    assert "Rahul" in result.reason
    assert fake_cua.call_kinds("execute") == []


async def test_recent_history_stays_bounded() -> None:
    fake_cua = FakeCua()
    jev = ScriptedJev(act(VeloActionKind.OBSERVE), act(VeloActionKind.OBSERVE), done())
    agent = build_agent(fake_cua, jev, recent_history_steps=2)

    result = await agent.run(VeloObjective(text="Watch the screen"))

    assert result.status is VeloStatus.DONE
    history_lengths = [len(call["recent_steps"]) for call in jev.calls]
    assert history_lengths == [0, 1, 2]
    assert all("action" in entry for call in jev.calls for entry in call["recent_steps"])
