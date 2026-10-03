#!/usr/bin/env bash
# Build the sani-tts output worker environment (Jarvis Phase 1, T09).
#
# The worker runtime is pinned via sani/tts/pyproject.toml + uv.lock. Engine
# weights/voices are NOT downloaded here: asset acquisition happens only
# after the owner-authorized audition selects the engine and its licensed
# asset manifest (hash + source + licence + voice) is recorded in
# docs/verification/phase1/VOICE_SELECTION.md. No cloud fallback exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TTS_DIR="$(cd "$SCRIPT_DIR/../tts" && pwd)"
STAGE_ROOT="$(cd "$SCRIPT_DIR/../src-tauri" && pwd)"

cd "$TTS_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to build the sani-tts worker environment" >&2
  exit 1
fi

uv sync --frozen

echo "sani-tts worker environment built (engine: pending audition)"

# Build a relocatable, self-contained worker executable.  A shell launcher
# into uv's development environment is deliberately forbidden: it would
# silently depend on the build machine after the application is moved.
# PyInstaller freezes the worker's Python runtime alongside its executable;
# no engine weights, voices, or network assets are downloaded by this step.
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
BUILD="$SCRIPT_DIR/../.tts-build"
OUT="$STAGE_ROOT/binaries"

[ -x "$PY" ] || { echo "error: missing project Python runtime at $PY" >&2; exit 1; }
"$PY" -c 'import PyInstaller' >/dev/null 2>&1 || {
  echo "error: project runtime lacks PyInstaller; install the locked release build dependency" >&2
  exit 1
}

rm -rf "$BUILD" "$OUT/sani-tts-python"
mkdir -p "$BUILD" "$OUT"
"$PY" -m PyInstaller --noconfirm --clean --onedir \
  --name sani-tts-python --distpath "$BUILD/dist" --workpath "$BUILD/work" \
  --specpath "$BUILD" "$STAGE_ROOT/python/sani_tts.py"
cp -R "$BUILD/dist/sani-tts-python" "$OUT/sani-tts-python"
chmod +x "$OUT/sani-tts-python/sani-tts-python"

# The provenance file deliberately records that no engine/voice asset exists
# yet. It is shipped beside the executable and is auditable in the release
# package without selecting or downloading an engine.
SOURCE_SHA256="$(shasum -a 256 "$STAGE_ROOT/python/sani_tts.py" | awk '{print $1}')"
printf '{"schema_version":1,"engine":"macos-say","asset_manifest":null,"worker_source_sha256":"%s"}\n' "$SOURCE_SHA256" \
  > "$OUT/sani-tts-python/build-info.json"
echo "sani-tts packaged worker: $OUT/sani-tts-python/sani-tts-python"
