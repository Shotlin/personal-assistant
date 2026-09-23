#!/usr/bin/env bash
# One owned release pipeline: source workers, resources, Tauri bundle and an
# inspectable non-secret identity. It intentionally does not install or
# replace /Applications/Sani.app; that remains an explicit owner action.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
TRIPLE="$(rustc -vV | sed -n 's|host: ||p')"
[ "$TRIPLE" = "aarch64-apple-darwin" ] || { echo "error: this MacBook release requires an arm64 build host" >&2; exit 1; }
(cd "$HERE" && ./scripts/build-sidecar.sh && ./scripts/build-core.sh && ./scripts/build-cua-driver.sh && npm run build)
STT="$HERE/src-tauri/binaries/sani-stt-$TRIPLE"
CORE="$HERE/src-tauri/binaries/sani-core-$TRIPLE"
CUA="$HERE/src-tauri/binaries/cua-driver-$TRIPLE"
"$STT" --list-models >/dev/null
[ -x "$CORE" ] || { echo "error: packaged core missing" >&2; exit 1; }
[ -x "$CUA" ] || { echo "error: packaged CUA driver missing" >&2; exit 1; }
REVISION="$(git -C "$ROOT" rev-parse HEAD)"
python3 - "$HERE/src-tauri/release-manifest.json" "$REVISION" "$TRIPLE" "$STT" "$CORE" "$CUA" <<'PY'
import hashlib, json, pathlib, sys
out, revision, arch, stt, core, cua = sys.argv[1:]
digest = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
pathlib.Path(out).write_text(json.dumps({"source_revision": revision, "architecture": arch, "protocol": 1, "components": {"sani-stt": digest(stt), "sani-core": digest(core), "cua-driver": digest(cua)}}, indent=2) + "\n")
PY
(cd "$HERE" && npm run tauri -- build)
echo "release bundle: $HERE/src-tauri/target/release/bundle/macos/Sani.app"
