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
   - `COINGECKO_MCP_ENABLED` – opt-in flag for the Coingecko MCP integration (defaults to `false`).
   - `COINGECKO_PRO_API_KEY` / `COINGECKO_API_KEY` – credentials passed to the Coingecko MCP server once enabled (override the command with `COINGECKO_MCP_COMMAND`).
   - `GEMINI_MODEL_MCP` – override the Gemini model used for Gemini-powered responses (default `gemini-1.5-flash-latest`).
   - `GEMINI_PERSONA` – optional system prompt to shape the agent's voice/persona.
   - `TELEGRAM_HTTP_READ_TIMEOUT` / `TELEGRAM_HTTP_CONNECT_TIMEOUT` – override Telegram HTTP timeouts.
   - `ARB_MIN_LIQUIDITY_USD` / `ARB_MIN_VOLUME_24H_USD` / `ARB_MIN_TXNS_24H` – enforce per-venue liquidity, 24h volume (USD), and 24h transaction minimums before an arbitrage snapshot is considered (defaults: 50k / 100k / 2,400).
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

### Admin console

`./start.sh` now boots an interactive admin console alongside the bot. The CLI shares the same event loop as the Telegram worker, so any changes take effect immediately:

- `token add <pair_key> --symbols TK/USDC --base-symbol TK --quote-symbol USDC --base-address 0x... [--quote-address 0x...] [--dex-id foo] [--fee-tier 0.30]`
- `token set-thresholds <pair_key> [--min-liquidity 25000] [--min-volume 50000] [--min-txns 500]`
- `token remove <pair_key>`
- `settings set-global [--min-liquidity ...] [--min-volume ...] [--min-txns ...]`
- `settings set-mev --bps 12`
- `arb-profile set [--min-net-bps ...] [--test-size-eur ...] [--slippage-cap-bps ...]`
- `arb-profile reset`
- `log [n]`
- `help` / `quit`

State is persisted to `data/admin_state.db` by default; override the location with `ADMIN_STATE_PATH=/path/to/state.db`. Legacy JSON files (`admin_state.json`) are migrated into SQLite automatically on first run. To disable the console entirely—useful for containerized or non-interactive deployments—set `DISABLE_ADMIN_CONSOLE=1` before launching.

When the console is active the standard output handler switches to quiet mode (warnings and errors only) so that the prompt remains readable. Use the inline `log` command to view the latest entries recorded by the in-memory buffer, or export `ADMIN_CONSOLE_VERBOSE=1` to restore the original console log level. Adjust the buffer depth by setting `ADMIN_CONSOLE_LOG_CAPACITY` (default `500`).

### Registering commands only

If you just want to verify Telegram command registration without starting any MCP servers, run:
```bash
./register_commands.py
```
The script looks for `TELEGRAM_MCP_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, clears existing commands, and registers the bot command list across default, private, group, and chat scopes.

## Telegram Commands

- `/help` – quick reference for the enabled commands.
- `/gas` – current gas tiers, sequencer lag, and base fee for the primary network.
- `/pairs` – list every tracked arbitrage pair with age metadata.
- `/sub <index|pair>` – subscribe this chat to a tracked pair (`/pairs` shows the 1-based index and the literal `pair_key`, e.g. `/sub 1` or `/sub base:foo/usdc@dex`).
- `/unsub <index|pair>` – remove a tracked pair subscription.
- `/mysubs` – display the chat’s explicit subscriptions (and whether the global toggle is active).
- `/suball` – subscribe the chat to all tracked pairs (if configuration allows).
- `/unsuball` – clear the global subscription toggle.

Other legacy commands (account lookups, transaction summaries, gas alert management, etc.) remain implemented but are hidden from the Telegram command list while the app focuses on gas telemetry and arbitrage workflows. They can be re-enabled later by expanding the command menu and help text to include the dormant handlers.

Gemini-free installs still support the commands above; only the free-form text routing falls back to a setup reminder when no Gemini API key is present.

## Testing

Run the full suite with:
```bash
./venv-dev/bin/pytest
```
or activate the developer environment (`source venv-dev/bin/activate`) and use `pytest`. All tests are async-friendly and mock external services.

## Debugging Telegram Delivery

When commands appear in Telegram but the bot never replies, verify the long-polling loop and clear any stale webhook or update backlog:

1. Launch with verbose logging:
   ```bash
   LOG_LEVEL=DEBUG python -m mcp_app_telegram
   ```
   Successful startup prints:
   - `Cleared existing Telegram webhook`
   - `Starting Telegram long polling`
   - `Telegram long polling started (running=True)`
   - `Application worker tasks started`

   The debug trace also shows recurring `getUpdates` calls. If these lines never appear or carry warnings, polling failed to initialize.

2. Inspect pending updates in another shell:
   ```bash
   python get_updates.py
   ```
   Check `pending_update_count` and the most recent `update_id`. A large count means Telegram is still holding undelivered updates.

3. Drain the backlog when needed:
   ```bash
   python drop_updates.py
   ```
   This advances the offset on Telegram’s side so the next polling cycle starts fresh.

4. Resend `/help`. In debug mode you should see an `Incoming update: {...}` log entry as the handler fires. If the queue stays empty in `getUpdates`, ensure no webhook is registered (step 1) and that only a single process is polling the token.

These utilities run entirely against the Bot API—no MCP servers or Gemini key required—so they are safe to use even in production.
