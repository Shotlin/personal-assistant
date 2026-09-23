#!/usr/bin/env bash
# Build, sign and install Sani.app into /Applications, then launch it the way a
# user would (LaunchServices/Finder), so the installed app — not a dev binary —
# is what gets verified.
#
# Sani stays a plain local app: ad-hoc signing only. No certificate, no
# keychain item, so no "codesign wants to access a key" prompt can ever appear.
#
# The one macOS fact this script is organised around: for ad-hoc code, TCC
# records Accessibility and Screen Recording against the main executable's
# cdhash. Any install that changes those bytes silently invalidates the grant
# the user just made — which is exactly how System Settings ends up showing a
# checked "Sani" row while Sani truthfully reports `denied`. So this script
# never reinstalls bytes that are already installed, and says so plainly when
# the identity really does change.
#
# The previously installed bundle is kept as a timestamped backup under
# ~/Library/Caches/sani-app-backups so this is always reversible.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE="$HERE/src-tauri/target/release/bundle/macos/Sani.app"
INSTALLED="/Applications/Sani.app"
BACKUP_ROOT="$HOME/Library/Caches/sani-app-backups"
BUNDLE_ID="app.sani.local"

if [ ! -d "$HERE/src-tauri/binaries" ]; then
  echo "==> no packaged sidecar; run ./scripts/build-sidecar.sh first" >&2
  exit 1
fi

echo "==> release build (app bundle only)"
(cd "$HERE" && npm run tauri -- build --bundles app)

# Sign inside-out with stable identifiers. `--deep` is avoided: it signs the
# outer bundle before its contents and leaves nested helpers linker-signed with
# a per-build hash as their identifier (e.g. `cua-driver-5555…`), which makes
# the driver look like a brand new client to macOS on every single build.
echo "==> ad-hoc signing nested helpers, then the bundle"
while IFS= read -r -d '' binary; do
  [ "$binary" = "$BUNDLE/Contents/MacOS/sani" ] && continue
  # CuaDriver.app ships with a Developer ID signature and that exact identity
  # is what the user's Accessibility and Screen Recording grants are recorded
  # against. Re-signing it ad-hoc here would re-create the original bug: a new
  # code identity on every build, and a permission that silently stops working.
  case "$binary" in */CuaDriver.app/*) continue ;; esac
  file -b "$binary" 2>/dev/null | grep -q 'Mach-O' || continue
  # Strip any extension from the file name alone. Applying `%.*` to the whole
  # path stops at the dot inside `Sani.app`, which named every helper
  # `app.sani.local.Sani`.
  name="$(basename "$binary")"
  codesign --force --timestamp=none --sign - \
    --identifier "$BUNDLE_ID.${name%.*}" "$binary" 2>/dev/null || true
done < <(find "$BUNDLE/Contents" -type f ! -path "*/_CodeSignature/*" -print0)
codesign --force --timestamp=none --sign - --identifier "$BUNDLE_ID" "$BUNDLE"
codesign --verify --verbose=1 "$BUNDLE" 2>&1 | tail -1

# `--verbose=4` is required: plain `-dvv` does not print the CDHash line at all,
# which would make every install look like a first install.
cdhash() { codesign -d --verbose=4 "$1" 2>&1 | sed -n 's/^CDHash=//p' | head -1; }
NEW_HASH="$(cdhash "$BUNDLE")"
OLD_HASH=""
[ -d "$INSTALLED" ] && OLD_HASH="$(cdhash "$INSTALLED")"

echo "==> quitting any running Sani, including an orphaned driver"
# No `osascript` here: asking System Events from a non-GUI shell can hang the
# install indefinitely. Signalling the process is enough for a local app.
pkill -x sani 2>/dev/null || true
pkill -x sani-stt 2>/dev/null || true
pkill -x sani-core 2>/dev/null || true
# A driver outliving its parent keeps owning the private socket, so the next
# Sani would talk to a process carrying the previous build's code identity: it
# answers every request and nothing ever moves on screen.
pkill -x cua-driver 2>/dev/null || true
sleep 1
rm -f "$HOME/Library/Application Support/app.sani.local/cua-driver.sock"

if [ -z "$OLD_HASH" ]; then
  echo "==> first install, code identity $NEW_HASH"
elif [ "$OLD_HASH" = "$NEW_HASH" ]; then
  # TCC binds to the main executable only, so replacing nested helpers keeps the
  # permissions the user already granted working.
  echo "==> main executable unchanged ($NEW_HASH) — existing grants still apply"
else
  echo "==> code identity changes $OLD_HASH -> $NEW_HASH"
  echo "    macOS binds Accessibility and Screen Recording to that value, so Sani"
  echo "    must be approved once again. Clearing the orphaned rows now is what"
  echo "    stops System Settings showing a toggle for a build that no longer"
  echo "    exists — the exact state that reads 'on' while Sani reads 'denied'."
  tccutil reset ScreenCapture "$BUNDLE_ID" 2>/dev/null || true
  tccutil reset Accessibility "$BUNDLE_ID" 2>/dev/null || true
fi

# Move the old bundle out before copying: `cp -R` onto an existing directory
# nests a copy inside it instead of replacing it.
if [ -d "$INSTALLED" ]; then
  mkdir -p "$BACKUP_ROOT"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  mv "$INSTALLED" "$BACKUP_ROOT/Sani-$STAMP.app"
  echo "==> previous install backed up to $BACKUP_ROOT/Sani-$STAMP.app"
fi
echo "==> installing to $INSTALLED"
cp -R "$BUNDLE" "$INSTALLED"

# A pile of old bundles in the backup folder is how a stale copy gets launched
# by accident, so only the most recent few are kept.
ls -1dt "$BACKUP_ROOT"/Sani-*.app 2>/dev/null | tail -n +6 |
  while IFS= read -r stale; do rm -rf "$stale"; done

echo "==> launching via LaunchServices (same path as double-clicking in Finder)"
# Only /Applications/Sani.app is ever launched, and never with `-n`, so exactly
# one identity runs. A copy under target/ is a different code identity and must
# not be used to judge permissions.
open "$INSTALLED"
sleep 3
echo "log: ~/Library/Logs/app.sani.local/sani.log"
