import pytest

from mcp_app_telegram.alerts import GasAlertManager, GasAlertSubscription
from mcp_app_telegram.mcp_client import GasStats


@pytest.mark.asyncio
async def test_alert_subscription_matching():
    manager = GasAlertManager()
    subscription = GasAlertSubscription(chat_id=1, threshold=0.8, direction="below")
    await manager.add_subscription(subscription)

    stats = GasStats(safe=0.7, standard=0.8, fast=0.6, block_lag_seconds=5.0, base_fee=0.7)
    matches = await manager.evaluate(stats)

    assert subscription in matches
    remaining = await manager.list_subscriptions()
    assert subscription not in remaining


@pytest.mark.asyncio
async def test_clear_for_chat():
    manager = GasAlertManager()
    subscription = GasAlertSubscription(chat_id=1, threshold=1.2, direction="above")
    await manager.add_subscription(subscription)
    await manager.clear_for_chat(1)

    remaining = await manager.list_subscriptions()
    assert not remaining
