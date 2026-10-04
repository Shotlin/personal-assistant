from assistant.core import context_meter


def test_unknown_until_recorded() -> None:
    assert context_meter.snapshot("nope")["known"] is False


def test_reported_tokens_and_window(monkeypatch) -> None:
    monkeypatch.setattr(context_meter, "_windows", {"m/x": 100_000})
    context_meter.record("c1", 25_000, model="m/x")
    snap = context_meter.snapshot("c1")
    assert snap["tokens"] == 25_000 and snap["window"] == 100_000
    assert snap["percent"] == 25 and snap["estimated"] is False


def test_estimate_is_marked(monkeypatch) -> None:
    monkeypatch.setattr(context_meter, "_windows", {"m/x": 1000})
    context_meter.estimate_add("c2", "x" * 400, "m/x")
    snap = context_meter.snapshot("c2")
    assert snap["estimated"] is True and snap["tokens"] == 100 and snap["percent"] == 10
