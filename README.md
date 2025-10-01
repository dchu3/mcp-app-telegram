# mcp-app-telegram

Async Telegram bot that surfaces Base Chain data through the MCP EVM server (or a JSON-RPC endpoint fallback). It is designed for operators who want on-chain telemetry delivered straight into chat.

## Prerequisites

- Python 3.13
- Node.js 18+ (needed for `npx @mcpdotdirect/evm-mcp-server`)
- Telegram bot token with API access
- MCP-compatible EVM server **or** a Base RPC endpoint (e.g., `https://mainnet.base.org`)
- Gemini API key (optional, enables natural-language queries via Gemini)

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
   export GEMINI_API_KEY="<gemini-api-key>"  # optional, required for natural-language queries
   ```
   Additional knobs:
   - `MCP_SERVERS` – JSON array of MCP server definitions (see below) when running multiple integrations.
   - `MCP_PRIMARY_EVM` / `MCP_PRIMARY_DEXSCREENER` – pick the default server keys for bot commands.
   - `COINGECKO_PRO_API_KEY` / `COINGECKO_API_KEY` – when set, automatically enables the Coingecko MCP server (override command with `COINGECKO_MCP_COMMAND`).
   - `GEMINI_MODEL` – override the Gemini model used for Gemini-powered responses (default `gemini-1.5-flash-latest`).
   - `GEMINI_PERSONA` – optional system prompt to shape the agent's voice/persona.
   - `TELEGRAM_HTTP_READ_TIMEOUT` / `TELEGRAM_HTTP_CONNECT_TIMEOUT` – override Telegram HTTP timeouts.
   - Legacy environment variables (`MCP_EVM_BASE_URL`, `MCP_EVM_PROTOCOL`, `MCP_EVM_SERVER_COMMAND`, `MCP_EVM_NETWORK`, `DEXSCREENER_MCP_*`) remain supported and populate a single-server configuration when `MCP_SERVERS` is not set.

   Example `MCP_SERVERS` payload:
   ```json
   [
     {
       "key": "base-evm",
       "kind": "evm",
       "protocol": "json-rpc",
       "base_url": "https://mainnet.base.org",
       "network": "base"
     },
     {
       "key": "dex-central",
       "kind": "dexscreener",
       "command": ["node", "/opt/dexscreener/index.js"]
     },
     {
       "key": "coingecko",
       "kind": "coingecko",
       "command": ["npx", "-y", "@coingecko/coingecko-mcp"],
       "env": {
         "COINGECKO_PRO_API_KEY": "abcd1234",
         "COINGECKO_ENVIRONMENT": "pro"
       }
     }
   ]
   ```
   Set `MCP_PRIMARY_EVM=base-evm` to make the Base RPC the default target for bot commands.

## Running the bot

Use the helper script so the virtual environment is activated automatically:
```bash
./start.sh
```
The bot starts long-polling until you press `Ctrl+C`. When running in MCP mode it automatically spawns the EVM MCP server via stdio; override the command with `MCP_EVM_SERVER_COMMAND` if you host your own build.

## Telegram Commands

- `/help` – quick reference for all available commands.
- Send a normal message – Gemini agent interprets the request and invokes an MCP tool when helpful.
- `/gas` – current gas tiers, sequencer lag, and base fee for the default network.
- `/account <address>` – account balance, nonce, and contract status (supports both MCP and JSON-RPC backends).
- `/transaction <hash>` – summary of a transaction, including status and gas usage.
- `/gasalert <network> <threshold>` / `/gasalertabove <network> <threshold>` – one-off alerts when fast gas drops below or rises above a threshold for the specified network.
- `/cleargasalerts` – clear pending gas alerts for the chat.
- `/gasalerts` – list every active gas alert tied to the chat.

When a Gemini API key is configured the bot routes free-form chat messages through the agent, which picks between gas stats, account, and transaction lookups (and any additional tools you register). Without the API key the bot replies with setup guidance.

## Testing

Run the full suite with:
```bash
pytest
```
All tests are async-friendly and mock external services.
