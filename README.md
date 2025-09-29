# mcp-app-telegram

Async Telegram bot that surfaces Base Chain data through the MCP EVM server (or a JSON-RPC endpoint fallback). It is designed for operators who want on-chain telemetry delivered straight into chat.

## Prerequisites

- Python 3.13
- Node.js 18+ (needed for `npx @mcpdotdirect/evm-mcp-server`)
- Telegram bot token with API access
- MCP-compatible EVM server **or** a Base RPC endpoint (e.g., `https://mainnet.base.org`)

## Setup

1. Install dependencies and prepare a virtual environment:
   ```bash
   ./setup_dev.sh
   ```
2. Activate the environment when working locally:
   ```bash
   source venv/bin/activate
   ```
3. Provide the required environment variables:
   ```bash
   export TELEGRAM_MCP_BOT_TOKEN="<bot-token>"
   export TELEGRAM_CHAT_ID="<default-chat-id>"
   export ONCHAIN_VALIDATION_RPC_URL="https://mainnet.base.org"  # optional JSON-RPC fallback
   ```
   Additional knobs:
   - `MCP_EVM_BASE_URL` – direct MCP endpoint (defaults to `http://localhost:8080`).
   - `MCP_EVM_PROTOCOL` – force `mcp` or `json-rpc`.
   - `MCP_EVM_SERVER_COMMAND` – override the spawned stdio server command (default `npx -y @mcpdotdirect/evm-mcp-server`).
   - `MCP_EVM_NETWORK` – target network for MCP tool calls (defaults to `base`).
   - `TELEGRAM_HTTP_READ_TIMEOUT` / `TELEGRAM_HTTP_CONNECT_TIMEOUT` – override Telegram HTTP timeouts.

## Running the bot

Use the helper script so the virtual environment is activated automatically:
```bash
./start.sh
```
The bot starts long-polling until you press `Ctrl+C`. When running in MCP mode it automatically spawns the EVM MCP server via stdio; override the command with `MCP_EVM_SERVER_COMMAND` if you host your own build.

## Telegram Commands

- `/gas` – current Base gas tiers with sequencer lag and base fee.
- `/account <address>` – account balance, nonce, and contract status (supports both MCP and JSON-RPC backends).
- `/tx <hash>` – summary of a transaction, including status and gas usage.
- `/gas_sub <threshold>` / `/gas_sub_above <threshold>` – one-off alerts when fast gas drops below or rises above a threshold.
- `/gas_clear` – clear pending gas alerts for the chat.

## Testing

Run the full suite with:
```bash
pytest
```
All tests are async-friendly and mock external services.
