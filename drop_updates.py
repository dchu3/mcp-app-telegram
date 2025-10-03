#!/usr/bin/env python3
"""Advance getUpdates offset to discard pending Telegram updates."""
from __future__ import annotations

import asyncio
import os
import sys

from telegram import Bot

TOKEN_ENV = "TELEGRAM_MCP_BOT_TOKEN"

async def main() -> None:
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} must be set")

    try:
        offset_arg = sys.argv[1]
    except IndexError:
        raise SystemExit("Usage: drop_updates.py <offset>")

    try:
        offset = int(offset_arg)
    except ValueError as exc:
        raise SystemExit("Offset must be an integer") from exc

    bot = Bot(token=token)
    updates = await bot.get_updates(offset=offset)
    print(f"Advanced offset to {offset}; dropped {len(updates)} updates.")


if __name__ == "__main__":
    asyncio.run(main())
