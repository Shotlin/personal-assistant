"""Authoritative Moonshine model catalog contract for the Sani STT sidecar."""

import importlib.util
from pathlib import Path
import sys


def load_sidecar():
    path = Path(__file__).parents[2] / "sani/src-tauri/python/sani_stt.py"
    spec = importlib.util.spec_from_file_location("sani_stt", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_supported_models_are_exact_and_small_is_default() -> None:
    sidecar = load_sidecar()

    assert sidecar.SUPPORTED_MODELS == (
        "tiny-streaming-en",
        "base-streaming-en",
        "small-streaming-en",
        "medium-streaming-en",
    )
    assert sidecar.DEFAULT_MODEL == "small-streaming-en"


def test_list_models_is_a_machine_readable_catalog() -> None:
    sidecar = load_sidecar()
    parser = sidecar.build_parser()
    args = parser.parse_args(["--list-models"])

    assert args.list_models is True


def test_silence_never_commits_without_explicit_flush() -> None:
    sidecar = load_sidecar()
    events: list[dict] = []
    acc = sidecar.TurnAccumulator(
        turn_end_s=1.0,
        max_utterance_s=30.0,
        partial_stable_s=0.1,
        emit=events.append,
    )
    acc.on_line_text(1, "keep this draft", 0.0)
    acc.on_speech_end(0.0)
    for seconds in (2.0, 5.0, 15.0):
        assert acc.due(seconds)[0] != "commit"
    assert not [event for event in events if event["type"] == "final"]
    acc.commit("explicit-flush", 15.0)
    assert [event["text"] for event in events if event["type"] == "final"] == ["keep this draft"]
