import time
from types import SimpleNamespace

import pytest

from mcp_app_telegram.arb.profiles import ProfileService
from mcp_app_telegram.arb.signals import ArbSignalService
from mcp_app_telegram.config import ScanPairDefinition
from mcp_app_telegram.infra.store import InMemoryStore, PairMetadata, SwrSnapshot
from mcp_app_telegram.market.dispatcher import MarketUpdateDispatcher
from mcp_app_telegram.market.fetcher import MarketDataFetcher


class StubResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self) -> None:  # pragma: no cover - simple stub
        return None

    def json(self):
        return self._data


class StubHttpClient:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    async def get(self, url):
        self.calls.append(url)
        for prefix, payload in self._responses:
            if url.startswith(prefix):
                return StubResponse(payload)
        raise AssertionError(f"Unexpected URL {url}")

    async def aclose(self):  # pragma: no cover - no-op for tests
        return None


class StubEvmClient:
    async def fetch_gas_stats(self):
        return SimpleNamespace(fast=20.0)


@pytest.mark.asyncio
async def test_market_fetcher_produces_payload():
    metadata = PairMetadata(
        pair_key="base:token/quote@dex",
        symbols="TK/USDC",
        base_symbol="TK",
        quote_symbol="USDC",
        base_address="0xbase",
        quote_address="0xquote",
        dex_id="dex",
        fee_tiers=("0.05",),
    )

    dex_payload = {
        "pairs": [
            {
                "chainId": "base",
                "dexId": "dexA",
                "priceUsd": "1.00",
                "url": "https://dexA",
                "quoteToken": {"address": "0xquote"},
            },
            {
                "chainId": "base",
                "dexId": "dexB",
                "priceUsd": "1.02",
                "url": "https://dexB",
                "quoteToken": {"address": "0xquote"},
            },
        ]
    }
    coingecko_payload = {"ethereum": {"eur": 2000}}
    http_client = StubHttpClient([
        ("https://api.dexscreener.com", dex_payload),
        ("https://api.coingecko.com", coingecko_payload),
    ])

    fetcher = MarketDataFetcher(
        StubEvmClient(),
        default_size_eur=500.0,
        mev_buffer_bps=10.0,
        http_client=http_client,
    )
    result = await fetcher.fetch_pair(metadata)
    assert result.status == "fresh"
    payload = result.payload
    assert payload["pair_key"] == metadata.pair_key
    assert pytest.approx(payload["gross_bps"], rel=1e-3) == 200.0
    await fetcher.close()


@pytest.mark.asyncio
async def test_market_fetcher_handles_insufficient_pairs():
    metadata = PairMetadata(
        pair_key="base:token/quote@dex",
        symbols="TK/USDC",
        base_symbol="TK",
        quote_symbol="USDC",
        base_address="0xbase",
        quote_address="0xquote",
        dex_id="dex",
        fee_tiers=("0.05",),
    )
    http_client = StubHttpClient([
        ("https://api.dexscreener.com", {"pairs": [
            {"chainId": "base", "priceUsd": "1.00", "quoteToken": {"address": "0xquote"}}
        ]}),
        ("https://api.coingecko.com", {"ethereum": {"eur": 2000}}),
    ])
    fetcher = MarketDataFetcher(
        StubEvmClient(),
        default_size_eur=500.0,
        mev_buffer_bps=10.0,
        http_client=http_client,
    )
    result = await fetcher.fetch_pair(metadata)
    assert result.status == "stale"
    assert "error" in result.payload
    await fetcher.close()


@pytest.mark.asyncio
async def test_dispatcher_sends_when_threshold_met(monkeypatch):
    store = InMemoryStore()
    definition = ScanPairDefinition(
        pair_key="base:pair1",
        symbols="PAIR/USDC",
        base_symbol="PAIR",
        quote_symbol="USDC",
        base_address="0xbase",
        quote_address="0xquote",
        dex_id="dex",
        fee_tiers=("0.05",),
    )
    await store.initialize_pairs([definition], scan_size=1)
    await store.subscribe_pair(101, definition.pair_key)

    profile_service = ProfileService(store)
    await profile_service.update(101, min_net_bps=10.0)
    signal_service = ArbSignalService(default_mev_buffer_bps=10.0)

    class StubBot:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text):
            self.messages.append((chat_id, text))

    application = SimpleNamespace(bot=StubBot())
    dispatcher = MarketUpdateDispatcher(application, store, profile_service, signal_service)

    metadata = await store.get_pair_metadata(definition.pair_key)
    payload = {
        "pair_key": definition.pair_key,
        "symbols": definition.symbols,
        "buy_leg": {"venue": "dexA", "fee_bps": 5.0, "symbol": "PAIR"},
        "sell_leg": {"venue": "dexB", "fee_bps": 5.0, "symbol": "PAIR"},
        "gross_bps": 120.0,
        "slippage_bps": 0.0,
        "gas_cost_eur": 1.0,
        "mev_buffer_bps": 10.0,
    }
    snapshot = SwrSnapshot(
        pair_key=definition.pair_key,
        payload=payload,
        timestamp=time.time(),
        ttl=15.0,
    )
    await dispatcher.handle_snapshot(metadata, snapshot, stale=False)
    assert application.bot.messages

    # Subsequent call with same snapshot should respect cooldown
    await dispatcher.handle_snapshot(metadata, snapshot, stale=False)
    assert len(application.bot.messages) == 1

    # Raise thresholds so signal no longer fires
    await profile_service.update(101, min_net_bps=1000.0)
    await dispatcher.handle_snapshot(metadata, snapshot, stale=False)
    assert len(application.bot.messages) == 1
