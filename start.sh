#!/bin/bash
set -euo pipefail

MCP_SERVER_PID=""

cleanup() {
  if [ -n "$MCP_SERVER_PID" ] && kill -0 "$MCP_SERVER_PID" 2>/dev/null; then
    kill "$MCP_SERVER_PID" 2>/dev/null || true
    wait "$MCP_SERVER_PID" 2>/dev/null || true
    MCP_SERVER_PID=""
  fi
}

trap cleanup EXIT INT TERM

cd "$(dirname "$0")"

if [ -d "venv" ]; then
  echo "Activating local virtual environment..."
  # shellcheck source=/dev/null
  . "venv/bin/activate"
else
  echo "No local virtual environment detected; falling back to system Python."
fi

# Optionally boot the Dexscreener MCP server in the background.
if [ "${SKIP_LOCAL_MCP_SERVER:-0}" != "1" ]; then
  if [ -z "${DEXSCREENER_MCP_ROOT:-}" ]; then
    echo "Refusing to start Dexscreener MCP server: DEXSCREENER_MCP_ROOT is not set." >&2
    echo "Set DEXSCREENER_MCP_ROOT to the directory containing mcp-dexscreener/index.js." >&2
    exit 1
  fi

  MCP_SERVER_ENTRY="${DEXSCREENER_MCP_ROOT%/}/index.js"

  if [ ! -f "$MCP_SERVER_ENTRY" ]; then
    echo "Dexscreener MCP server entrypoint not found at '$MCP_SERVER_ENTRY'." >&2
    exit 1
  fi

  echo "Starting Dexscreener MCP server..."
  node "$MCP_SERVER_ENTRY" &
  MCP_SERVER_PID=$!
  # Give the server a brief moment to bind its port before the bot connects.
  sleep 1
else
  echo "Skipping Dexscreener MCP server startup (SKIP_LOCAL_MCP_SERVER=1)."
fi

echo "Starting Telegram MCP application..."
python -m mcp_app_telegram
APP_EXIT_CODE=$?

cleanup

exit "$APP_EXIT_CODE"
