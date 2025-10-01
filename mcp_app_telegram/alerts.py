
"""Manage gas alert subscriptions and evaluation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable, List

from .mcp_client import GasStats
from .database import add_gas_alert, remove_gas_alert, get_gas_alerts, get_gas_alerts_for_chat, remove_all_gas_alerts_for_chat

@dataclass(slots=True)
class GasAlertSubscription:
    chat_id: int
    network: str
    threshold: float
    direction: str  # 'below' or 'above'

    def should_alert(self, stats: GasStats) -> bool:
        # Alerts are based on the "fast" gas tier for responsiveness.
        if self.direction == "below":
            return stats.fast <= self.threshold
        return stats.fast >= self.threshold

    def describe(self) -> str:
        comparator = "≤" if self.direction == "below" else "≥"
        return f"fast gas {comparator} {self.threshold:.2f} gwei on {self.network}"


class GasAlertManager:
    """Tracks gas alert subscriptions in the database."""

    def __init__(self) -> None:
        pass

    async def list_subscriptions(self, chat_id: int) -> List[GasAlertSubscription]:
        alerts = get_gas_alerts_for_chat(chat_id)
        return [GasAlertSubscription(chat_id=chat_id, network=alert['network'], threshold=alert['price_threshold'], direction=alert['direction']) for alert in alerts]

    async def add_subscription(self, subscription: GasAlertSubscription) -> None:
        add_gas_alert(subscription.chat_id, subscription.network, subscription.threshold, subscription.direction)

    async def clear_for_chat(self, chat_id: int) -> None:
        remove_all_gas_alerts_for_chat(chat_id)

    async def evaluate(self, network: str, stats: GasStats) -> Iterable[GasAlertSubscription]:
        alerts = get_gas_alerts(network)
        matches = []
        for alert in alerts:
            subscription = GasAlertSubscription(chat_id=alert['chat_id'], network=network, threshold=alert['price_threshold'], direction=alert['direction'])
            if subscription.should_alert(stats):
                matches.append(subscription)
        
        if matches:
            for sub in matches:
                remove_gas_alert(sub.chat_id, sub.network, sub.direction)

        return tuple(matches)
