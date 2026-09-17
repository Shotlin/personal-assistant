#!/bin/bash
# Start the native Personal Assistant gateway (FastAPI) with validated settings.
# Prerequisites:
#   - uv installed (https://docs.astral.sh/uv/)
#   - .env present (copy .env.example and fill in real values)
#   - compose services running: docker compose up -d postgres
#   - (when CUA_ENABLED=true) Cua Driver installed, bounded daemon started,
#     and the capability manifest present at CUA_CAPABILITY_MANIFEST_PATH
set -euo pipefail
cd "$(dirname "$0")/.."

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.uv-python}"

# Install exactly the committed lock file (never re-resolve).
uv sync --frozen

exec uv run uvicorn assistant.main:create_application \
  --factory \
  --host "${APP_HOST:-127.0.0.1}" \
  --port "${APP_PORT:-8787}"
