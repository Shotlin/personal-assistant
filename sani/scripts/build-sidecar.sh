#!/usr/bin/env bash
# Freeze Sani's Moonshine STT worker into a self-contained macOS sidecar binary.
#
# The installed app must not depend on the repository .stt-venv, a global
# Python, or anything a Terminal exports. PyInstaller --onefile produces a
# single executable that Tauri ships as `externalBin` next to the app binary
# (Contents/MacOS/sani-stt-<target-triple>).
#
# Output: src-tauri/binaries/sani-stt-<rustc-host-triple>
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HERE/.stt-venv"
BUILD="$HERE/.sidecar-build"
OUT="$HERE/src-tauri/binaries"

PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$VENV/Scripts/python.exe"
if [ ! -x "$PY" ]; then
  echo "error: no STT venv at $VENV — run ./scripts/setup-stt.sh first" >&2
  exit 1
fi

if ! "$PY" -c "import PyInstaller" >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PY" pyinstaller
  else
    "$PY" -m pip install --quiet pyinstaller
  fi
fi

TRIPLE="$(rustc -vV | sed -n 's|host: ||p')"
if [ -z "$TRIPLE" ]; then
  echo "error: could not determine the rustc host triple" >&2
  exit 1
fi

rm -rf "$BUILD"
mkdir -p "$BUILD" "$OUT"

"$PY" -m PyInstaller \
  --noconfirm --clean --onefile \
  --name sani-stt \
  --distpath "$BUILD/dist" \
  --workpath "$BUILD/work" \
  --specpath "$BUILD" \
  --collect-all moonshine_voice \
  --collect-submodules onnxruntime \
  --collect-binaries onnxruntime \
  --collect-data onnxruntime \
  --collect-submodules numpy \
  --hidden-import encodings \
  "$HERE/src-tauri/python/sani_stt.py"

cp "$BUILD/dist/sani-stt" "$OUT/sani-stt-$TRIPLE"
chmod +x "$OUT/sani-stt-$TRIPLE"

echo "Sidecar ready: $OUT/sani-stt-$TRIPLE ($(du -h "$OUT/sani-stt-$TRIPLE" | cut -f1))"
echo "Catalogue contract check:"
"$OUT/sani-stt-$TRIPLE" --list-models >"$BUILD/catalogue.out" 2>"$BUILD/catalogue.err" || true
grep -Fx '{"models":["tiny-streaming-en","base-streaming-en","small-streaming-en","medium-streaming-en"],"default":"small-streaming-en"}' "$BUILD/catalogue.out" >/dev/null \
  || { echo "error: sidecar catalogue contract failed; see $BUILD/catalogue.err" >&2; tail -5 "$BUILD/catalogue.err" >&2; exit 1; }
echo "  sidecar catalogue is current"
