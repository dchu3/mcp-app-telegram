"""Manage gas alert subscriptions and evaluation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .mcp_client import GasStats


@dataclass(slots=True)
class GasAlertSubscription:
    chat_id: int
    threshold: float
    direction: str  # 'below' or 'above'

    def should_alert(self, stats: GasStats) -> bool:
        # Alerts are based on the "fast" gas tier for responsiveness.
        if self.direction == "below":
            return stats.fast <= self.threshold
        return stats.fast >= self.threshold

    def describe(self) -> str:
        comparator = "≤" if self.direction == "below" else "≥"
        return f"fast gas {comparator} {self.threshold:.2f} gwei"


class GasAlertManager:
    """Tracks gas alert subscriptions in memory."""

    def __init__(self) -> None:
        self._subscriptions: List[GasAlertSubscription] = []
        self._lock = asyncio.Lock()

    async def list_subscriptions(self) -> Sequence[GasAlertSubscription]:
        async with self._lock:
            return tuple(self._subscriptions)

    async def add_subscription(self, subscription: GasAlertSubscription) -> None:
        async with self._lock:
            self._subscriptions.append(subscription)

    async def clear_for_chat(self, chat_id: int) -> None:
        async with self._lock:
            self._subscriptions = [s for s in self._subscriptions if s.chat_id != chat_id]

    async def evaluate(self, stats: GasStats) -> Iterable[GasAlertSubscription]:
        async with self._lock:
            matches = [s for s in self._subscriptions if s.should_alert(stats)]
            if matches:
                self._subscriptions = [s for s in self._subscriptions if s not in matches]
            return tuple(matches)
