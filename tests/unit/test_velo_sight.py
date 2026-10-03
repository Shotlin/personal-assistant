"""Sight: find a control the tree can't name, click it, verify, remember it."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from assistant.velo import sight
from assistant.velo.adapter import CuaAdapter
from assistant.velo.contracts import AppIdentity, OutcomeState, Target, TaskState
from assistant.velo.recipes import execute
from assistant.velo.sight import SightKit
from assistant.velo.uimemory import UiMemory, distance, fingerprint_of, scope_for
from assistant.velo.vision import VisionHit, parse_controls, parse_hit
from tests.unit.velo_fakes import (
    EventLog,
    app_entry,
    reset_world,
    standard_tools,
    window_state_payload,
)

W, H = 1000, 600


def _png(*, button: bool, popup: bool = False) -> bytes:
    image = Image.new("RGB", (W, H), (30, 30, 30))
    draw = ImageDraw.Draw(image)
    if button:
        draw.rectangle((700, 500, 860, 540), fill=(240, 240, 240))
        draw.text((730, 512), "Get started", fill=(0, 0, 0))
    if popup:
        draw.rectangle((100, 100, 900, 450), fill=(10, 80, 160))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class FakeVision:
    enabled = True

    def __init__(self, hit: VisionHit | None) -> None:
        self.hit, self.calls = hit, 0

    async def locate(self, png, target, width, height):
        self.calls += 1
        return self.hit

    async def controls(self, png, width, height):
        return [VisionHit("Get started", 780, 520, 0.9), VisionHit("Close", 880, 120, 0.9)]


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    reset_world()
    async def instant(*_a, **_k):
        return None
    monkeypatch.setattr(sight.asyncio, "sleep", instant)
    yield
    reset_world()


def _setup(tmp_path: Path, vision, *, clicks_change: bool = True):
    from tests.unit.velo_fakes import SHARED_APPS

    SHARED_APPS["list"] = [app_entry("Google Chrome", pid=9, running=True)]
    tools = standard_tools()
    ui = {"clicked": False}

    def state(kwargs):
        out = kwargs.get("screenshot_out_file")
        elements = [{"role": "AXTextField", "label": "Address and search bar",
                     "value": "https://flow.google.com/project/1", "element_token": "a",
                     "frame": {"x": 1, "y": 80, "w": 400, "h": 30}}]
        payload = window_state_payload(elements, pid=9, window_id=2)
        if out:
            Path(out).write_bytes(_png(button=not ui["clicked"], popup=not ui["clicked"]))
            payload.update({"screenshot_file_path": out, "screenshot_width": W,
                            "screenshot_height": H, "window_bounds": {"width": 1470, "height": 846}})
        return "t", payload

    def click(kwargs):
        if clicks_change and "x" in kwargs:
            ui["clicked"] = True
        return "ok", {}

    tools["get_window_state"]._respond = state
    tools["click"]._respond = click
    tools["click"].args = {k: {} for k in ("element_token", "pid", "window_id", "x", "y")}
    log = EventLog()

    async def on_event(kind, data):
        await log(kind, data)

    memory = UiMemory(tmp_path / "ui.db")
    adapter = CuaAdapter(tools, on_event=on_event, sight=SightKit(vision, memory))
    task = TaskState(instruction="t", conversation="c", max_actions=12)
    task.resolved = Target(app=AppIdentity(name="Google Chrome", pid=9, running=True), window_id=2)
    return adapter, tools, task, memory, ui


async def test_a_control_the_tree_cannot_name_is_found_by_sight_clicked_and_remembered(tmp_path):
    vision = FakeVision(VisionHit("Get started", 780, 520, 0.9))
    adapter, tools, task, memory, _ui = _setup(tmp_path, vision)
    result = await execute("click_named", task, adapter, label="get started")
    assert result.state is OutcomeState.CONFIRMED and "by sight" in result.answer
    assert (tools["click"].calls[-1]["x"], tools["click"].calls[-1]["y"]) == (780, 520)
    spots = memory.entries("site:flow.google.com")
    assert len(spots) == 1 and spots[0].label == "get started"
    assert 0.77 < spots[0].rel_x < 0.79 and spots[0].fingerprint


async def test_the_second_time_the_remembered_spot_is_used_without_the_model(tmp_path):
    vision = FakeVision(VisionHit("Get started", 780, 520, 0.9))
    adapter, tools, task, memory, ui = _setup(tmp_path, vision)
    await execute("click_named", task, adapter, label="get started")
    assert vision.calls == 1
    ui["clicked"] = False  # the popup is back
    again = await execute("click_named", task, adapter, label="get started")
    assert again.state is OutcomeState.CONFIRMED and "remembered" in again.answer
    assert vision.calls == 1, "no second vision call"


async def test_a_changed_page_at_the_remembered_spot_falls_back_to_sight(tmp_path):
    vision = FakeVision(VisionHit("Get started", 780, 520, 0.9))
    adapter, tools, task, memory, ui = _setup(tmp_path, vision)
    await execute("click_named", task, adapter, label="get started")
    ui["clicked"] = False
    spot = memory.entries()[0]
    # the control no longer looks like it did: same layout, different pixels there
    import sqlite3

    db = sqlite3.connect(tmp_path / "ui.db")
    db.execute("UPDATE spots SET fingerprint='0000000000000000'")
    db.commit()
    await execute("click_named", task, adapter, label="get started")
    assert vision.calls == 2
    assert spot.label == "get started"


async def test_a_click_that_changes_nothing_is_never_reported_as_done(tmp_path):
    vision = FakeVision(VisionHit("Get started", 780, 520, 0.9))
    adapter, _tools, task, memory, _ui = _setup(tmp_path, vision, clicks_change=False)
    result = await execute("click_named", task, adapter, label="get started")
    assert result.state is OutcomeState.UNKNOWN
    assert memory.entries() == []


async def test_without_vision_or_memory_the_old_honest_answer_is_kept(tmp_path):
    adapter, _tools, task, _memory, _ui = _setup(tmp_path, None)
    adapter.sight = SightKit(None, None)
    result = await execute("click_named", task, adapter, label="get started")
    assert result.state is OutcomeState.NO_EFFECT and "can't see" in result.answer


async def test_learn_screen_remembers_every_control_once(tmp_path):
    adapter, _tools, task, memory, _ui = _setup(tmp_path, FakeVision(None))
    result = await execute("learn_screen", task, adapter)
    assert result.state is OutcomeState.CONFIRMED and "Learned 2 controls" in result.answer
    assert {s.label for s in memory.entries("site:flow.google.com")} == {"Get started", "Close"}


def test_vision_answers_are_validated_never_trusted():
    assert parse_hit('{"found": true, "label": "OK", "x": 10, "y": 20, "confidence": 0.9}', 100, 100)
    assert parse_hit('{"found": true, "x": 500, "y": 20}', 100, 100) is None  # off the image
    assert parse_hit('{"found": false}', 100, 100) is None
    assert parse_hit("I think it is near the top", 100, 100) is None
    assert parse_hit('```json\n{"found": true, "label": "Go", "x": 5, "y": 5}\n```', 100, 100)
    assert len(parse_controls('[{"label":"A","x":1,"y":1},{"label":"B","x":999,"y":1}]', 100, 100)) == 1


def test_memory_is_per_site_size_aware_and_stops_trusting_failures(tmp_path):
    memory = UiMemory(tmp_path / "m.db")
    memory.remember("site:a.com", "Buy", role="button", rel_x=.5, rel_y=.5, win_w=1000, win_h=600,
                    fingerprint="")
    assert memory.recall("site:b.com", "buy", 1000, 600) == []
    assert memory.recall("site:a.com", "buy", 1500, 900) == []
    spot = memory.recall("site:a.com", "BUY", 1010, 605)[0]
    memory.mark(spot, worked=False)
    memory.mark(spot, worked=False)
    assert memory.recall("site:a.com", "buy", 1000, 600) == []
    assert memory.clear() == 1


def test_fingerprints_distinguish_a_button_from_a_page_and_scope_names_the_site():
    on, off = _png(button=True), _png(button=False)
    assert distance(fingerprint_of(on, 780, 520), fingerprint_of(on, 781, 521)) <= 4
    assert distance(fingerprint_of(on, 780, 520), fingerprint_of(off, 780, 520)) > 10
    assert scope_for("Google Chrome", "https://flow.google.com/x") == "site:flow.google.com"
    assert scope_for("Codex", "") == "app:codex"
    assert sight.guess_label("now you click the new project button here") == "new project"
