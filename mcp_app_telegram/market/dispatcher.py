from __future__ import annotations

import asyncio
import time
from typing import Mapping, Optional

from telegram.ext import Application

from ..arb.profiles import ProfileService
from ..arb.signals import ArbSignalService
from ..formatting import format_arb_signal
from ..infra.store import InMemoryStore, PairMetadata
from ..infra.swr import SwrSnapshot
from ..market.fetcher import MarketDataFetcher


class MarketUpdateDispatcher:
    """Fan out market snapshots to subscribed Telegram chats."""

    def __init__(
        self,
        application: Application,
        store: InMemoryStore,
        profile_service: ProfileService,
        signal_service: ArbSignalService,
    ) -> None:
        self._application = application
        self._store = store
        self._profile_service = profile_service
        self._signal_service = signal_service
        self._last_sent: dict[tuple[int, str], float] = {}

    async def handle_snapshot(self, metadata: PairMetadata, snapshot: SwrSnapshot, stale: bool) -> None:
        payload = snapshot.payload
        if not isinstance(payload, Mapping):
            return
        if "gross_bps" not in payload:
            return

        subscribers = await self._store.list_pair_subscribers(metadata.pair_key)
        if not subscribers:
            return

        age_seconds = snapshot.age()
        tasks = []
        for chat_id in subscribers:
            tasks.append(self._dispatch_to_chat(chat_id, metadata, payload, age_seconds, stale))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch_to_chat(
        self,
        chat_id: int,
        metadata: PairMetadata,
        payload: Mapping[str, object],
        age_seconds: float,
        stale: bool,
    ) -> None:
        now = time.time()
        profile = await self._profile_service.get(chat_id)
        cooldown_key = (chat_id, metadata.pair_key)
        last_time = self._last_sent.get(cooldown_key, 0.0)
        if now - last_time < profile.cooldown_seconds:
            return

        calculation_input = MarketDataFetcher.build_calculation_input(
            metadata,
            payload,
            profile_size=profile.test_size_eur,
        )
        signal = self._signal_service.calculate(calculation_input, profile)
        if not signal.meets_threshold:
            return

        message = format_arb_signal(
            metadata=metadata,
            signal=signal,
            payload=payload,
            age_seconds=age_seconds,
            stale=stale,
        )
        await self._application.bot.send_message(chat_id=chat_id, text=message)
        self._last_sent[cooldown_key] = now
