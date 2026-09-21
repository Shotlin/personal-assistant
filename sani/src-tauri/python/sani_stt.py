#!/usr/bin/env python3
"""Sani speech sidecar: Moonshine Voice streaming STT over stdio.

Protocol (binary-safe framing on stdin, one JSON object per line on stdout):

stdin frame:  <type:u8> <len:u32le> <payload>
  type 0x01 = audio: payload is f32le mono samples at 16 kHz (-1.0..1.0)
  type 0x02 = control: payload is a UTF-8 JSON object
    {"cmd": "reset"}   -- drop the current line, start fresh
    {"cmd": "flush"}   -- force-finalize the current line now

stdout event: {"type": "...", ...} per line
  {"type": "downloading", "progress": 0.42, "file": "..."}
  {"type": "ready", "model": "small-streaming-en"}
  {"type": "partial", "text": "..."}
  {"type": "final", "text": "..."}
  {"type": "error", "message": "..."}

Endpointing: Moonshine's streaming model finalizes a line on its own when
trailing silence flows through the stream. As a bounded fallback for noisy
environments the sidecar also force-finalizes when a line stays open after
`--finalize-silence` seconds of quiet audio.

The sidecar never stores or logs audio; it exists only to turn sound into text.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import threading
import time

MODEL_CHOICES = {
    "tiny-streaming-en": "TINY_STREAMING",
    "base-streaming-en": "BASE_STREAMING",
    "small-streaming-en": "SMALL_STREAMING",
    "medium-streaming-en": "MEDIUM_STREAMING",
}

# RMS below this (float samples -1..1) counts as silence for the fallback.
SILENCE_RMS = 0.0035
CHUNK = 1600  # samples per add_audio call (~100 ms)


def emit(payload: dict, write_lock: threading.Lock) -> None:
    line = json.dumps(payload, separators=(",", ":"))
    with write_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Sani Moonshine streaming STT sidecar")
    parser.add_argument("--model", default="small-streaming-en", choices=sorted(MODEL_CHOICES))
    parser.add_argument("--language", default="en")
    parser.add_argument("--update-interval", type=float, default=0.25)
    parser.add_argument("--finalize-silence", type=float, default=1.4,
                        help="seconds of quiet audio after which an open line is force-finalized")
    args = parser.parse_args()

    from moonshine_voice.download import get_model_for_language
    from moonshine_voice.transcriber import (
        Error,
        LineCompleted,
        LineStarted,
        LineTextChanged,
        ModelArch,
        Transcriber,
    )

    write_lock = threading.Lock()
    arch = ModelArch[MODEL_CHOICES[args.model]]

    def on_progress(fraction: float, name: str) -> None:
        emit({"type": "downloading", "progress": round(fraction, 3), "file": name}, write_lock)

    try:
        model_path, resolved_arch = get_model_for_language(
            args.language, arch, on_progress=on_progress
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced to the host verbatim
        emit({"type": "error", "message": f"model load failed: {exc}"}, write_lock)
        return 1

    try:
        transcriber = Transcriber(str(model_path), resolved_arch)
        stream = transcriber.create_stream(args.update_interval)
    except Exception as exc:  # noqa: BLE001
        emit({"type": "error", "message": f"transcriber init failed: {exc}"}, write_lock)
        return 1

    state = {
        "line_open": False,
        "last_quiet": time.monotonic(),
        "last_speech": 0.0,
    }
    state_lock = threading.Lock()
    discarding = threading.Event()

    def listener(event: object) -> None:
        if isinstance(event, LineTextChanged):
            if discarding.is_set():
                return
            with state_lock:
                state["line_open"] = True
            emit({"type": "partial", "text": event.line.text}, write_lock)
        elif isinstance(event, LineCompleted):
            with state_lock:
                state["line_open"] = False
            text = event.line.text
            if discarding.is_set() or not text.strip():
                # Cancelled capture or empty result: never a final.
                return
            emit({"type": "final", "text": text}, write_lock)
        elif isinstance(event, LineStarted):
            with state_lock:
                state["line_open"] = True
        elif isinstance(event, Error):
            emit({"type": "error", "message": str(event.error)}, write_lock)

    stream.add_listener(listener)
    stream.start()
    emit({"type": "ready", "model": args.model}, write_lock)

    def finalize_now(discard: bool = False) -> None:
        # stop() completes the open line (emitting LineCompleted), start()
        # opens a fresh one on the same loaded model. With discard=True the
        # completed line is swallowed: the host cancelled the capture.
        if discard:
            discarding.set()
        try:
            stream.stop()
            stream.start()
        except Exception as exc:  # noqa: BLE001
            emit({"type": "error", "message": f"finalize failed: {exc}"}, write_lock)
        finally:
            if discard:
                discarding.clear()
        with state_lock:
            state["line_open"] = False

    def watchdog() -> None:
        """Force-finalize when the model's own endpointing has not fired."""
        while True:
            time.sleep(0.25)
            with state_lock:
                open_line = state["line_open"]
                quiet_since = state["last_quiet"]
                last_speech = state["last_speech"]
            if open_line and quiet_since - last_speech >= args.finalize_silence:
                emit({"type": "note", "message": "watchdog finalize"}, write_lock)
                finalize_now()

    threading.Thread(target=watchdog, daemon=True).start()

    # ---- stdin framing loop (main thread) ----
    stdin = sys.stdin.buffer
    while True:
        header = stdin.read(5)
        if len(header) < 5:
            break
        frame_type = header[0]
        (length,) = struct.unpack("<I", header[1:5])
        payload = b""
        while len(payload) < length:
            more = stdin.read(length - len(payload))
            if not more:
                break
            payload += more

        if frame_type == 0x01:
            samples = struct.unpack(f"<{length // 4}f", payload)
            try:
                stream.add_audio(list(samples))
            except Exception as exc:  # noqa: BLE001
                emit({"type": "error", "message": f"add_audio failed: {exc}"}, write_lock)
                continue
            # RMS over this frame updates the quiet/speech clocks.
            if samples:
                mean_sq = sum(s * s for s in samples) / len(samples)
                rms = mean_sq ** 0.5
                now = time.monotonic()
                with state_lock:
                    if rms >= SILENCE_RMS:
                        state["last_speech"] = now
                        state["last_quiet"] = now
                    else:
                        state["last_quiet"] = max(state["last_quiet"], now)
        elif frame_type == 0x02:
            try:
                control = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if control.get("cmd") == "flush":
                finalize_now()
            elif control.get("cmd") == "discard":
                finalize_now(discard=True)
            elif control.get("cmd") == "reset":
                finalize_now(discard=True)

    try:
        stream.stop()
        transcriber.close()
    except Exception:  # noqa: BLE001 -- shutdown best effort
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
