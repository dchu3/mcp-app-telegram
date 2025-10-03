from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

import httpx

from ..arb.signals import ArbCalculationInput, MarketLeg
from ..infra.store import PairMetadata
from ..infra.swr import SwrFetchResult
from ..mcp import EvmMcpClient


DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"
COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=eur"


@dataclass(slots=True)
class MarketBaseData:
    pair: PairMetadata
    buy_leg: MarketLeg
    sell_leg: MarketLeg
    buy_price_usd: float
    sell_price_usd: float
    gross_bps: float
    slippage_bps: float
    gas_cost_eur: float
    mev_buffer_bps: float
    base_payload: Mapping[str, Any]


class MarketDataFetcher:
    """Fetch market quotes and cost estimates for tracked pairs."""

    def __init__(
        self,
        evm_client: EvmMcpClient,
        *,
        default_size_eur: float,
        mev_buffer_bps: float,
        timeout: float = 10.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._evm_client = evm_client
        self._default_size_eur = default_size_eur
        self._mev_buffer_bps = mev_buffer_bps
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = http_client is None
        self._eth_price_cache: tuple[float, float] = (0.0, 0.0)  # (timestamp, price)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_pair(self, metadata: PairMetadata) -> SwrFetchResult:
        try:
            data = await self._collect_market_data(metadata)
        except Exception as exc:  # pragma: no cover - network errors treated as stale
            payload: Dict[str, Any] = {
                "pair_key": metadata.pair_key,
                "error": str(exc),
                "timestamp": time.time(),
            }
            return SwrFetchResult(payload=payload, status="stale")

        payload = {
            "pair_key": metadata.pair_key,
            "symbols": metadata.symbols,
            "buy_leg": {
                "venue": data.buy_leg.venue,
                "fee_bps": data.buy_leg.fee_bps,
                "symbol": data.buy_leg.symbol,
                "price_usd": data.buy_price_usd,
            },
            "sell_leg": {
                "venue": data.sell_leg.venue,
                "fee_bps": data.sell_leg.fee_bps,
                "symbol": data.sell_leg.symbol,
                "price_usd": data.sell_price_usd,
            },
            "gross_bps": data.gross_bps,
            "slippage_bps": data.slippage_bps,
            "gas_cost_eur": data.gas_cost_eur,
            "mev_buffer_bps": data.mev_buffer_bps,
            "default_size_eur": self._default_size_eur,
            "source": "dexscreener",  # best-effort indicator
            "timestamp": time.time(),
        }
        payload.update(data.base_payload)
        return SwrFetchResult(payload=payload)

    async def _collect_market_data(self, metadata: PairMetadata) -> MarketBaseData:
        if not metadata.base_address:
            raise ValueError("pair definition missing base token address")

        pairs = await self._fetch_dexscreener_pairs(metadata.base_address)
        filtered = []
        for pair in pairs:
            if pair.get("chainId") != "base":
                continue
            quote = pair.get("quoteToken") or {}
            quote_addr = str(quote.get("address") or "").lower()
            if metadata.quote_address and quote_addr != metadata.quote_address.lower():
                continue
            price_usd = float(pair.get("priceUsd") or 0.0)
            if price_usd <= 0:
                continue
            filtered.append(pair)

        if len(filtered) < 2:
            raise RuntimeError("insufficient venues for spread calculation")

        filtered.sort(key=lambda item: float(item.get("priceUsd") or 0.0))
        buy = filtered[0]
        sell = filtered[-1]

        buy_price = float(buy.get("priceUsd") or 0.0)
        sell_price = float(sell.get("priceUsd") or 0.0)
        if buy_price <= 0 or sell_price <= 0:
            raise RuntimeError("invalid price data returned")

        gross_bps = (sell_price / buy_price - 1.0) * 10_000
        gross_bps = float(gross_bps)

        fee_bps = self._resolve_fee_bps(metadata.fee_tiers)
        buy_leg = MarketLeg(venue=str(buy.get("dexId") or "unknown"), fee_bps=fee_bps, symbol=metadata.base_symbol)
        sell_leg = MarketLeg(venue=str(sell.get("dexId") or "unknown"), fee_bps=fee_bps, symbol=metadata.base_symbol)

        gas_cost_eur = await self._estimate_gas_cost_eur()

        return MarketBaseData(
            pair=metadata,
            buy_leg=buy_leg,
            sell_leg=sell_leg,
            buy_price_usd=buy_price,
            sell_price_usd=sell_price,
            gross_bps=gross_bps,
            slippage_bps=0.0,
            gas_cost_eur=gas_cost_eur,
            mev_buffer_bps=self._mev_buffer_bps,
            base_payload={
                "buy_url": buy.get("url"),
                "sell_url": sell.get("url"),
            },
        )

    async def _fetch_dexscreener_pairs(self, base_address: str) -> Sequence[Mapping[str, Any]]:
        url = DEXSCREENER_TOKEN_URL.format(address=base_address)
        response = await self._client.get(url)
        response.raise_for_status()
        payload = response.json()
        pairs = payload.get("pairs") if isinstance(payload, Mapping) else None
        if not isinstance(pairs, Sequence):
            return []
        return [pair for pair in pairs if isinstance(pair, Mapping)]

    def _resolve_fee_bps(self, tiers: Sequence[str]) -> float:
        if not tiers:
            return 0.0
        try:
            percent = float(tiers[0])
        except ValueError:
            return 0.0
        return max(0.0, percent * 100)

    async def _estimate_gas_cost_eur(self) -> float:
        stats = await self._evm_client.fetch_gas_stats()
        fast_gwei = getattr(stats, "fast", None)
        if fast_gwei is None:
            return 0.0
        gas_limit = 180_000
        eth_price_eur = await self._fetch_eth_eur()
        cost_eth = fast_gwei * 1e-9 * gas_limit
        return cost_eth * eth_price_eur

    async def _fetch_eth_eur(self) -> float:
        now = time.time()
        cached_ts, cached_price = self._eth_price_cache
        if now - cached_ts < 60 and cached_price > 0:
            return cached_price
        response = await self._client.get(COINGECKO_SIMPLE_PRICE_URL)
        response.raise_for_status()
        payload = response.json()
        price = payload.get("ethereum", {}).get("eur") if isinstance(payload, Mapping) else None
        if isinstance(price, (int, float)) and price > 0:
            self._eth_price_cache = (now, float(price))
            return float(price)
        raise RuntimeError("unable to fetch ETH/EUR price")

    @staticmethod
    def build_calculation_input(
        metadata: PairMetadata,
        payload: Mapping[str, Any],
        profile_size: float,
    ) -> ArbCalculationInput:
        buy_leg = payload.get("buy_leg", {})
        sell_leg = payload.get("sell_leg", {})
        return ArbCalculationInput(
            pair=metadata,
            buy_leg=MarketLeg(
                venue=str(buy_leg.get("venue", "unknown")),
                fee_bps=float(buy_leg.get("fee_bps", 0.0)),
                symbol=str(buy_leg.get("symbol", metadata.base_symbol)),
            ),
            sell_leg=MarketLeg(
                venue=str(sell_leg.get("venue", "unknown")),
                fee_bps=float(sell_leg.get("fee_bps", 0.0)),
                symbol=str(sell_leg.get("symbol", metadata.base_symbol)),
            ),
            gross_bps=float(payload.get("gross_bps", 0.0)),
            size_eur=profile_size,
            slippage_bps=float(payload.get("slippage_bps", 0.0)),
            gas_cost_eur=float(payload.get("gas_cost_eur", 0.0)),
            mev_buffer_bps=float(payload.get("mev_buffer_bps", 0.0)),
        )
