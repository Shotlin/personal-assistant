#!/usr/bin/env python3
"""Feed real synthesized speech into the PACKAGED sidecar and print what it hears.

This isolates the voice engine from macOS capture: it drives
/Applications/Sani.app/Contents/MacOS/sani-stt with the exact stdio framing
Sani uses (0x01 f32 mono 16 kHz, 0x02 JSON control), so a transcript here
proves the installed, self-contained sidecar really transcribes speech without
a repo venv, a global Python or a Terminal environment.

Usage: python3 scripts/probe-sidecar-speech.py ["sentence to speak"]
"""

from __future__ import annotations

import array
import io
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import wave

SIDECAR = "/Applications/Sani.app/Contents/MacOS/sani-stt"
MODEL = os.environ.get("SANI_STT_MODEL", "small-streaming-en")
RATE = 16000


def speak_wav(text: str, path: str) -> None:
    """Render `text` to a 16 kHz mono WAV with the macOS speech synthesiser."""
    aiff = path + ".aiff"
    subprocess.run(["say", "-v", "Samantha", "-o", aiff, text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, path],
        check=True,
    )
    os.unlink(aiff)


def read_mono_f32(path: str) -> array.array:
    with wave.open(path, "rb") as src:
        assert src.getnchannels() == 1 and src.getframerate() == RATE, "afconvert failed"
        raw = src.readframes(src.getnframes())
    pcm = array.array("h")
    pcm.frombytes(raw)
    return array.array("f", (s / 32768.0 for s in pcm))


def frame(kind: int, payload: bytes) -> bytes:
    return struct.pack("<BI", kind, len(payload)) + payload


def pump(stream, sink: "queue.Queue[dict]") -> None:
    """One reader thread: non-blocking stdout handling by hand is unreliable."""
    for line in stream:
        try:
            sink.put(json.loads(line))
        except Exception:
            pass
    sink.put({"type": "eof"})


def drain(sink: "queue.Queue[dict]", finals: list[str]) -> None:
    while True:
        try:
            event = sink.get_nowait()
        except queue.Empty:
            return
        kind = event.get("type")
        if kind == "partial":
            print("  partial:", event["text"])
        elif kind == "final":
            finals.append(event["text"])
            print("  FINAL  :", event["text"])
        elif kind == "error":
            print("  ERROR  :", event.get("message"))
        elif kind == "note":
            print("  note   :", event.get("message"))


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "What is seven times eight?"
    if not os.path.exists(SIDECAR):
        print(f"missing {SIDECAR} — install Sani.app first", file=sys.stderr)
        return 2

    wav_path = "/tmp/sani-probe-speech.wav"
    speak_wav(text, wav_path)
    samples = read_mono_f32(wav_path)
    samples.extend(array.array("f", [0.0] * (RATE * 3)))  # 3 s trailing silence
    print(f"speaking: {text!r}  ({len(samples) / RATE:.2f}s of audio incl. silence)")

    proc = subprocess.Popen(
        [SIDECAR, "--model", MODEL],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    assert proc.stdin and proc.stdout
    sink: "queue.Queue[dict]" = queue.Queue()
    # stdin carries binary frames, stdout carries JSON lines: two modes, so the
    # text wrapper is built explicitly rather than via text=True.
    threading.Thread(target=pump, args=(io.TextIOWrapper(proc.stdout), sink), daemon=True).start()

    ready = False
    deadline = time.time() + 240
    while time.time() < deadline:
        event = sink.get()
        if event.get("type") == "ready":
            print("  <- ready:", event.get("model"))
            ready = True
            break
        if event.get("type") == "eof":
            print("sidecar exited before ready", file=sys.stderr)
            return 1
    if not ready:
        print("sidecar never reported ready", file=sys.stderr)
        proc.kill()
        return 1

    # Stream at real-time pace: 100 ms of samples every 100 ms.
    finals: list[str] = []
    chunk = 1600
    for i in range(0, len(samples), chunk):
        proc.stdin.write(frame(0x01, samples[i : i + chunk].tobytes()))
        proc.stdin.flush()
        time.sleep(0.1)
        drain(sink, finals)
        if finals:
            break

    if not finals:
        proc.stdin.write(frame(0x02, b'{"cmd":"flush"}'))
        proc.stdin.flush()
        time.sleep(2.0)
        drain(sink, finals)

    proc.kill()
    print()
    print(f"finals: {finals}")
    print(
        "RESULT:",
        "packaged sidecar transcribed real speech" if finals else "NO FINAL TRANSCRIPT",
    )
    return 0 if finals else 1


if __name__ == "__main__":
    sys.exit(main())
