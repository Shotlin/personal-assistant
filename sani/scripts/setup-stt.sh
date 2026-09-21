#!/usr/bin/env bash
# Bootstrap Sani's local STT environment (Moonshine Voice, Small Streaming English).
#
# Creates sani/.stt-venv with the `moonshine-voice` package + onnxruntime.
# The first model load downloads the small streaming model into the user
# cache (~/Library/Caches/moonshine_voice or platform equivalent); after that
# everything runs fully on-device.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HERE/.stt-venv"

if [ ! -x "$VENV/bin/python" ] && [ ! -x "$VENV/Scripts/python.exe" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV" --python 3.12
  else
    python3 -m venv "$VENV"
  fi
fi

PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$VENV/Scripts/python.exe"

if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" "moonshine-voice>=0.1.5" onnxruntime
else
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install "moonshine-voice>=0.1.5" onnxruntime
fi

echo "Sani STT environment ready: $PY"
