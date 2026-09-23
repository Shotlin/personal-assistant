#!/usr/bin/env bash
# Package the pinned CUA driver as Sani's embedded worker.  It is never
# installed globally and Sani never relies on PATH or a user LaunchAgent.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$HERE/.cua-build"
OUT="$HERE/src-tauri/binaries"
VERSION="0.28.2"
ARCHIVE="cua-driver-rs-${VERSION}-darwin-arm64.tar.gz"
URL="https://github.com/trycua/cua/releases/download/cua-driver-rs-v${VERSION}/${ARCHIVE}"
SHA256="818ddefa0fa8ba2ec9cba837c7aa634a4b064221c748752cf49c5b08e2c94e8c"
TRIPLE="$(rustc -vV | sed -n 's|host: ||p')"

[ "$TRIPLE" = "aarch64-apple-darwin" ] || {
  echo "error: Sani's pinned CUA package is arm64 macOS only" >&2
  exit 1
}
mkdir -p "$BUILD" "$OUT"
if [ ! -f "$BUILD/$ARCHIVE" ]; then
  curl -fL --retry 3 "$URL" -o "$BUILD/$ARCHIVE"
fi
actual="$(shasum -a 256 "$BUILD/$ARCHIVE" | awk '{print $1}')"
[ "$actual" = "$SHA256" ] || {
  echo "error: CUA driver checksum mismatch (expected $SHA256, got $actual)" >&2
  exit 1
}
rm -rf "$BUILD/unpacked"
mkdir -p "$BUILD/unpacked"
tar -xzf "$BUILD/$ARCHIVE" --strip-components=1 -C "$BUILD/unpacked"
DRIVER="$BUILD/unpacked/cua-driver"
[ -x "$DRIVER" ] || { echo "error: CUA archive lacks executable driver" >&2; exit 1; }
codesign --verify --strict "$DRIVER"
cp "$DRIVER" "$OUT/cua-driver-$TRIPLE"
chmod +x "$OUT/cua-driver-$TRIPLE"
echo "CUA driver ready: $OUT/cua-driver-$TRIPLE ($VERSION)"
