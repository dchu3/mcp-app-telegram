from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Tuple

from .store import InMemoryStore, SwrSnapshot


@dataclass(slots=True)
class SwrFetchResult:
    payload: Mapping[str, Any]
    ttl: Optional[float] = None
    status: str = "fresh"


class SwrCache:
    """Coalesced stale-while-revalidate cache backed by the store."""

    def __init__(
        self,
        store: InMemoryStore,
        *,
        default_ttl: float,
        stale_window: Optional[float] = None,
    ) -> None:
        if default_ttl <= 0:
            raise ValueError("default_ttl must be positive")
        self._store = store
        self._default_ttl = default_ttl
        self._stale_window = stale_window if stale_window is not None else default_ttl * 2
        self._locks: Dict[str, asyncio.Lock] = {}
        self._inflight: Dict[str, asyncio.Future[SwrSnapshot]] = {}

    async def get_snapshot(self, pair_key: str) -> Optional[SwrSnapshot]:
        return await self._store.get_swr_snapshot(pair_key)

    async def get_or_fetch(
        self,
        pair_key: str,
        fetcher: Callable[[], Awaitable[SwrFetchResult]],
        *,
        allow_stale: bool = True,
    ) -> Tuple[Optional[SwrSnapshot], bool]:
        """Return a fresh snapshot or trigger a revalidation if needed.

        Returns a tuple of (snapshot, is_stale_fallback).
        """

        snapshot = await self._store.get_swr_snapshot(pair_key)
        now = time.time()
        if snapshot and snapshot.is_fresh(now):
            return snapshot, False

        stale_candidate = snapshot if snapshot and allow_stale and snapshot.age(now) <= self._stale_window else None

        lock = self._locks.setdefault(pair_key, asyncio.Lock())
        async with lock:
            cached = await self._store.get_swr_snapshot(pair_key)
            now = time.time()
            if cached and cached.is_fresh(now):
                return cached, False

            inflight = self._inflight.get(pair_key)
            if inflight is not None:
                try:
                    result = await asyncio.shield(inflight)
                    return result, False
                except Exception:
                    if stale_candidate is not None:
                        return stale_candidate, True
                    raise

            future: asyncio.Future[SwrSnapshot] = asyncio.get_running_loop().create_future()
            self._inflight[pair_key] = future

            try:
                fetch_result = await fetcher()
                ttl = fetch_result.ttl or self._default_ttl
                await self._store.set_swr_snapshot(pair_key, fetch_result.payload, ttl, status=fetch_result.status)
                fresh_snapshot = await self._store.get_swr_snapshot(pair_key)
                if fresh_snapshot is None:
                    raise RuntimeError("Failed to persist SWR snapshot")
                future.set_result(fresh_snapshot)
                return fresh_snapshot, fetch_result.status == "stale"
            except Exception as exc:
                future.set_exception(exc)
                if stale_candidate is not None:
                    return stale_candidate, True
                raise
            finally:
                self._inflight.pop(pair_key, None)
                if not future.done():
                    future.cancel()

    async def save_snapshot(self, pair_key: str, result: SwrFetchResult) -> SwrSnapshot:
        ttl = result.ttl or self._default_ttl
        await self._store.set_swr_snapshot(pair_key, result.payload, ttl, status=result.status)
        snapshot = await self._store.get_swr_snapshot(pair_key)
        if snapshot is None:
            raise RuntimeError("Failed to fetch snapshot after save")
        return snapshot
