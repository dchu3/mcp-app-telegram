from __future__ import annotations

import asyncio
from typing import Iterable, Optional, Sequence

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Config
from ..infra.scheduler import CentralScheduler
from ..infra.store import InMemoryStore
from ..infra.swr import SwrCache


def _get_store(context: ContextTypes.DEFAULT_TYPE) -> InMemoryStore:
    store = context.application.bot_data.get("store")
    if not isinstance(store, InMemoryStore):
        raise RuntimeError("Store not configured in bot context")
    return store


def _get_scheduler(context: ContextTypes.DEFAULT_TYPE) -> Optional[CentralScheduler]:
    scheduler = context.application.bot_data.get("scheduler")
    if isinstance(scheduler, CentralScheduler):
        return scheduler
    return None


def _get_config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    config = context.application.bot_data.get("config")
    if not isinstance(config, Config):
        raise RuntimeError("Config not configured in bot context")
    return config


def _resolve_pair_key(target: str, scan_set: Sequence[str]) -> Optional[str]:
    if not target:
        return None
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(scan_set):
            return scan_set[idx]
        return None
    for pair_key in scan_set:
        if pair_key.lower() == target.lower():
            return pair_key
    return None


def _format_pair_entry(index: int, pair_key: str, symbols: str, status: str) -> str:
    return f"{index}. {symbols} — {pair_key} ({status})"


async def list_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _get_store(context)
    scan_set = await store.get_scan_set()
    metadata = {item.pair_key: item for item in await store.list_pair_metadata()}
    if not scan_set:
        await update.effective_message.reply_text("No tracked pairs configured yet.")
        return

    swr_cache = context.application.bot_data.get("swr_cache")
    lines: list[str] = ["Tracked pairs:"]
    for idx, pair_key in enumerate(scan_set, start=1):
        meta = metadata.get(pair_key)
        symbols = meta.symbols if meta else pair_key
        age_label = "n/a"
        if isinstance(swr_cache, SwrCache):
            snapshot = await swr_cache.get_snapshot(pair_key)
            if snapshot:
                age_label = f"age {int(snapshot.age())}s"
        lines.append(_format_pair_entry(idx, pair_key, symbols, age_label))

    await update.effective_message.reply_text("\n".join(lines))


async def subscribe_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _get_store(context)
    config = _get_config(context)
    scan_set = await store.get_scan_set()
    if not context.args:
        await update.effective_message.reply_text("Usage: /sub <index|pair_key>")
        return

    target = context.args[0]
    pair_key = _resolve_pair_key(target, scan_set)
    if pair_key is None:
        await update.effective_message.reply_text("Pair not found in the current scan set.")
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return

    _, explicit = await store.list_user_subscriptions(chat_id)
    if config.max_user_subs and len(explicit) >= config.max_user_subs:
        await update.effective_message.reply_text(
            f"You have reached the maximum of {config.max_user_subs} explicit subscriptions."
        )
        return

    try:
        await store.subscribe_pair(chat_id, pair_key)
    except KeyError:
        await update.effective_message.reply_text("Pair metadata missing; cannot subscribe right now.")
        return
    scheduler = _get_scheduler(context)
    if scheduler:
        await scheduler.trigger_refresh()
    await update.effective_message.reply_text(f"Subscribed to pair {pair_key}.")


async def unsubscribe_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _get_store(context)
    if not context.args:
        await update.effective_message.reply_text("Usage: /unsub <index|pair_key>")
        return
    scan_set = await store.get_scan_set()
    target = context.args[0]
    pair_key = _resolve_pair_key(target, scan_set)
    if pair_key is None:
        pair_key = target
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    await store.unsubscribe_pair(chat_id, pair_key)
    scheduler = _get_scheduler(context)
    if scheduler:
        await scheduler.trigger_refresh()
    await update.effective_message.reply_text(f"Unsubscribed from pair {pair_key}.")


async def subscribe_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = _get_config(context)
    if not config.allow_sub_all:
        await update.effective_message.reply_text("Global subscriptions are disabled by configuration.")
        return
    store = _get_store(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    await store.subscribe_all(chat_id)
    scheduler = _get_scheduler(context)
    if scheduler:
        await scheduler.trigger_refresh()
    await update.effective_message.reply_text("Subscribed to all tracked pairs.")


async def unsubscribe_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _get_store(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    await store.unsubscribe_all(chat_id)
    scheduler = _get_scheduler(context)
    if scheduler:
        await scheduler.trigger_refresh()
    await update.effective_message.reply_text("Cleared global subscription.")


async def list_my_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _get_store(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    all_active, explicit = await store.list_user_subscriptions(chat_id)
    lines = ["Your subscriptions:"]
    if all_active:
        lines.append("- Global: all tracked pairs")
    if explicit:
        for pair in explicit:
            lines.append(f"- {pair}")
    if len(lines) == 1:
        lines.append("(none)")
    await update.effective_message.reply_text("\n".join(lines))


COMMAND_HANDLERS = {
    "pairs": list_pairs,
    "sub": subscribe_pair,
    "unsub": unsubscribe_pair,
    "suball": subscribe_all,
    "unsuball": unsubscribe_all,
    "mysubs": list_my_subs,
}
