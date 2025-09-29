#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -d "venv" ]; then
  echo "Activating local virtual environment..."
  # shellcheck source=/dev/null
  . "venv/bin/activate"
else
  echo "No local virtual environment detected; falling back to system Python."
fi

# Boot a local MCP EVM server by default; opt out with SKIP_LOCAL_MCP_SERVER=1.
echo "Starting Telegram MCP application..."
exec python -m mcp_app_telegram
