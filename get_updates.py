#!/usr/bin/env python3
"""Dump recent Telegram updates for debugging the MCP bot."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from telegram import Bot

TOKEN_ENV = "TELEGRAM_MCP_BOT_TOKEN"


def pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, sort_keys=True, default=str)
    except Exception:
        return repr(obj)


async def main() -> None:
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} must be set")

    bot = Bot(token=token)

    info = await bot.get_webhook_info()
    payload = {
        "url": info.url,
        "pending_update_count": info.pending_update_count,
        "last_error_date": info.last_error_date,
        "last_error_message": info.last_error_message,
    }
    print("Webhook info:")
    print(pretty(payload))
    print()

    updates = await bot.get_updates()
    print(f"Fetched {len(updates)} updates")
    print()
    for update in updates[-10:]:
        payload = {
            "update_id": update.update_id,
            "chat_id": update.effective_chat.id if update.effective_chat else None,
            "chat_type": update.effective_chat.type if update.effective_chat else None,
            "text": update.message.text if update.message else None,
            "callback_data": update.callback_query.data if update.callback_query else None,
        }
        print(pretty(payload))
        print()

    if updates:
        next_offset = updates[-1].update_id + 1
        print(f"Use offset={next_offset} to discard these updates if needed.")


if __name__ == "__main__":
    asyncio.run(main())
