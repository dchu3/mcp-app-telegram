from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Optional, Tuple

from ..infra.store import InMemoryStore


@dataclass(slots=True)
class ArbProfile:
    min_net_bps: float = 20.0
    min_net_eur: float = 0.5
    test_size_eur: float = 500.0
    venues: Tuple[str, ...] = ()
    slippage_cap_bps: float = 150.0
    cooldown_seconds: int = 180

    def to_dict(self) -> Mapping[str, object]:
        return {
            "min_net_bps": self.min_net_bps,
            "min_net_eur": self.min_net_eur,
            "test_size_eur": self.test_size_eur,
            "venues": list(self.venues),
            "slippage_cap_bps": self.slippage_cap_bps,
            "cooldown_seconds": self.cooldown_seconds,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ArbProfile":
        venues_raw = payload.get("venues")
        if isinstance(venues_raw, Iterable) and not isinstance(venues_raw, (str, bytes)):
            venues: Tuple[str, ...] = tuple(str(v) for v in venues_raw)
        else:
            venues = ()
        return cls(
            min_net_bps=float(payload.get("min_net_bps", cls.min_net_bps)),
            min_net_eur=float(payload.get("min_net_eur", cls.min_net_eur)),
            test_size_eur=float(payload.get("test_size_eur", cls.test_size_eur)),
            venues=venues,
            slippage_cap_bps=float(payload.get("slippage_cap_bps", cls.slippage_cap_bps)),
            cooldown_seconds=int(payload.get("cooldown_seconds", cls.cooldown_seconds)),
        )


class ProfileService:
    """Access layer for chat-specific arbitrage profiles."""

    def __init__(self, store: InMemoryStore, *, default_profile: Optional[ArbProfile] = None) -> None:
        self._store = store
        self._default = default_profile or ArbProfile()

    async def get(self, chat_id: int) -> ArbProfile:
        payload = await self._store.get_profile(chat_id)
        if payload:
            return ArbProfile.from_dict(payload)
        return replace(self._default)

    async def update(self, chat_id: int, **kwargs: object) -> ArbProfile:
        profile = await self.get(chat_id)
        for key, value in kwargs.items():
            if not hasattr(profile, key):
                continue
            setattr(profile, key, value)  # type: ignore[arg-type]
        await self._store.record_profile(chat_id, profile.to_dict())
        return profile

    async def reset(self, chat_id: int) -> ArbProfile:
        fresh = replace(self._default)
        await self._store.record_profile(chat_id, fresh.to_dict())
        return fresh
