from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Dict, Mapping, Optional, Tuple

from .ratelimit import RequestRateLimiter
from .swr import SwrCache, SwrFetchResult
from .store import InMemoryStore, PairMetadata, SwrSnapshot


_LOGGER = logging.getLogger(__name__)


class PollingTier(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


@dataclass(slots=True)
class PairState:
    pair_key: str
    tier: PollingTier
    next_run: float
    watchers: int = 0
    volatility_score: float = 0.0
    last_snapshot_status: str = "unknown"
    last_error: Optional[str] = None


class CentralScheduler:
    """Scheduler that polls tracked pairs with tiered cadences."""

    def __init__(
        self,
        *,
        store: InMemoryStore,
        swr_cache: SwrCache,
        rate_limiter: RequestRateLimiter,
        cadences: Mapping[PollingTier, float],
        fetcher: Callable[[PairMetadata], Awaitable[SwrFetchResult]],
        on_snapshot: Optional[Callable[[PairMetadata, SwrSnapshot, bool], Awaitable[None]]] = None,
        host_provider: Optional[Callable[[PairMetadata], Optional[str]]] = None,
    ) -> None:
        self._store = store
        self._swr_cache = swr_cache
        self._rate_limiter = rate_limiter
        self._cadences = cadences
        self._fetcher = fetcher
        self._on_snapshot = on_snapshot
        self._host_provider = host_provider or (lambda _: None)
        self._states: Dict[str, PairState] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._task is not None:
                return
            await self._refresh_pairs_locked()
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run_loop(), name="central-scheduler")

    async def stop(self) -> None:
        async with self._lock:
            if self._task is None:
                return
            self._stop_event.set()
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            self._ready_event.clear()

    def set_on_snapshot(
        self,
        callback: Optional[Callable[[PairMetadata, SwrSnapshot, bool], Awaitable[None]]],
    ) -> None:
        self._on_snapshot = callback

    async def await_ready(self) -> None:
        await self._ready_event.wait()

    async def trigger_refresh(self) -> None:
        await self._refresh_pairs()

    async def _run_loop(self) -> None:
        try:
            self._ready_event.set()
            while not self._stop_event.is_set():
                pair_key, wait_for = await self._next_pair_due()
                if pair_key is None:
                    await asyncio.sleep(wait_for)
                    continue
                if wait_for > 0:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=wait_for)
                        break
                    except asyncio.TimeoutError:
                        pass
                await self._poll_pair(pair_key)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive guard
            _LOGGER.exception("Scheduler loop crashed")
        finally:
            self._ready_event.clear()

    async def _next_pair_due(self) -> Tuple[Optional[str], float]:
        async with self._lock:
            now = time.monotonic()
            if not self._states:
                return None, 1.0
            pair_key, state = min(self._states.items(), key=lambda item: item[1].next_run)
            wait_for = max(0.0, state.next_run - now)
            return pair_key, wait_for

    async def _poll_pair(self, pair_key: str) -> None:
        metadata = await self._store.get_pair_metadata(pair_key)
        if metadata is None:
            return
        state = self._states.get(pair_key)
        if state is None:
            return
        host = self._host_provider(metadata)
        try:
            await self._rate_limiter.acquire(host, tokens=1)
            snapshot, stale = await self._swr_cache.get_or_fetch(pair_key, lambda: self._fetcher(metadata))
            if snapshot is not None and self._on_snapshot is not None:
                await self._on_snapshot(metadata, snapshot, stale)
            await self._update_watchers(pair_key)
            await self._reschedule(pair_key, success=True, status=snapshot.status if snapshot else "missing")
        except Exception as exc:
            _LOGGER.warning("Polling failed for %s: %s", pair_key, exc, exc_info=True)
            await self._reschedule(pair_key, success=False, status="error", error=str(exc))

    async def _reschedule(self, pair_key: str, *, success: bool, status: str, error: Optional[str] = None) -> None:
        async with self._lock:
            state = self._states.get(pair_key)
            if state is None:
                return
            cadence = self._cadences.get(state.tier, self._cadences[PollingTier.WARM])
            jitter = cadence * 0.15
            delay = cadence + (random.random() * jitter - jitter / 2)
            state.next_run = time.monotonic() + max(0.5, delay)
            state.last_snapshot_status = status
            state.last_error = error if not success else None

    async def _update_watchers(self, pair_key: str) -> None:
        subscribers = await self._store.list_pair_subscribers(pair_key)
        watcher_count = len(subscribers)
        async with self._lock:
            state = self._states.get(pair_key)
            if state is None:
                return
            state.watchers = watcher_count
            await self._maybe_adjust_tier(state)

    async def _maybe_adjust_tier(self, state: PairState) -> None:
        old_tier = state.tier
        if state.watchers >= 5:
            state.tier = PollingTier.HOT
        elif state.watchers >= 1:
            state.tier = PollingTier.WARM
        else:
            state.tier = PollingTier.COLD
        if old_tier != state.tier:
            cadence = self._cadences.get(state.tier, self._cadences[PollingTier.WARM])
            state.next_run = time.monotonic() + cadence

    async def _refresh_pairs(self) -> None:
        async with self._lock:
            await self._refresh_pairs_locked()

    async def _refresh_pairs_locked(self) -> None:
        scan_set = await self._store.get_scan_set()
        metadata_items = {meta.pair_key: meta for meta in await self._store.list_pair_metadata()}
        known_keys = set(self._states)
        for pair_key in scan_set:
            if pair_key not in metadata_items:
                continue
            if pair_key not in self._states:
                cadence = self._cadences.get(PollingTier.WARM, 30.0)
                self._states[pair_key] = PairState(
                    pair_key=pair_key,
                    tier=PollingTier.WARM,
                    next_run=time.monotonic() + (len(self._states) * 1.0),
                )
        for obsolete in known_keys - set(scan_set):
            self._states.pop(obsolete, None)


__all__ = [
    "CentralScheduler",
    "PairState",
    "PollingTier",
]
