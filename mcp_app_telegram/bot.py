"""Telegram bot wiring for the MCP EVM integration."""

from __future__ import annotations

import logging
from functools import partial
from typing import Optional

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

from .alerts import GasAlertManager, GasAlertSubscription
from .config import Config
from .formatting import format_account, format_gas_stats, format_transaction
from .gemini_agent import GeminiAgent, GeminiAgentError
from .mcp_client import DexscreenerMcpClient, EvmMcpClient, McpClientError

_LOGGER = logging.getLogger(__name__)

REFRESH_QUERY = "gas_refresh"

TELEGRAM_COMMANDS = [
    BotCommand("help", "Show available commands"),
    BotCommand("ask", "Ask Gemini to run an MCP tool"),
    BotCommand("gas", "Show Base gas stats"),
    BotCommand("account", "Show account balance and nonce"),
    BotCommand("tx", "Summarize transaction status"),
    BotCommand("gas_sub", "Alert when fast gas drops below threshold"),
    BotCommand("gas_sub_above", "Alert when fast gas rises above threshold"),
    BotCommand("gas_clear", "Clear gas alerts for this chat"),
]


_HELP_TEXT = (
    "Here are the commands I understand:\n"
    "- /ask <question> : Gemini agent picks an MCP tool to answer.\n"
    "- /gas : Base gas tiers, base fee, and sequencer lag.\n"
    "- /account <address> : Balance, nonce, and contract status for an address.\n"
    "- /tx <hash> : Transaction status, gas used, and value.\n"
    "- /gas_sub <gwei> : Alert when fast gas drops below a threshold.\n"
    "- /gas_sub_above <gwei> : Alert when fast gas rises above a threshold.\n"
    "- /gas_clear : Clear pending gas alerts in this chat."
)

_TELEGRAM_MESSAGE_LIMIT = 4000


async def _reply_text_chunks(message, text: str) -> None:
    text = text.strip()
    if not text:
        return

    if len(text) <= _TELEGRAM_MESSAGE_LIMIT:
        await message.reply_text(text)
        return

    cursor = 0
    length = len(text)
    while cursor < length:
        remaining = text[cursor:]
        if len(remaining) <= _TELEGRAM_MESSAGE_LIMIT:
            chunk = remaining
            cursor = length
        else:
            window = remaining[:_TELEGRAM_MESSAGE_LIMIT]
            split = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
            if split <= 0:
                split = _TELEGRAM_MESSAGE_LIMIT
            chunk = window[:split]
            cursor += split
        await message.reply_text(chunk.strip())


async def _handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(_HELP_TEXT)


async def _handle_gas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: EvmMcpClient = context.application.bot_data["mcp_client"]
    try:
        stats = await client.fetch_gas_stats()
    except Exception as exc:  # pragma: no cover - network failure guard
        _LOGGER.exception("Failed to load gas stats")
        await update.effective_message.reply_text(f"Error fetching gas stats: {exc}")
        return

    text = format_gas_stats(stats)
    keyboard = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton("Refresh", callback_data=REFRESH_QUERY)
    )
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def _handle_gas_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    client: EvmMcpClient = context.application.bot_data["mcp_client"]
    try:
        stats = await client.fetch_gas_stats()
    except Exception as exc:  # pragma: no cover
        await query.edit_message_text(f"Error fetching gas stats: {exc}")
        return

    await query.edit_message_text(format_gas_stats(stats), reply_markup=query.message.reply_markup)


async def _handle_tx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /tx <transaction-hash>")
        return
    tx_hash = context.args[0]
    client: EvmMcpClient = context.application.bot_data["mcp_client"]
    try:
        summary = await client.fetch_transaction(tx_hash)
    except McpClientError as exc:
        await update.effective_message.reply_text(f"MCP error: {exc}")
        return
    except Exception as exc:  # pragma: no cover
        _LOGGER.exception("Transaction lookup failed")
        await update.effective_message.reply_text(f"Error fetching transaction: {exc}")
        return

    await update.effective_message.reply_text(format_transaction(summary))


async def _handle_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /ask <question>")
        return

    agent: Optional[GeminiAgent] = context.application.bot_data.get("agent")
    question = " ".join(context.args).strip()

    if agent is None:
        await update.effective_message.reply_text(
            "The Gemini agent is not configured. Set GEMINI_API_KEY to enable /ask."
        )
        return

    try:
        answer = await agent.answer(question)
    except GeminiAgentError as exc:
        await update.effective_message.reply_text(f"Gemini agent error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive guard
        _LOGGER.exception("Unexpected failure in /ask handler")
        await update.effective_message.reply_text("An unexpected error occurred while answering.")
    else:
        await _reply_text_chunks(update.effective_message, answer)



async def _handle_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /account <address>")
        return

    address = context.args[0]
    if not address.startswith("0x") or len(address) != 42 or any(ch not in "0123456789abcdefABCDEF" for ch in address[2:]):
        await update.effective_message.reply_text("Address must be a 42-character hex string starting with 0x.")
        return

    client: EvmMcpClient = context.application.bot_data["mcp_client"]
    try:
        summary = await client.fetch_account(address.lower())
    except McpClientError as exc:
        await update.effective_message.reply_text(f"MCP error: {exc}")
        return
    except Exception as exc:  # pragma: no cover
        _LOGGER.exception("Account lookup failed")
        await update.effective_message.reply_text(f"Error fetching account: {exc}")
        return

    await update.effective_message.reply_text(format_account(summary))


async def _handle_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: str) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /gas_sub <threshold_gwei>")
        return
    try:
        threshold = float(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Threshold must be a number.")
        return

    alert_manager: GasAlertManager = context.application.bot_data["alert_manager"]
    subscription = GasAlertSubscription(
        chat_id=update.effective_chat.id,
        threshold=threshold,
        direction=direction,
    )
    await alert_manager.add_subscription(subscription)
    await update.effective_message.reply_text(
        f"Subscribed to alerts when {subscription.describe()}"
    )


async def _handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    alert_manager: GasAlertManager = context.application.bot_data["alert_manager"]
    await alert_manager.clear_for_chat(update.effective_chat.id)
    await update.effective_message.reply_text("Cleared gas alert subscriptions for this chat.")


async def gas_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    client: EvmMcpClient = context.bot_data["mcp_client"]
    alert_manager: GasAlertManager = context.bot_data["alert_manager"]
    config: Config = context.bot_data["config"]

    try:
        stats = await client.fetch_gas_stats()
    except Exception as exc:  # pragma: no cover
        _LOGGER.warning("Skipping alert evaluation due to MCP error: %s", exc)
        return

    matches = await alert_manager.evaluate(stats)
    if config.gas_alert_threshold is not None and stats.fast <= config.gas_alert_threshold:
        matches = tuple(matches) + (
            GasAlertSubscription(
                chat_id=config.telegram_chat_id,
                threshold=config.gas_alert_threshold,
                direction="below",
            ),
        )

    if not matches:
        return

    message = format_gas_stats(stats)
    for subscription in matches:
        try:
            await context.bot.send_message(
                chat_id=subscription.chat_id,
                text=f"Gas alert triggered ({subscription.describe()}):\n{message}",
            )
        except Exception as exc:  # pragma: no cover
            _LOGGER.warning("Failed to send alert to %s: %s", subscription.chat_id, exc)


def build_application(
    config: Config,
    client: EvmMcpClient,
    alert_manager: GasAlertManager,
    *,
    agent: Optional[GeminiAgent] = None,
    dex_client: Optional[DexscreenerMcpClient] = None,
) -> Application:
    request = HTTPXRequest(
        read_timeout=config.telegram_read_timeout,
        connect_timeout=config.telegram_connect_timeout,
        write_timeout=config.telegram_read_timeout,
        pool_timeout=config.telegram_connect_timeout,
    )

    application = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .request(request)
        .rate_limiter(AIORateLimiter())
        .build()
    )

    application.bot_data.update(
        {
            "config": config,
            "mcp_client": client,
            "alert_manager": alert_manager,
            "agent": agent,
            "dex_client": dex_client,
        }
    )

    application.add_handler(CommandHandler("help", _handle_help))
    application.add_handler(CommandHandler("ask", _handle_ask))
    application.add_handler(CommandHandler("gas", _handle_gas))
    application.add_handler(CommandHandler("account", _handle_account))
    application.add_handler(CommandHandler("tx", _handle_tx))
    application.add_handler(CommandHandler("gas_sub", partial(_handle_subscribe, direction="below")))
    application.add_handler(CommandHandler("gas_sub_above", partial(_handle_subscribe, direction="above")))
    application.add_handler(CommandHandler("gas_clear", _handle_clear))
    application.add_handler(CallbackQueryHandler(_handle_gas_refresh, pattern=f"^{REFRESH_QUERY}$"))

    if application.job_queue is None:
        raise RuntimeError("JobQueue is unavailable. Install python-telegram-bot[job-queue] to enable scheduled tasks.")

    application.job_queue.run_repeating(gas_monitor_job, interval=30, first=10)

    return application
