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

rm -rf "$BUILD" "$OUT/sani-core-runtime"
mkdir -p "$BUILD" "$OUT"
# A macOS one-file executable extracts Python dylibs into /private/tmp.  Once
# the outer Sani app is ad-hoc signed, hardened runtime rejects those extracted
# dylibs as a different signing identity.  `--onedir` keeps the signed runtime
# in Sani.app's Resources and also avoids PyInstaller's parent/child process
# pair at launch.
PYTHONPATH="$ROOT/src" "$PY" -m PyInstaller --noconfirm --clean --onedir \
  --name sani-core --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" \
  --paths "$ROOT/src" --collect-submodules assistant \
  "$HERE/src-tauri/python/sani_core_entry.py"
cp -R "$BUILD/dist/sani-core" "$OUT/sani-core-runtime"
chmod +x "$OUT/sani-core-runtime/sani-core"
echo "Core runtime ready: $OUT/sani-core-runtime/sani-core"
