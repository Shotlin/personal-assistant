"""Unit tests for truthful run timing (master plan WP1, finding F06)."""

import threading
import time

from assistant.observability.timing import RunTimeline


def test_terminal_event_marked_once():
    timeline = RunTimeline("run-1")
    timeline.mark("accepted")
    timeline.mark_terminal()
    timeline.mark_terminal()  # duplicate ignored
    assert timeline.terminal_marked is True
    terminals = [e for e in timeline.events() if e["event"] == "run_finished"]
    assert len(terminals) == 1


def test_elapsed_measures_real_time():
    timeline = RunTimeline("run-2")
    time.sleep(0.05)
    assert timeline.elapsed_ms >= 40


def test_concurrent_single_terminal_mark():
    timeline = RunTimeline("run-3")
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        timeline.mark_terminal()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    terminals = [e for e in timeline.events() if e["event"] == "run_finished"]
    assert len(terminals) == 1
