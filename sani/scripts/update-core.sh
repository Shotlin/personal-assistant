#!/usr/bin/env bash
# Ship a Python (sani-core) change WITHOUT reinstalling Sani.app.
#
# Reinstalling the bundle changes the app's code identity, and macOS then
# revokes Accessibility and Screen Recording. This builds the frozen core,
# signs it ad-hoc, and places it where the installed app prefers it
# (~/Library/Application Support/app.sani.local/core-override). The app binary is
# untouched, so the permissions you granted keep working. Quit and reopen Sani
# afterwards to load it. Remove the folder to go back to the bundled core.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/Application Support/app.sani.local/core-override"

(cd "$HERE" && ./scripts/build-core.sh >/dev/null)
SRC="$HERE/src-tauri/binaries/sani-core-runtime"
[ -x "$SRC/sani-core" ] || { echo "error: frozen core missing at $SRC" >&2; exit 1; }

rm -rf "$DEST.new"
mkdir -p "$DEST.new"
cp -R "$SRC" "$DEST.new/sani-core-runtime"
while IFS= read -r -d '' binary; do
  file -b "$binary" 2>/dev/null | grep -q 'Mach-O' || continue
  codesign --force --timestamp=none --sign - "$binary" 2>/dev/null || true
done < <(find "$DEST.new" -type f -print0)

rm -rf "$DEST"
mv "$DEST.new" "$DEST"
echo "core override installed: $DEST"
echo "quit and reopen Sani to load it (permissions are unaffected)"
