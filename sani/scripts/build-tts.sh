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
