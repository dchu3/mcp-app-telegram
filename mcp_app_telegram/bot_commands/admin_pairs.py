from __future__ import annotations

from typing import Iterable, Optional

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Config
from ..infra.scheduler import CentralScheduler
from ..infra.store import InMemoryStore, PairMetadata
from ..mcp.manager import McpClientRegistry
from ..mcp_client import EvmMcpClient


def _get_config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    config = context.application.bot_data.get("config")
    if not isinstance(config, Config):
        raise RuntimeError("Configuration missing from bot context")
    return config


def _get_store(context: ContextTypes.DEFAULT_TYPE) -> InMemoryStore:
    store = context.application.bot_data.get("store")
    if not isinstance(store, InMemoryStore):
        raise RuntimeError("Store missing from bot context")
    return store


def _get_scheduler(context: ContextTypes.DEFAULT_TYPE) -> Optional[CentralScheduler]:
    scheduler = context.application.bot_data.get("scheduler")
    if isinstance(scheduler, CentralScheduler):
        return scheduler
    return None


def _get_registry(context: ContextTypes.DEFAULT_TYPE) -> Optional[McpClientRegistry]:
    registry = context.application.bot_data.get("mcp_registry")
    if isinstance(registry, McpClientRegistry):
        return registry
    return None


def _require_admin(update: Update, config: Config) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return False
    if chat_id != config.telegram_chat_id:
        return False
    return True


async def rotate_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = _get_config(context)
    if not _require_admin(update, config):
        await update.effective_message.reply_text("Admin command only.")
        return

    store = _get_store(context)
    scan_set = await store.get_scan_set()
    metadata = {meta.pair_key: meta for meta in await store.list_pair_metadata()}

    if not context.args:
        lines = ["Provide a list of indices or pair keys to form the new scan set."]
        lines.append("Current order:")
        for idx, key in enumerate(scan_set, start=1):
            symbols = metadata.get(key).symbols if key in metadata else key
            lines.append(f"{idx}. {symbols} ({key})")
        await update.effective_message.reply_text("\n".join(lines))
        return

    desired: list[str] = []
    for arg in context.args:
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(scan_set):
                desired.append(scan_set[idx])
        elif arg in metadata:
            desired.append(arg)
        else:
            await update.effective_message.reply_text(f"Unknown pair '{arg}' ignored.")
    if not desired:
        await update.effective_message.reply_text("No valid pairs supplied.")
        return

    desired = desired[: config.scan_size]
    removed, added = await store.set_scan_set(desired)
    scheduler = _get_scheduler(context)
    if scheduler:
        await scheduler.trigger_refresh()
    response = ["Scan set updated."]
    if added:
        response.append("Added: " + ", ".join(sorted(added)))
    if removed:
        response.append("Removed: " + ", ".join(sorted(removed)))
    await update.effective_message.reply_text("\n".join(response))


async def show_limits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = _get_config(context)
    if not _require_admin(update, config):
        await update.effective_message.reply_text("Admin command only.")
        return
    lines = [
        "Limits and cadences:",
        f"- MAX_USER_SUBS: {config.max_user_subs}",
        f"- SCAN_SIZE: {config.scan_size}",
        f"- SCAN cadences (s): hot={config.scan_cadence_hot}, warm={config.scan_cadence_warm}, cold={config.scan_cadence_cold}",
        f"- SWR_TTL: {config.swr_ttl}",
        f"- GLOBAL_REQS_PER_MIN: {config.global_reqs_per_min}",
        f"- MEV_BUFFER_BPS: {config.mev_buffer_bps}",
        f"- SEQUENCER_LAG_MS_SUSPEND: {config.sequencer_lag_ms_suspend}",
    ]
    await update.effective_message.reply_text("\n".join(lines))


async def list_mcp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = _get_config(context)
    if not _require_admin(update, config):
        await update.effective_message.reply_text("Admin command only.")
        return
    registry = _get_registry(context)
    if registry is None:
        await update.effective_message.reply_text("MCP registry unavailable.")
        return
    lines = ["Available MCP clients:"]
    for key in registry.keys():
        lines.append(f"- {key}")
    await update.effective_message.reply_text("\n".join(lines))


async def rpc_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = _get_config(context)
    if not _require_admin(update, config):
        await update.effective_message.reply_text("Admin command only.")
        return
    registry = _get_registry(context)
    if registry is None:
        await update.effective_message.reply_text("MCP registry unavailable.")
        return
    key = context.application.bot_data.get("primary_evm_key")
    if not isinstance(key, str):
        await update.effective_message.reply_text("Primary EVM client not configured.")
        return
    try:
        client = registry.require_typed(key, EvmMcpClient)
    except (KeyError, TypeError) as exc:
        await update.effective_message.reply_text(f"EVM client error: {exc}")
        return

    try:
        stats = await client.fetch_gas_stats()
    except Exception as exc:  # pragma: no cover - network guard
        await update.effective_message.reply_text(f"RPC ping failed: {exc}")
        return

    lag = getattr(stats, "sequencer_lag_ms", None)
    fast = getattr(stats, "fast", None)
    message = "RPC OK"
    if fast is not None:
        message += f" — fast {fast} gwei"
    if lag is not None:
        message += f" — sequencer lag {lag} ms"
    await update.effective_message.reply_text(message)


COMMAND_HANDLERS = {
    "rotatepairs": rotate_pairs,
    "limits": show_limits,
    "mcptest": list_mcp,
    "rpcping": rpc_ping,
}
