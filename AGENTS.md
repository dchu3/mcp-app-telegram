# Repository Guidelines

## Project Structure & Module Organization
All runtime code lives in `mcp_app_telegram/`. Keep configuration helpers in `config.py`, Telegram wiring in `bot.py`, long-lived clients in `mcp_client.py`, LLM tooling in `gemini_agent.py`, and shared formatting in `formatting.py`. Add new async services as modules within this package. Tests mirror this layout inside `tests/` (e.g., `tests/test_mcp_client.py`, `tests/test_gemini_agent.py`). Shell helpers such as `setup_dev.sh` and `start.sh` sit at the repository root and should be updated rather than duplicated.

## Build, Test, and Development Commands
Bootstrap a workspace with `./setup_dev.sh`, then activate the virtualenv via `source venv/bin/activate`. Install runtime-only dependencies with `pip install -r requirements.txt` when packaging or deploying. Run the full suite using `pytest`; narrow to a module while iterating (for example `pytest tests/test_formatting.py` or `pytest tests/test_gemini_agent.py::test_agent_runs_gas_tool`). Use `./start.sh` to launch the bot with proper environment activation.

## Coding Style & Naming Conventions
Target Python 3.13 and keep `from __future__ import annotations` at the top of new modules. Use 4-space indentation, type hints, and descriptive async function names (`fetch_account`, `handle_command`). Stick to `snake_case` for functions/variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. When expanding the Telegram command set (e.g., `/gasalert`), add skinny handlers in `bot.py`, route logic into helper modules such as `gemini_agent.py`, and remember to keep the `/help` copy and README in sync with the command list.

## Testing Guidelines
Write pytest-based tests that mirror the module under test (`tests/test_bot_handlers.py`, `tests/test_gemini_agent.py`, etc.). Use `pytest.mark.asyncio` for coroutine tests and stub network boundaries with `httpx.MockTransport`, AsyncMock, or PTB test harnesses. Cover both MCP and JSON-RPC branches when touching the client, and add plan/execution cases for agent logic to prove tool selection. Aim for ≥85% coverage on new modules and include regression tests for every command.

## Commit & Pull Request Guidelines
Keep commits scoped to a single concern with sentence-case titles (e.g., `Add account summary command`). Reference issues via `Fixes #123` when applicable. Pull requests should include a short description, testing proof (`pytest`), and—if the change affects bot behavior—sample Telegram output or screenshots to help reviewers validate UX.

## Configuration & Operations Tips
Credentials (`TELEGRAM_MCP_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) must be sourced from environment variables. Declare multiple server connections via `MCP_SERVERS` (JSON) and pick defaults with `MCP_PRIMARY_EVM` / `MCP_PRIMARY_DEXSCREENER`. Legacy variables such as `ONCHAIN_VALIDATION_RPC_URL`, `MCP_EVM_BASE_URL`, and `MCP_EVM_PROTOCOL` still backfill a single EVM configuration when the JSON payload is omitted. Set `COINGECKO_PRO_API_KEY` (and optionally `COINGECKO_MCP_COMMAND`) to wire in the Coingecko MCP integration. Provide `GEMINI_API_KEY` (and optionally `GEMINI_MODEL_MCP` / `GEMINI_PERSONA`) to enable agent-driven text replies. Tune Telegram HTTP timeouts with `TELEGRAM_HTTP_READ_TIMEOUT` and `TELEGRAM_HTTP_CONNECT_TIMEOUT` when running in slow environments. Never commit secrets, and rotate RPC endpoints or bot tokens immediately after exposure.
