# GEMINI.md

## Project Overview

This project is a Python-based Telegram bot that provides data from the Base blockchain. It is designed for operators who want on-chain telemetry delivered straight into a chat interface.

The bot can connect to an MCP EVM server for data retrieval or fall back to a standard JSON-RPC endpoint. It also features an optional integration with the Gemini API, allowing users to make natural language queries about the blockchain.

**Key Technologies:**

*   Python 3.13
*   `python-telegram-bot`: For interacting with the Telegram Bot API.
*   `web3`: For interacting with the Ethereum blockchain.
*   `google-generativeai`: For the optional Gemini integration.
*   `asyncio`: For asynchronous operations.
*   `pytest`: For testing.

**Architecture:**

The application is built on an asynchronous architecture using `asyncio`. The code is organized into several modules:

*   `mcp_app_telegram/app.py`: The main application entry point that initializes and runs the bot.
*   `mcp_app_telegram/bot.py`: Contains the Telegram bot's command handlers and core logic.
*   `mcp_app_telegram/config.py`: Manages the application's configuration, which is loaded from environment variables.
*   `mcp_app_telegram/mcp_client.py`: A client for interacting with the MCP EVM server.
*   `mcp_app_telegram/gemini_agent.py`: An agent for handling natural language queries via the Gemini API.
*   `tests/`: Contains the project's test suite.

## Building and Running

**1. Setup:**

To set up the development environment, install the required dependencies using the provided script:

```bash
./setup_dev.sh
```

This will create a Python virtual environment and install the necessary packages from `requirements.txt` and `requirements-dev.txt`.

**2. Configuration:**

The bot is configured using environment variables. The following variables are required:

*   `TELEGRAM_MCP_BOT_TOKEN`: Your Telegram bot token.
*   `TELEGRAM_CHAT_ID`: The default chat ID for the bot.
*   `ONCHAIN_VALIDATION_RPC_URL`: A Base RPC endpoint (e.g., `https://mainnet.base.org`).

For the optional Gemini integration, you also need to set:

*   `GEMINI_API_KEY`: Your Gemini API key.

**3. Running the Bot:**

To start the bot, run the following command:

```bash
./start.sh
```

This script activates the virtual environment and starts the bot.

**4. Running Tests:**

To run the test suite, use `pytest`:

```bash
pytest
```

## Development Conventions

*   **Asynchronous Code:** The project uses `asyncio` extensively. All I/O operations should be non-blocking.
*   **Type Hinting:** The codebase uses Python's type hinting for improved readability and static analysis.
*   **Configuration:** All configuration is managed through environment variables, as defined in `mcp_app_telegram/config.py`.
*   **Testing:** The project has a comprehensive test suite in the `tests/` directory. New features should be accompanied by corresponding tests.
*   **Linting:** The project uses `ruff` for linting. You can run the linter with `ruff check .`.
