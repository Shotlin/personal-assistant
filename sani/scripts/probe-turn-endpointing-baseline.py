#!/usr/bin/env python3
"""Baseline: how many `final` events does the CURRENT sidecar emit for one
natural utterance that contains a thinking pause?

Drives the installed packaged binary directly over stdio. Never contacts the
gateway, so this costs zero tokens.
"""
import array
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import wave

SIDECAR = os.environ.get(
    "SANI_STT_SIDECAR", "/Applications/Sani.app/Contents/MacOS/sani-stt"
)
MODEL = os.environ.get("SANI_STT_MODEL", "small-streaming-en")
RATE = 16000
SCRATCH = "/tmp/sani-baseline"


def synth(phrase: str, out: str) -> None:
    aiff = out + ".aiff"
    subprocess.run(["say", "-v", "Samantha", "-o", aiff, phrase], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, out],
        check=True,
    )


def read_mono(path: str) -> array.array:
    with wave.open(path) as src:
        assert src.getnchannels() == 1 and src.getframerate() == RATE, "convert failed"
        data = src.readframes(src.getnframes())
    import numpy as np

    pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    return array.array("f", pcm.tolist())


def frame(kind: int, payload: bytes) -> bytes:
    return struct.pack("<BI", kind, len(payload)) + payload


def main() -> int:
    if not os.path.exists(SIDECAR):
        print("sidecar not installed:", SIDECAR)
        return 2
    os.makedirs(SCRATCH, exist_ok=True)

    phrases = ["Go to GitHub", "and check my latest build"]
    gap_ms = int(sys.argv[1]) if len(sys.argv) > 1 else 900

    samples = array.array("f")
    for i, phrase in enumerate(phrases):
        path = f"{SCRATCH}/p{i}.wav"
        synth(phrase, path)
        samples.extend(read_mono(path))
        if i < len(phrases) - 1:
            samples.extend(array.array("f", [0.0] * (RATE * gap_ms // 1000)))
    # trailing silence long enough to end the turn
    samples.extend(array.array("f", [0.0] * (RATE * 3 // 10)))
    print(f"clip: {len(samples)/RATE:.2f}s  gap between phrases {gap_ms}ms")

    proc = subprocess.Popen(
        [SIDECAR, "--model", MODEL],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    events: queue.Queue = queue.Queue()

    def pump() -> None:
        for line in iter(proc.stdout.readline, b""):
            events.put(line.decode("utf-8", "replace").strip())
        events.put(None)

    threading.Thread(target=pump, daemon=True).start()

    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            msg = events.get(timeout=0.5)
        except queue.Empty:
            continue
        if msg and '"type":"ready"' in msg:
            break
    else:
        print("sidecar never became ready")
        proc.kill()
        return 1

    finals, partials, notes = [], 0, []
    start = time.time()
    chunk = 1600

    def drain(block: bool) -> None:
        nonlocal partials
        while True:
            try:
                msg = events.get_nowait()
            except queue.Empty:
                return
            if msg is None:
                return
            if '"type":"final"' in msg:
                finals.append(msg)
                print(f"  FINAL   t={time.time()-start:5.2f}s {msg}")
            elif '"type":"partial"' in msg:
                partials += 1
                print(f"  partial t={time.time()-start:5.2f}s {msg}")
            elif '"type":"note"' in msg:
                notes.append(msg)
                print(f"  note    t={time.time()-start:5.2f}s {msg}")

    for i in range(0, len(samples), chunk):
        block = samples[i : i + chunk]
        payload = block.tobytes()  # array('f') is already little-endian f32
        try:
            proc.stdin.write(frame(0x01, payload))
            proc.stdin.flush()
        except BrokenPipeError:
            break
        time.sleep(0.1)
        drain(False)

    time.sleep(3.0)
    drain(False)
    proc.kill()

    print(f"\npartials={partials} notes={len(notes)} FINAL_COUNT={len(finals)}")
    print(f"expected for a correct endpointer: 1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
