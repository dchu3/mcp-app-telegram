# Repository Guidelines

## Project Structure & Module Organization
Keep Python sources inside a package such as `mcp_app_telegram/`; group bot entrypoints in `bot.py`, Telegram handlers in `handlers/`, and shared utilities in `services/`. Place asynchronous integration tests under `tests/` matching package paths (e.g., `tests/handlers/test_commands.py`). Support scripts live at the repository root (`setup.sh`, `setup_dev.sh`, `requirements*.txt`); update them instead of duplicating logic. Ignore the locally-created `venv/` directory; use it only for experiments.

## Build, Test, and Development Commands
Run `./setup_dev.sh` for a first-time bootstrap; it creates `venv/` and installs both runtime and dev dependencies. Activate the environment with `source venv/bin/activate` before running commands. Execute `pytest` for the full suite, or target a file with `pytest tests/handlers/test_commands.py`. Use `pip install -r requirements.txt` when the production image is refreshed.

## Coding Style & Naming Conventions
Target Python 3.13; enable `from __future__ import annotations` when type relationships matter. Use 4-space indents, type hints, and f-strings for formatting. Name modules and functions with `snake_case`, classes with `PascalCase`, and constants with `UPPER_SNAKE`. Keep async Telegram handlers pure (no side effects beyond the Telegram client) and request helpers idempotent. Run `python -m compileall mcp_app_telegram` before committing if you add low-level imports to catch syntax slips.

## Testing Guidelines
Write new features with pytest tests under mirrored paths, starting files with `test_` and naming async tests `async def`. Leverage `pytest-asyncio` fixtures for coroutine handlers and `pytest-mock` for network boundaries. Provide fixture data in-line or via `tests/fixtures/` if it grows. Ensure coverage for happy-path Telegram updates, error branches, and webhook retries; aim for >=85% module coverage on new code.

## Commit & Pull Request Guidelines
Keep commits focused and titled like `Add message dispatcher`-the history favors concise sentence-case summaries (`Update LICENSE`). Reference issues using `Fixes #123` when applicable. Pull requests need a short motivation, testing notes (`pytest` output), and screenshots or logs for Telegram flows. Checklist any deployment or secret-rotation steps so reviewers can confirm readiness.

## Security & Configuration Tips
Never commit bot tokens or API keys; store them in `.env` or the hosting secret store and load via `os.environ`. Regenerate tokens immediately after accidental exposure. Validate incoming Telegram updates by checking signatures and restrict outbound web3 endpoints to HTTPS. Rotate dependencies periodically by updating `requirements*.txt` and documenting incompatible changes.
