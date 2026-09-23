#!/usr/bin/env bash
# Freeze the existing private sani-core IPC entry point for the installed app.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="$ROOT/.venv/bin/python"
BUILD="$HERE/.core-build"
OUT="$HERE/src-tauri/binaries"

[ -x "$PY" ] || { echo "error: missing project Python runtime at $PY" >&2; exit 1; }
"$PY" -c 'import PyInstaller, assistant.core' >/dev/null 2>&1 || {
  echo "error: project runtime lacks PyInstaller; install the locked release build dependency" >&2
  exit 1
}

TRIPLE="$(rustc -vV | sed -n 's|host: ||p')"
[ -n "$TRIPLE" ] || { echo "error: could not determine Rust target triple" >&2; exit 1; }

rm -rf "$BUILD"
mkdir -p "$BUILD" "$OUT"
PYTHONPATH="$ROOT/src" "$PY" -m PyInstaller --noconfirm --clean --onefile \
  --name sani-core --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" \
  --paths "$ROOT/src" --collect-submodules assistant \
  "$HERE/src-tauri/python/sani_core_entry.py"
cp "$BUILD/dist/sani-core" "$OUT/sani-core-$TRIPLE"
chmod +x "$OUT/sani-core-$TRIPLE"
echo "Core sidecar ready: $OUT/sani-core-$TRIPLE"
