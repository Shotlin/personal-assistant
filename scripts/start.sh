#!/bin/bash
# One-command local bring-up: containers -> DB schema -> Cua Driver check ->
# gateway -> chat UI in the browser. Safe to re-run: every step is idempotent
# and already-running pieces are detected and skipped.
#
# Usage: ./scripts/start.sh
# Stop:  ./scripts/stop.sh
set -euo pipefail
cd "$(dirname "$0")/.."

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.uv-python}"
export PATH="$HOME/.local/bin:$PATH"

mkdir -p var
GATEWAY_LOG="$PWD/var/gateway.log"
GATEWAY_PID_FILE="$PWD/var/gateway.pid"

# Reads a simple KEY=VALUE from .env (last match wins, no quoting support
# needed -- this project's .env never quotes values).
env_get() { grep -E "^${1}=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }

echo "== 1/7 .env =="
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
  echo "Edit it now and set at least AGENT_GATEWAY_API_KEY and OPENROUTER_API_KEY, then re-run this script."
  exit 1
fi

MODEL_PROVIDER="$(env_get MODEL_PROVIDER)"; MODEL_PROVIDER="${MODEL_PROVIDER:-openrouter}"
GATEWAY_KEY="$(env_get AGENT_GATEWAY_API_KEY)"
if [ -z "$GATEWAY_KEY" ] || [ "$GATEWAY_KEY" = "change-me" ]; then
  echo "AGENT_GATEWAY_API_KEY in .env is empty or still the placeholder 'change-me'."
  echo "Set it to a long random string, then re-run this script."
  exit 1
fi
if [ "$MODEL_PROVIDER" = "openrouter" ] && [ -z "$(env_get OPENROUTER_API_KEY)" ]; then
  echo "MODEL_PROVIDER=openrouter but OPENROUTER_API_KEY is empty in .env. Add your key, then re-run."
  exit 1
fi
echo ".env looks configured (provider: $MODEL_PROVIDER)"

echo "== 2/7 Docker =="
if ! docker info >/dev/null 2>&1; then
  echo "Docker does not appear to be running. Start Docker Desktop, then re-run this script."
  exit 1
fi

echo "== 3/7 dependencies (uv sync --frozen) =="
uv sync --frozen

echo "== 4/7 containers (postgres + open-webui) =="
docker compose up -d --wait --wait-timeout 120

echo "== 5/7 database schema (idempotent) =="
uv run python scripts/init_db.py

echo "== 6/7 Cua Driver daemon =="
CUA_ENABLED="$(env_get CUA_ENABLED)"; CUA_ENABLED="${CUA_ENABLED:-true}"
if [ "$CUA_ENABLED" = "true" ]; then
  if ! command -v cua-driver >/dev/null 2>&1; then
    echo "WARNING: CUA_ENABLED=true but 'cua-driver' is not on PATH -- gateway startup will fail."
    echo "  Install it (see README 'Cua Driver') or set CUA_ENABLED=false in .env."
  else
    bounded_ready() {
      cua-driver status 2>&1 | grep -q "permission mode: bounded" \
        && cua-driver status 2>&1 | grep -q "capability manifest: configured=true"
    }
    if bounded_ready; then
      echo "cua-driver daemon already running (bounded, manifest approved)"
    else
      echo "cua-driver daemon not ready; kicking the LaunchAgent..."
      launchctl kickstart -k "gui/$(id -u)/com.trycua.cua_driver_daemon" 2>/dev/null || true
      sleep 4
      if bounded_ready; then
        echo "cua-driver daemon now running (bounded, manifest approved)"
      else
        echo "WARNING: cua-driver daemon still not confirmed bounded/manifest-approved."
        echo "  The gateway will refuse to expose desktop-control tools until this is fixed."
        echo "  Check: cua-driver status   (see README Troubleshooting)"
      fi
    fi
  fi
else
  echo "CUA_ENABLED=false in .env; skipping (chat-only mode, no desktop control)."
fi

echo "== 7/7 gateway + chat UI =="
APP_PORT="$(env_get APP_PORT)"; APP_PORT="${APP_PORT:-8787}"
if curl -fsS "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null 2>&1; then
  echo "Gateway already running on :${APP_PORT}"
else
  echo "Starting gateway in the background (log: var/gateway.log)..."
  nohup ./scripts/run_agent_api.sh > "$GATEWAY_LOG" 2>&1 &
  disown
  ready=""
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${APP_PORT}/healthz" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ -z "$ready" ]; then
    echo "Gateway did not become healthy within 30s -- check $GATEWAY_LOG"
    exit 1
  fi
  lsof -tiTCP:"${APP_PORT}" -sTCP:LISTEN 2>/dev/null | head -1 > "$GATEWAY_PID_FILE" || true
  echo "Gateway is up (pid $(cat "$GATEWAY_PID_FILE" 2>/dev/null || echo '?'))"
fi

WEBUI_URL="http://127.0.0.1:3000"
echo
echo "Personal Assistant is live:"
echo "  Chat UI : $WEBUI_URL"
echo "  Gateway : http://127.0.0.1:${APP_PORT}  (OpenAPI docs at /docs)"
echo "  Logs    : $GATEWAY_LOG"
echo "  Stop    : ./scripts/stop.sh"
echo
echo "First time only: create the Open WebUI admin account when the page opens,"
echo "then check Admin Settings -> Connections for the preconfigured gateway."
echo

if command -v open >/dev/null 2>&1; then
  open "$WEBUI_URL"
fi
