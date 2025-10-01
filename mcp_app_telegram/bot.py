"""Telegram bot wiring for the MCP EVM integration."""

from __future__ import annotations

import logging
from functools import partial
from typing import Mapping, Optional

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from .alerts import GasAlertManager, GasAlertSubscription
from .config import Config
from .formatting import format_account, format_gas_stats, format_transaction
from .gemini_agent import GeminiAgent, GeminiAgentError
from .database import get_distinct_networks_with_alerts
from .mcp_client import DexscreenerMcpClient, EvmMcpClient, McpClientError
from .mcp.manager import McpClientRegistry

_LOGGER = logging.getLogger(__name__)

REFRESH_QUERY = "gas_refresh"

TELEGRAM_COMMANDS = [
    BotCommand("help", "Show available commands"),
    BotCommand("gas", "Show Base gas stats"),
    BotCommand("account", "Show account balance and nonce"),
    BotCommand("transaction", "Summarize transaction status"),
    BotCommand("gasalert", "Alert when fast gas drops below threshold"),
    BotCommand("gasalertabove", "Alert when fast gas rises above threshold"),
    BotCommand("cleargasalerts", "Clear gas alerts for this chat"),
    BotCommand("gasalerts", "List active gas alerts for this chat"),
]


_HELP_TEXT = (
    "Here are the commands I understand:\n"
    "- Send a normal message: Gemini agent picks an MCP tool to answer.\n"
    "- /gas : Base gas tiers, base fee, and sequencer lag.\n"
    "- /account <address> : Balance, nonce, and contract status for an address.\n"
    "- /transaction <hash> : Transaction status, gas used, and value.\n"
    "- /gasalert <network> <gwei> : Alert when fast gas drops below a threshold.\n"
    "- /gasalertabove <network> <gwei> : Alert when fast gas rises above a threshold.\n"
    "- /cleargasalerts : Clear pending gas alerts in this chat.\n"
    "- /gasalerts : List active gas alerts in this chat."
)

_TELEGRAM_MESSAGE_LIMIT = 4000


def _resolve_registry(bot_data: Mapping[str, object]) -> McpClientRegistry:
    registry = bot_data.get("mcp_registry")
    if not isinstance(registry, McpClientRegistry):
        raise RuntimeError("MCP registry is not configured")
    return registry


def _primary_evm_client(bot_data: Mapping[str, object]) -> EvmMcpClient:
    registry = _resolve_registry(bot_data)
    key = bot_data.get("primary_evm_key")
    if not isinstance(key, str):
        raise RuntimeError("Primary EVM MCP key is not configured")
    return registry.require_typed(key, EvmMcpClient)


def _evm_client_for_network(bot_data: Mapping[str, object], network: str) -> EvmMcpClient:
    registry = _resolve_registry(bot_data)
    key = bot_data.get("primary_evm_key")
    if not isinstance(key, str):
        raise RuntimeError("Primary EVM MCP key is not configured")
    mapping = bot_data.get("network_client_map")
    if isinstance(mapping, Mapping):
        mapped = mapping.get(network.lower())
        if isinstance(mapped, str):
            key = mapped
    return registry.require_typed(key, EvmMcpClient)


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
    client = _primary_evm_client(context.application.bot_data)
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
    client = _primary_evm_client(context.application.bot_data)
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
    client = _primary_evm_client(context.application.bot_data)
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


async def _handle_text_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = (message.text or "").strip()
    if not text:
        return

    agent: Optional[GeminiAgent] = context.application.bot_data.get("agent")
    if agent is None:
        await message.reply_text(
            "The Gemini agent is not configured. Set GEMINI_API_KEY to enable natural language questions."
        )
        return

    try:
        answer = await agent.answer(text)
    except GeminiAgentError as exc:
        await message.reply_text(f"Gemini agent error: {exc}")
    except Exception:  # pragma: no cover - defensive guard
        _LOGGER.exception("Unexpected failure while handling text query")
        await message.reply_text("An unexpected error occurred while answering.")
    else:
        await _reply_text_chunks(message, answer)



async def _handle_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /account <address>")
        return

    address = context.args[0]
    if not address.startswith("0x") or len(address) != 42 or any(ch not in "0123456789abcdefABCDEF" for ch in address[2:]):
        await update.effective_message.reply_text("Address must be a 42-character hex string starting with 0x.")
        return

    client = _primary_evm_client(context.application.bot_data)
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
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /gasalert <network> <threshold_gwei>")
        return
    network_input = context.args[0]
    network = network_input.lower()
    network_map = context.application.bot_data.get("network_client_map")
    if isinstance(network_map, Mapping) and network_map:
        if network not in network_map:
            known = ", ".join(sorted(network_map.keys()))
            await update.effective_message.reply_text(
                f"Unknown network '{network_input}'. Known networks: {known}"
            )
            return
    try:
        threshold = float(context.args[1])
    except ValueError:
        await update.effective_message.reply_text("Threshold must be a number.")
        return

    alert_manager: GasAlertManager = context.application.bot_data["alert_manager"]
    subscription = GasAlertSubscription(
        chat_id=update.effective_chat.id,
        network=network,
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


async def _handle_list_gas_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    alert_manager: GasAlertManager = context.application.bot_data["alert_manager"]
    subscriptions = await alert_manager.list_subscriptions(update.effective_chat.id)
    if not subscriptions:
        await update.effective_message.reply_text("No active gas alerts for this chat.")
        return

    message = "Active gas alerts:\n"
    for sub in subscriptions:
        message += f"- {sub.describe()}\n"
    await update.effective_message.reply_text(message)


async def gas_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    alert_manager: GasAlertManager = context.bot_data["alert_manager"]

    networks = get_distinct_networks_with_alerts()

    for network in networks:
        client = _evm_client_for_network(context.bot_data, network)
        try:
            stats = await client.fetch_gas_stats(network)
        except Exception as exc:  # pragma: no cover
            _LOGGER.warning("Skipping alert evaluation for %s due to MCP error: %s", network, exc)
            continue

        matches = await alert_manager.evaluate(network, stats)

        if not matches:
            continue

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
    registry: McpClientRegistry,
    alert_manager: GasAlertManager,
    *,
    agent: Optional[GeminiAgent] = None,
    dex_client: Optional[DexscreenerMcpClient] = None,
    primary_evm_key: str,
    primary_dex_key: Optional[str],
    network_client_map: Mapping[str, str],
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
            "mcp_registry": registry,
            "alert_manager": alert_manager,
            "agent": agent,
            "dex_client": dex_client,
            "primary_evm_key": primary_evm_key,
            "primary_dex_key": primary_dex_key,
            "network_client_map": dict(network_client_map),
        }
    )

    application.add_handler(CommandHandler("help", _handle_help))
    application.add_handler(CommandHandler("gas", _handle_gas))
    application.add_handler(CommandHandler("account", _handle_account))
    application.add_handler(CommandHandler("transaction", _handle_tx))
    application.add_handler(CommandHandler("gasalert", partial(_handle_subscribe, direction="below")))
    application.add_handler(CommandHandler("gasalertabove", partial(_handle_subscribe, direction="above")))
    application.add_handler(CommandHandler("cleargasalerts", _handle_clear))
    application.add_handler(CommandHandler("gasalerts", _handle_list_gas_alerts))
    application.add_handler(CallbackQueryHandler(_handle_gas_refresh, pattern=f"^{REFRESH_QUERY}$"))

    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), _handle_text_query)
    )

    if application.job_queue is None:
        raise RuntimeError("JobQueue is unavailable. Install python-telegram-bot[job-queue] to enable scheduled tasks.")

    application.job_queue.run_repeating(gas_monitor_job, interval=30, first=10)

    return application
