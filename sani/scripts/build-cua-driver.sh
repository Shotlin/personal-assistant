#!/usr/bin/env bash
# Package the pinned CUA driver as Sani's computer-control worker.
#
# Sani ships the vendor's `CuaDriver.app`, not the loose `cua-driver` binary.
# That bundle is signed `Developer ID Application: Cua AI, Inc.`, and macOS
# records Accessibility and Screen Recording against *that* stable identity.
# A bare nested binary gets an ad-hoc identity that changes on every build, so
# it can never hold a grant -- which is why computer control reported "ready"
# while the pointer never moved. Sani still owns the endpoint: it launches the
# bundle with an explicit private `--socket`, so no global or standard-mode
# daemon is ever revived.
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
APP="$BUILD/unpacked/CuaDriver.app"
[ -x "$DRIVER" ] || { echo "error: CUA archive lacks executable driver" >&2; exit 1; }
[ -d "$APP" ] || { echo "error: CUA archive lacks CuaDriver.app" >&2; exit 1; }
codesign --verify --strict "$DRIVER"
# Verify the bundle's own Developer ID chain and copy it untouched: re-signing
# here would replace the stable identity the permission is granted against.
# Output is captured first: `grep -q` closes the pipe early, which makes
# codesign die on SIGPIPE and `pipefail` abort the script.
verify_out="$(codesign --verify --strict --verbose=2 "$APP" 2>&1)"
case "$verify_out" in *"satisfies its Designated Requirement"*) ;; *)
  echo "error: CuaDriver.app failed signature verification: $verify_out" >&2; exit 1 ;;
esac
sig_out="$(codesign -d --verbose=4 "$APP" 2>&1)"
case "$sig_out" in *"Authority=Developer ID Application"*) ;; *)
  echo "error: CuaDriver.app is not Developer ID signed; its TCC identity would" >&2
  echo "       change on every build, so the permission grant could not persist." >&2
  exit 1 ;;
esac
rm -rf "$OUT/CuaDriver.app"
cp -R "$APP" "$OUT/CuaDriver.app"
rm -f "$OUT/cua-driver-$TRIPLE" "$OUT/cua-driver"
echo "CUA driver bundle ready: $OUT/CuaDriver.app ($VERSION)"
