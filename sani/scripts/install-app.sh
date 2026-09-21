#!/usr/bin/env bash
# Build, ad-hoc sign and install Sani.app into /Applications, then launch it the
# way a user would (LaunchServices/Finder), so the installed app — not a dev
# binary — is what gets verified.
#
# The previously installed bundle is kept as a timestamped backup under
# ~/Library/Caches/sani-app-backups so this is always reversible.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE="$HERE/src-tauri/target/release/bundle/macos/Sani.app"
BACKUP_ROOT="$HOME/Library/Caches/sani-app-backups"

if [ ! -d "$HERE/src-tauri/binaries" ]; then
  echo "==> no packaged sidecar; run ./scripts/build-sidecar.sh first" >&2
  exit 1
fi

echo "==> release build (app bundle only)"
(cd "$HERE" && npm run tauri -- build --bundles app)

echo "==> ad-hoc sign (stable bundle identity for TCC)"
# --sign - keeps this a local-only signature; deep signing covers the sidecar.
codesign --deep --force --sign - "$BUNDLE" 2>/dev/null
codesign --verify --verbose=1 "$BUNDLE" 2>&1 | tail -2

echo "==> quitting any running Sani"
# No `osascript` here: asking System Events from a non-GUI shell can hang the
# install indefinitely. Signalling the process is enough for a local app.
pkill -x sani 2>/dev/null || true
pkill -f "Sani.app/Contents/MacOS/sani" 2>/dev/null || true
sleep 1

if [ -d "/Applications/Sani.app" ]; then
  mkdir -p "$BACKUP_ROOT"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  mv "/Applications/Sani.app" "$BACKUP_ROOT/Sani-$STAMP.app"
  echo "==> previous install backed up to $BACKUP_ROOT/Sani-$STAMP.app"
fi

echo "==> installing to /Applications/Sani.app"
cp -R "$BUNDLE" /Applications/Sani.app

echo "==> launching via LaunchServices (same path as double-clicking in Finder)"
open /Applications/Sani.app
sleep 3
echo "log: ~/Library/Logs/app.sani.local/sani.log"
