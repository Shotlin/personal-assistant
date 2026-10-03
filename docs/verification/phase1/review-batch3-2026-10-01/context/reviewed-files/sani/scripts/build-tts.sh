#!/usr/bin/env bash
# Build the sani-tts output worker environment (Jarvis Phase 1, T09).
#
# The worker runtime is pinned via sani/tts/pyproject.toml + uv.lock. Engine
# weights/voices are NOT downloaded here: asset acquisition happens only
# after the owner-authorized audition selects the engine and its licensed
# asset manifest (hash + source + licence + voice) is recorded in
# docs/verification/phase1/VOICE_SELECTION.md. No cloud fallback exists.
set -euo pipefail

cd "$(dirname "$0")/../tts"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to build the sani-tts worker environment" >&2
  exit 1
fi

uv sync --frozen

echo "sani-tts worker environment built (engine: pending audition)"

# Stage the packaged interpreter/worker layout the host resolver expects:
#   binaries/sani-tts-python/bin/python3   (a REAL interpreter launcher)
#   python/sani_tts.py                     (the worker, bundled separately)
# No engine weights or assets are downloaded here; the engine stays
# `unspecified` until the owner-authorized audition selects one.
STAGE_ROOT="$(cd "$(dirname "$0")/../src-tauri" && pwd)"
STAGE_BIN="$STAGE_ROOT/binaries/sani-tts-python/bin"
mkdir -p "$STAGE_BIN"
PYTHON_BIN="$(cd ../tts && uv run python -c 'import sys; print(sys.executable)')"
if [ -z "$PYTHON_BIN" ]; then
  echo "sani-tts staging failed: no interpreter from uv env" >&2
  exit 1
fi
cat > "$STAGE_BIN/python3" <<LAUNCHER
#!/bin/sh
# Staged sani-tts interpreter launcher (pinned worker environment).
exec "$PYTHON_BIN" "\$@"
LAUNCHER
chmod +x "$STAGE_BIN/python3"
# The worker script itself lives at src-tauri/python/sani_tts.py and is
# bundled to the resource root by tauri.conf.json ("sani_tts.py").
echo "sani-tts staged interpreter: $STAGE_BIN/python3"
