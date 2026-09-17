#!/bin/bash
# Go-live sequence for Phase 1: run every live verification in order.
# Prerequisites (checked below):
#   - OPENROUTER_API_KEY set in .env
#   - TCC grants for CuaDriver.app (Accessibility + Screen Recording)
#   - Open WebUI admin account created (manual step, checked last)
set -euo pipefail
cd "$(dirname "$0")/.."

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.uv-python}"
export PATH="$HOME/.local/bin:$PATH"

echo "== 1/6 preconditions =="
uv run python - <<'EOF'
from dotenv import dotenv_values
from pathlib import Path
vals = dotenv_values(".env")
key = (vals.get("OPENROUTER_API_KEY") or "").strip()
assert key, "OPENROUTER_API_KEY is empty -- paste your key into .env first"
manifest = Path(vals["CUA_CAPABILITY_MANIFEST_PATH"])
assert manifest.is_file(), f"manifest missing: {manifest}"
print("key present, manifest present")
EOF

echo "== 2/6 live model smoke (tool call) =="
RUN_LIVE_MODEL=1 uv run pytest tests/integration/test_live_model.py -q

echo "== 3/6 CUA daemon + bounded manifest =="
if ! pgrep -f "CuaDriver.app/Contents/MacOS/cua-driver serve" >/dev/null; then
  echo "starting bounded daemon..."
  open -n -g -a CuaDriver --args serve \
    --permission-mode bounded \
    --capability-manifest "$(pwd)/config/cua-capabilities.yaml" \
    --approve-capability-manifest
  sleep 4
fi
cua-driver check_permissions

echo "== 4/6 CUA live verification (allowed + denial) =="
uv run python scripts/verify_cua.py --live

echo "== 5/6 full CUA e2e (Calculator 6x7 -> 42) =="
RUN_LIVE_CUA=1 uv run pytest tests/e2e/test_cua_calculator.py -q

echo "== 6/6 Open WebUI + gateway smoke =="
uv run python scripts/smoke_openwebui.py

echo
echo "Go-live checks passed. Remaining manual step: send one chat in Open WebUI"
echo "(http://127.0.0.1:3000) and confirm X-OpenWebUI-* headers in the gateway logs."
