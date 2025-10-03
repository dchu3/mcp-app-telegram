#!/usr/bin/env python3
"""Utility script to register Telegram bot commands without starting MCP services."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from telegram import (
    Bot,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)

from mcp_app_telegram.bot import TELEGRAM_COMMANDS


async def register_commands() -> None:
    token = os.getenv("TELEGRAM_MCP_BOT_TOKEN")
    chat_id_raw = os.getenv("TELEGRAM_CHAT_ID")

    if not token:
        raise SystemExit("TELEGRAM_MCP_BOT_TOKEN must be set")
    if not chat_id_raw:
        raise SystemExit("TELEGRAM_CHAT_ID must be set")

    try:
        chat_id = int(chat_id_raw)
    except ValueError as exc:
        raise SystemExit("TELEGRAM_CHAT_ID must be an integer") from exc

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    bot = Bot(token=token)

    await bot.set_my_commands(TELEGRAM_COMMANDS)
    logging.info(
        "Registered global commands: %s",
        ", ".join(cmd.command for cmd in TELEGRAM_COMMANDS),
    )

    for scope_name, scope in (
        ("default", BotCommandScopeDefault()),
        ("private", BotCommandScopeAllPrivateChats()),
        ("group", BotCommandScopeAllGroupChats()),
    ):
        await bot.set_my_commands(TELEGRAM_COMMANDS, scope=scope)
        logging.info("Registered %s commands", scope_name)


def main() -> None:
    try:
        asyncio.run(register_commands())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
