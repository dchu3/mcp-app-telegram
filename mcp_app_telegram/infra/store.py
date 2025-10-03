from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

from ..config import ScanPairDefinition


@dataclass(slots=True)
class PairMetadata:
    """Metadata describing a tracked liquidity pair."""

    pair_key: str
    symbols: str
    base_symbol: str
    quote_symbol: str
    base_address: Optional[str]
    quote_address: Optional[str]
    dex_id: Optional[str]
    fee_tiers: Tuple[str, ...]


@dataclass(slots=True)
class SwrSnapshot:
    """Cached response body for stale-while-revalidate flows."""

    pair_key: str
    payload: Mapping[str, Any]
    timestamp: float
    ttl: float
    status: str = "fresh"

    def is_fresh(self, now: Optional[float] = None) -> bool:
        horizon = (now or time.time()) - self.timestamp
        return horizon <= self.ttl

    def age(self, now: Optional[float] = None) -> float:
        return max(0.0, (now or time.time()) - self.timestamp)


class InMemoryStore:
    """In-memory store with JSON snapshot persistence."""

    def __init__(self, snapshot_path: Optional[Path] = None) -> None:
        self._lock = asyncio.Lock()
        self._snapshot_path = snapshot_path
        self._scan_set: list[str] = []
        self._pair_meta: Dict[str, PairMetadata] = {}
        self._subs_by_pair: Dict[str, Set[int]] = {}
        self._subs_all: Set[int] = set()
        self._subs_by_user: Dict[int, Set[str]] = {}
        self._profiles: Dict[int, Dict[str, Any]] = {}
        self._swr_by_pair: Dict[str, SwrSnapshot] = {}

    async def initialize_pairs(self, pairs: Sequence[ScanPairDefinition], scan_size: int) -> None:
        async with self._lock:
            if not self._pair_meta:
                self._pair_meta = {}
            for definition in pairs:
                self._pair_meta[definition.pair_key] = PairMetadata(
                    pair_key=definition.pair_key,
                    symbols=definition.symbols,
                    base_symbol=definition.base_symbol,
                    quote_symbol=definition.quote_symbol,
                    base_address=definition.base_address,
                    quote_address=definition.quote_address,
                    dex_id=definition.dex_id,
                    fee_tiers=definition.fee_tiers,
                )
            existing_scan = [pair for pair in self._scan_set if pair in self._pair_meta]
            if not existing_scan:
                self._scan_set = [definition.pair_key for definition in pairs[:scan_size]]
            else:
                self._scan_set = existing_scan
                for definition in pairs:
                    if definition.pair_key not in self._scan_set and len(self._scan_set) < scan_size:
                        self._scan_set.append(definition.pair_key)
            for pair_key in list(self._subs_by_pair):
                if pair_key not in self._pair_meta:
                    self._subs_by_pair.pop(pair_key, None)
            await self._save_snapshot_locked()

    async def load_snapshot(self) -> None:
        if self._snapshot_path is None or not self._snapshot_path.exists():
            return
        try:
            payload = json.loads(self._snapshot_path.read_text())
        except json.JSONDecodeError:
            return
        async with self._lock:
            self._scan_set = list(payload.get("scan_set", []))
            self._pair_meta = {
                item["pair_key"]: PairMetadata(
                    pair_key=item["pair_key"],
                    symbols=item.get("symbols", item["pair_key"]),
                    base_symbol=item.get("base_symbol", ""),
                    quote_symbol=item.get("quote_symbol", ""),
                    base_address=item.get("base_address"),
                    quote_address=item.get("quote_address"),
                    dex_id=item.get("dex_id"),
                    fee_tiers=tuple(item.get("fee_tiers", ())),
                )
                for item in payload.get("pair_meta", [])
                if "pair_key" in item
            }
            self._subs_by_pair = {
                key: set(map(int, value))
                for key, value in payload.get("subs_by_pair", {}).items()
            }
            self._subs_all = set(map(int, payload.get("subs_all", [])))
            self._subs_by_user = {
                int(chat_id): set(value)
                for chat_id, value in payload.get("subs_by_user", {}).items()
            }
            self._profiles = {
                int(chat_id): dict(profile)
                for chat_id, profile in payload.get("profiles", {}).items()
            }
            self._swr_by_pair.clear()

    async def _save_snapshot_locked(self) -> None:
        if self._snapshot_path is None:
            return
        snapshot = {
            "scan_set": list(self._scan_set),
            "pair_meta": [asdict(meta) for meta in self._pair_meta.values()],
            "subs_by_pair": {key: sorted(map(int, value)) for key, value in self._subs_by_pair.items()},
            "subs_all": sorted(map(int, self._subs_all)),
            "subs_by_user": {str(chat_id): sorted(pairs) for chat_id, pairs in self._subs_by_user.items()},
            "profiles": {str(chat_id): profile for chat_id, profile in self._profiles.items()},
        }
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._snapshot_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(snapshot, indent=2))
        tmp_path.replace(self._snapshot_path)

    async def set_scan_set(self, pair_keys: Sequence[str]) -> Tuple[Set[str], Set[str]]:
        async with self._lock:
            for pair_key in pair_keys:
                if pair_key not in self._pair_meta:
                    raise KeyError(f"Unknown pair key '{pair_key}'")
            current = set(self._scan_set)
            incoming = [pair for pair in pair_keys if pair in self._pair_meta]
            added = set(incoming) - current
            removed = current - set(incoming)
            self._scan_set = list(incoming)
            await self._save_snapshot_locked()
            return removed, added

    async def get_scan_set(self) -> Tuple[str, ...]:
        async with self._lock:
            return tuple(self._scan_set)

    async def list_pair_metadata(self) -> Tuple[PairMetadata, ...]:
        async with self._lock:
            return tuple(self._pair_meta.values())

    async def get_pair_metadata(self, pair_key: str) -> Optional[PairMetadata]:
        async with self._lock:
            return self._pair_meta.get(pair_key)

    async def upsert_pair_metadata(self, metadata: PairMetadata) -> None:
        async with self._lock:
            self._pair_meta[metadata.pair_key] = metadata
            await self._save_snapshot_locked()

    async def subscribe_pair(self, chat_id: int, pair_key: str) -> None:
        async with self._lock:
            if pair_key not in self._pair_meta:
                raise KeyError(f"Unknown pair key '{pair_key}'")
            chat = int(chat_id)
            self._subs_by_pair.setdefault(pair_key, set()).add(chat)
            self._subs_by_user.setdefault(chat, set()).add(pair_key)
            await self._save_snapshot_locked()

    async def unsubscribe_pair(self, chat_id: int, pair_key: str) -> None:
        async with self._lock:
            chat = int(chat_id)
            if pair_key in self._subs_by_pair:
                self._subs_by_pair[pair_key].discard(chat)
                if not self._subs_by_pair[pair_key]:
                    self._subs_by_pair.pop(pair_key, None)
            if chat in self._subs_by_user:
                self._subs_by_user[chat].discard(pair_key)
                if not self._subs_by_user[chat]:
                    self._subs_by_user.pop(chat, None)
            await self._save_snapshot_locked()

    async def subscribe_all(self, chat_id: int) -> None:
        async with self._lock:
            self._subs_all.add(int(chat_id))
            await self._save_snapshot_locked()

    async def unsubscribe_all(self, chat_id: int) -> None:
        async with self._lock:
            self._subs_all.discard(int(chat_id))
            await self._save_snapshot_locked()

    async def list_user_subscriptions(self, chat_id: int) -> Tuple[bool, Tuple[str, ...]]:
        async with self._lock:
            chat = int(chat_id)
            all_active = chat in self._subs_all
            explicit = tuple(sorted(self._subs_by_user.get(chat, set())))
            return all_active, explicit

    async def list_pair_subscribers(self, pair_key: str) -> Tuple[int, ...]:
        async with self._lock:
            explicit = set(self._subs_by_pair.get(pair_key, set()))
            return tuple(sorted(explicit | self._subs_all))

    async def record_profile(self, chat_id: int, profile: Mapping[str, Any]) -> None:
        async with self._lock:
            self._profiles[int(chat_id)] = dict(profile)
            await self._save_snapshot_locked()

    async def get_profile(self, chat_id: int) -> Optional[Dict[str, Any]]:
        async with self._lock:
            profile = self._profiles.get(int(chat_id))
            return dict(profile) if profile else None

    async def set_swr_snapshot(self, pair_key: str, payload: Mapping[str, Any], ttl: float, status: str = "fresh") -> None:
        async with self._lock:
            self._swr_by_pair[pair_key] = SwrSnapshot(
                pair_key=pair_key,
                payload=dict(payload),
                timestamp=time.time(),
                ttl=ttl,
                status=status,
            )

    async def get_swr_snapshot(self, pair_key: str) -> Optional[SwrSnapshot]:
        async with self._lock:
            return self._swr_by_pair.get(pair_key)

    async def remove_swr_snapshot(self, pair_key: str) -> None:
        async with self._lock:
            self._swr_by_pair.pop(pair_key, None)

    async def get_effective_subscriptions(self, chat_id: int) -> Tuple[str, ...]:
        async with self._lock:
            chat = int(chat_id)
            explicit = self._subs_by_user.get(chat, set())
            if chat in self._subs_all:
                return tuple(sorted(set(self._scan_set)))
            return tuple(sorted(explicit))

    async def list_subscribers_for_scan_set(self) -> Mapping[str, Tuple[int, ...]]:
        async with self._lock:
            return {
                pair_key: tuple(sorted(self._subs_by_pair.get(pair_key, set()) | self._subs_all))
                for pair_key in self._scan_set
            }
