import pytest

from mcp_app_telegram.config import ScanPairDefinition
from mcp_app_telegram.infra.store import InMemoryStore


@pytest.mark.asyncio
async def test_initialize_pairs_and_subscriptions():
    store = InMemoryStore()
    pairs = (
        ScanPairDefinition(
            pair_key="base:pair1",
            symbols="PAIR1/USDC",
            base_symbol="PAIR1",
            quote_symbol="USDC",
            base_address="0x1",
            quote_address="0x2",
            dex_id="dex",
            fee_tiers=("0.05",),
        ),
        ScanPairDefinition(
            pair_key="base:pair2",
            symbols="PAIR2/USDC",
            base_symbol="PAIR2",
            quote_symbol="USDC",
            base_address="0x3",
            quote_address="0x4",
            dex_id="dex",
            fee_tiers=("0.05",),
        ),
    )

    await store.initialize_pairs(pairs, scan_size=1)
    scan_set = await store.get_scan_set()
    assert scan_set == ("base:pair1",)

    await store.subscribe_pair(123, "base:pair1")
    all_active, explicit = await store.list_user_subscriptions(123)
    assert all_active is False
    assert explicit == ("base:pair1",)

    subscribers = await store.list_pair_subscribers("base:pair1")
    assert subscribers == (123,)

    await store.subscribe_all(999)
    subscribers = await store.list_pair_subscribers("base:pair1")
    assert subscribers == (123, 999)

    await store.unsubscribe_pair(123, "base:pair1")
    subscribers = await store.list_pair_subscribers("base:pair1")
    assert subscribers == (999,)

    removed, added = await store.set_scan_set(["base:pair1", "base:pair2"])
    assert added == {"base:pair2"}
    assert removed == set()


@pytest.mark.asyncio
async def test_initialize_preserves_existing_scan():
    store = InMemoryStore()
    await store.initialize_pairs(
        (
            ScanPairDefinition(
                pair_key="base:pair1",
                symbols="PAIR1/USDC",
                base_symbol="PAIR1",
                quote_symbol="USDC",
                base_address="0x1",
                quote_address="0x2",
                dex_id="dex",
                fee_tiers=(),
            ),
        ),
        scan_size=1,
    )
    await store.set_scan_set(["base:pair1"])
    await store.initialize_pairs(
        (
            ScanPairDefinition(
                pair_key="base:pair2",
                symbols="PAIR2/USDC",
                base_symbol="PAIR2",
                quote_symbol="USDC",
                base_address="0x3",
                quote_address="0x4",
                dex_id="dex",
                fee_tiers=(),
            ),
        ),
        scan_size=1,
    )
    scan_set = await store.get_scan_set()
    assert scan_set == ("base:pair1",)
