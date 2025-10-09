#!/bin/bash
set -euo pipefail

DEX_MCP_PID=""
CG_MCP_PID=""

cleanup() {
  if [ -n "$DEX_MCP_PID" ] && kill -0 "$DEX_MCP_PID" 2>/dev/null; then
    kill "$DEX_MCP_PID" 2>/dev/null || true
    wait "$DEX_MCP_PID" 2>/dev/null || true
    DEX_MCP_PID=""
  fi
  if [ -n "$CG_MCP_PID" ] && kill -0 "$CG_MCP_PID" 2>/dev/null; then
    kill "$CG_MCP_PID" 2>/dev/null || true
    wait "$CG_MCP_PID" 2>/dev/null || true
    CG_MCP_PID=""
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
if [ "${SKIP_LOCAL_DEXS_MCP:-${SKIP_LOCAL_MCP_SERVER:-0}}" != "1" ]; then
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
  DEX_MCP_PID=$!
  # Give the server a brief moment to bind its port before the bot connects.
  sleep 1
else
  echo "Skipping Dexscreener MCP server startup (SKIP_LOCAL_DEXS_MCP=1)."
fi

if [ "${SKIP_LOCAL_COINGECKO_MCP:-0}" != "1" ]; then
  case "${COINGECKO_MCP_ENABLED:-}" in
    1|true|TRUE|on|ON|yes|YES)
      COINGECKO_ENABLED=1
      ;;
    *)
      COINGECKO_ENABLED=0
      ;;
  esac

  if [ "$COINGECKO_ENABLED" != "1" ]; then
    echo "COINGECKO_MCP_ENABLED is not truthy; skipping Coingecko MCP server startup." >&2
  else
    COINGECKO_KEY=${COINGECKO_PRO_API_KEY:-${COINGECKO_API_KEY:-}}
    if ! command -v npx >/dev/null 2>&1; then
      echo "Cannot start Coingecko MCP server because 'npx' is not available." >&2
    else
      if [ -z "$COINGECKO_KEY" ]; then
        echo "COINGECKO_PRO_API_KEY/COINGECKO_API_KEY is not set; skipping Coingecko MCP server startup." >&2
      else
        echo "Starting Coingecko MCP server..."
        env COINGECKO_PRO_API_KEY="$COINGECKO_KEY" \
            COINGECKO_ENVIRONMENT="${COINGECKO_ENVIRONMENT:-demo}" \
            npx -y @coingecko/coingecko-mcp >/tmp/coingecko-mcp.log 2>&1 &
        CG_MCP_PID=$!
        sleep 1
      fi
    fi
  fi
else
  echo "Skipping Coingecko MCP server startup (SKIP_LOCAL_COINGECKO_MCP=1)."
fi

echo "Starting Telegram MCP application..."
python -m mcp_app_telegram
APP_EXIT_CODE=$?


cleanup

exit "$APP_EXIT_CODE"
