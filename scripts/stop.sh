#!/bin/bash
# Stop what start.sh started: the gateway process, then (on request) the
# Docker containers. Leaves the Cua Driver daemon running -- it's a login
# LaunchAgent shared by other tools (e.g. Claude Code's own Terminal
# automation is in its allowlist), not something this project owns.
set -uo pipefail
cd "$(dirname "$0")/.."

env_get() { grep -E "^${1}=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }
APP_PORT="$(env_get APP_PORT)"; APP_PORT="${APP_PORT:-8787}"

PID="$(lsof -tiTCP:"${APP_PORT}" -sTCP:LISTEN 2>/dev/null | head -1)"
if [ -n "${PID:-}" ]; then
  echo "Stopping gateway on :${APP_PORT} (pid $PID)..."
  kill "$PID" 2>/dev/null || true
  for _ in $(seq 1 10); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done
  kill -9 "$PID" 2>/dev/null || true
  echo "Gateway stopped."
else
  echo "No gateway process found listening on :${APP_PORT}."
fi
rm -f var/gateway.pid

echo
read -r -p "Also stop the Docker containers (postgres, open-webui)? [y/N] " reply || reply="n"
if [[ "$reply" =~ ^[Yy]$ ]]; then
  docker compose stop
  echo "Containers stopped. (Restart with: docker compose up -d, or just run ./scripts/start.sh again.)"
else
  echo "Containers left running. Stop later with: docker compose stop"
fi

echo
echo "Cua Driver daemon left running (shared login service)."
echo "Full manual stop if you really want it: quit CuaDriver.app, or:"
echo "  launchctl bootout gui/$(id -u)/com.trycua.cua_driver_daemon"
