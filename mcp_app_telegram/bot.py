"""Telegram bot wiring for the MCP EVM integration."""

from __future__ import annotations

import logging
from functools import partial
import json
from typing import Any, Mapping, Optional

from telegram import Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

from .alerts import GasAlertManager, GasAlertSubscription
from .arb.profiles import ProfileService
from .arb.signals import ArbSignalService
from .bot_commands.admin_pairs import COMMAND_HANDLERS as ADMIN_COMMANDS
from .bot_commands.subscriptions import COMMAND_HANDLERS as SUBSCRIPTION_COMMANDS
from .config import Config
from .database import get_distinct_networks_with_alerts
from .formatting import format_account, format_gas_stats, format_transaction
from .gemini_agent import GeminiAgent, GeminiAgentError
from .infra.ratelimit import RequestRateLimiter
from .infra.scheduler import CentralScheduler
from .infra.store import InMemoryStore
from .infra.swr import SwrCache
from .market.dispatcher import MarketUpdateDispatcher
from .market.fetcher import MarketDataFetcher
from .mcp.manager import McpClientRegistry
from .mcp_client import DexscreenerMcpClient, EvmMcpClient, McpClientError

_LOGGER = logging.getLogger(__name__)

REFRESH_QUERY = "gas_refresh"

TELEGRAM_COMMANDS = [
    BotCommand("help", "Show available commands"),
    BotCommand("gas", "Show Base gas stats"),
    # BotCommand("account", "Show account balance and nonce"),
    # BotCommand("transaction", "Summarize transaction status"),
    # BotCommand("tx", "Alias for /transaction"),
    # BotCommand("gasalert", "Alert when fast gas drops below threshold"),
    # BotCommand("gasalertabove", "Alert when fast gas rises above threshold"),
    # BotCommand("cleargasalerts", "Clear gas alerts for this chat"),
    # BotCommand("gasalerts", "List active gas alerts for this chat"),
    # BotCommand("gas_sub", "Alias for /gasalert"),
    # BotCommand("gas_sub_above", "Alias for /gasalertabove"),
    # BotCommand("gas_clear", "Alias for /cleargasalerts"),
    BotCommand("pairs", "List tracked arbitrage pairs"),
    BotCommand("sub", "Subscribe to a tracked pair"),
    BotCommand("unsub", "Remove a pair subscription"),
    BotCommand("mysubs", "List your pair subscriptions"),
    BotCommand("suball", "Subscribe to all tracked pairs"),
    BotCommand("unsuball", "Clear global pair subscription"),
]

ADMIN_TELEGRAM_COMMANDS = [
    BotCommand("rotatepairs", "Admin: Reorder tracked pairs"),
    BotCommand("limits", "Admin: Show scan limits"),
    BotCommand("mcptest", "Admin: List MCP clients"),
    BotCommand("rpcping", "Admin: Check primary EVM RPC"),
]


_HELP_TEXT = (
    "Here are the commands I understand:\n"
    "- Send a normal message: Gemini agent picks an MCP tool to answer.\n"
    "- /gas : Base gas tiers, base fee, and sequencer lag.\n"
    "- /pairs : List tracked arbitrage pairs with realtime age.\n"
    "- /sub <index|pair> : Subscribe to a tracked pair.\n"
    "- /unsub <index|pair> : Remove a tracked pair subscription.\n"
    "- /suball : Subscribe to all tracked pairs when allowed.\n"
    "- /unsuball : Clear the global pair subscription.\n"
    "- /mysubs : Show your current pair subscriptions."
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


async def _log_incoming_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Emit a debug-level log for every incoming update without altering flow."""

    if not _LOGGER.isEnabledFor(logging.INFO):
        return

    description: dict[str, object] = {}
    if update.effective_chat is not None:
        description["chat_id"] = update.effective_chat.id
        description["chat_type"] = update.effective_chat.type
    if update.effective_user is not None:
        description["user_id"] = update.effective_user.id
        description["username"] = update.effective_user.username
    message = update.effective_message
    if message is not None:
        description["text"] = message.text
        description.setdefault("entities", message.entities)
    callback = update.callback_query
    if callback is not None:
        description["callback_data"] = callback.data
    try:
        payload = json.dumps(description, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive fallback
        payload = str(description)
    _LOGGER.info("Incoming update: %s", payload)


async def _handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _LOGGER.info("Handling /help for chat %s", update.effective_chat.id if update.effective_chat else "?")
    await update.effective_message.reply_text(_HELP_TEXT)


async def _handle_gas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _LOGGER.info("Handling /gas for chat %s", update.effective_chat.id if update.effective_chat else "?")
    client = _primary_evm_client(context.application.bot_data)
    network_label = context.application.bot_data.get("primary_evm_network")
    try:
        stats = await client.fetch_gas_stats()
    except Exception as exc:  # pragma: no cover - network failure guard
        _LOGGER.exception("Failed to load gas stats")
        await update.effective_message.reply_text(f"Error fetching gas stats: {exc}")
        return

    text = format_gas_stats(stats, network=network_label)
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
    network_label = context.application.bot_data.get("primary_evm_network")
    try:
        stats = await client.fetch_gas_stats()
    except Exception as exc:  # pragma: no cover
        await query.edit_message_text(f"Error fetching gas stats: {exc}")
        return

    await query.edit_message_text(
        format_gas_stats(stats, network=network_label),
        reply_markup=query.message.reply_markup,
    )


async def _handle_tx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _LOGGER.info("Handling /transaction for chat %s", update.effective_chat.id if update.effective_chat else "?")
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /transaction <transaction-hash> (alias: /tx)"
        )
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
    _LOGGER.info("Handling text query in chat %s", update.effective_chat.id if update.effective_chat else "?")

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
    _LOGGER.info("Handling /account for chat %s", update.effective_chat.id if update.effective_chat else "?")
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
    _LOGGER.info(
        "Handling gas alert subscribe (%s) for chat %s",
        direction,
        update.effective_chat.id if update.effective_chat else "?",
    )
    if len(context.args) != 2:
        alias = "/gas_sub" if direction == "below" else "/gas_sub_above"
        command = "/gasalert" if direction == "below" else "/gasalertabove"
        await update.effective_message.reply_text(
            f"Usage: {command} <network> <threshold_gwei> (alias: {alias})"
        )
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
    _LOGGER.info("Handling gas alert clear for chat %s", update.effective_chat.id if update.effective_chat else "?")
    alert_manager: GasAlertManager = context.application.bot_data["alert_manager"]
    await alert_manager.clear_for_chat(update.effective_chat.id)
    await update.effective_message.reply_text("Cleared gas alert subscriptions for this chat.")


async def _handle_list_gas_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _LOGGER.info("Handling gas alerts list for chat %s", update.effective_chat.id if update.effective_chat else "?")
    alert_manager: GasAlertManager = context.application.bot_data["alert_manager"]
    subscriptions = await alert_manager.list_subscriptions(update.effective_chat.id)
    if not subscriptions:
        await update.effective_message.reply_text("No active gas alerts for this chat.")
        return

    message = "Active gas alerts:\n"
    for sub in subscriptions:
        message += f"- {sub.describe()}\n"
    await update.effective_message.reply_text(message)


async def _handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    raw_text = (message.text or "").strip()
    if not raw_text.startswith("/"):
        return
    command = raw_text.split()[0]
    normalized = command.split("@", 1)[0][1:].lower()
    known: set[str] = context.application.bot_data.get("known_commands", set())
    if normalized in known:
        _LOGGER.debug("Command %s already handled upstream; skipping unknown handler", command)
        return
    if normalized == "start":
        _LOGGER.debug("Suppressing /start fallback reply")
        return
    _LOGGER.info("Received unknown command: %s", command)
    await message.reply_text(
        f"Unknown command '{command}'. Send /help for the list of supported commands."
    )


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

        message = format_gas_stats(stats, network=network)
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
    coingecko_clients: Mapping[str, Any] | None = None,
    primary_evm_key: str,
    primary_dex_key: Optional[str],
    network_client_map: Mapping[str, str],
    store: InMemoryStore,
    rate_limiter: RequestRateLimiter,
    swr_cache: SwrCache,
    scheduler: CentralScheduler,
    profile_service: ProfileService,
    signal_service: ArbSignalService,
    market_fetcher: MarketDataFetcher,
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
            "coingecko_clients": dict(coingecko_clients or {}),
            "primary_evm_key": primary_evm_key,
            "primary_dex_key": primary_dex_key,
            "network_client_map": dict(network_client_map),
            "store": store,
            "rate_limiter": rate_limiter,
            "swr_cache": swr_cache,
            "scheduler": scheduler,
            "profile_service": profile_service,
            "signal_service": signal_service,
            "market_fetcher": market_fetcher,
        }
    )

    primary_network = next(
        (name for name, key in network_client_map.items() if key == primary_evm_key),
        None,
    )
    if primary_network:
        application.bot_data["primary_evm_network"] = primary_network

    dispatcher = MarketUpdateDispatcher(application, store, profile_service, signal_service)
    scheduler.set_on_snapshot(dispatcher.handle_snapshot)
    application.bot_data["market_dispatcher"] = dispatcher

    application.add_handler(CommandHandler("help", _handle_help))
    application.add_handler(CommandHandler("gas", _handle_gas))
    application.add_handler(CommandHandler("account", _handle_account))
    application.add_handler(CommandHandler(["transaction", "tx"], _handle_tx))
    application.add_handler(
        CommandHandler(["gasalert", "gas_sub"], partial(_handle_subscribe, direction="below"))
    )
    application.add_handler(
        CommandHandler(["gasalertabove", "gas_sub_above"], partial(_handle_subscribe, direction="above"))
    )
    application.add_handler(CommandHandler(["cleargasalerts", "gas_clear"], _handle_clear))
    application.add_handler(CommandHandler(["gasalerts", "gas_alerts"], _handle_list_gas_alerts))
    for name, handler in SUBSCRIPTION_COMMANDS.items():
        application.add_handler(CommandHandler(name, handler))
    for name, handler in ADMIN_COMMANDS.items():
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(
        TypeHandler(Update, _log_incoming_update),
        group=-1,
    )
    application.add_handler(CallbackQueryHandler(_handle_gas_refresh, pattern=f"^{REFRESH_QUERY}$"))

    application.add_handler(MessageHandler(filters.COMMAND, _handle_unknown_command), group=1)
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), _handle_text_query)
    )

    registered: list[str] = []
    known_commands: set[str] = set()
    for group_index, handler_list in application.handlers.items():
        for handler in handler_list:
            if isinstance(handler, CommandHandler):
                command_names = {str(cmd).lower() for cmd in handler.commands}
                known_commands.update(command_names)
                registered.append(f"group {group_index}: {sorted(handler.commands)}")
    _LOGGER.info("Registered command handlers: %s", registered)

    application.bot_data["known_commands"] = known_commands

    if application.job_queue is None:
        raise RuntimeError("JobQueue is unavailable. Install python-telegram-bot[job-queue] to enable scheduled tasks.")

    application.job_queue.run_repeating(gas_monitor_job, interval=30, first=10)

    return application
