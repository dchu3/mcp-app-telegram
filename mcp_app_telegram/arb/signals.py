from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..infra.store import PairMetadata
from .profiles import ArbProfile


@dataclass(slots=True)
class MarketLeg:
    venue: str
    fee_bps: float
    symbol: str


@dataclass(slots=True)
class ArbCalculationInput:
    pair: PairMetadata
    buy_leg: MarketLeg
    sell_leg: MarketLeg
    gross_bps: float
    size_eur: float
    slippage_bps: float
    gas_cost_eur: float
    mev_buffer_bps: float


@dataclass(slots=True)
class ArbCostBreakdown:
    gross_bps: float
    net_bps: float
    net_eur: float
    lp_fee_bps: float
    slippage_bps: float
    gas_bps: float
    gas_cost_eur: float
    mev_buffer_bps: float


@dataclass(slots=True)
class ArbSignal:
    pair_key: str
    size_eur: float
    buy_leg: MarketLeg
    sell_leg: MarketLeg
    costs: ArbCostBreakdown
    meets_threshold: bool
    confidence: float
    status: str = "evaluated"


class ArbSignalService:
    """Pure arbitrage cost model calculations."""

    def __init__(self, *, default_mev_buffer_bps: float = 10.0) -> None:
        self._default_mev_buffer_bps = default_mev_buffer_bps

    def calculate(self, payload: ArbCalculationInput, profile: ArbProfile) -> ArbSignal:
        gas_bps = self._gas_to_bps(payload.gas_cost_eur, payload.size_eur)
        lp_fee_bps = max(0.0, payload.buy_leg.fee_bps) + max(0.0, payload.sell_leg.fee_bps)
        slippage_bps = max(0.0, payload.slippage_bps)
        mev_buffer_bps = payload.mev_buffer_bps or self._default_mev_buffer_bps
        net_bps = payload.gross_bps - (lp_fee_bps + slippage_bps + mev_buffer_bps + gas_bps)
        net_eur = (net_bps / 10_000.0) * payload.size_eur
        meets_threshold = net_bps >= profile.min_net_bps and net_eur >= profile.min_net_eur
        confidence = self._compute_confidence(payload, net_bps)
        breakdown = ArbCostBreakdown(
            gross_bps=payload.gross_bps,
            net_bps=net_bps,
            net_eur=net_eur,
            lp_fee_bps=lp_fee_bps,
            slippage_bps=slippage_bps,
            gas_bps=gas_bps,
            gas_cost_eur=payload.gas_cost_eur,
            mev_buffer_bps=mev_buffer_bps,
        )
        return ArbSignal(
            pair_key=payload.pair.pair_key,
            size_eur=payload.size_eur,
            buy_leg=payload.buy_leg,
            sell_leg=payload.sell_leg,
            costs=breakdown,
            meets_threshold=meets_threshold,
            confidence=confidence,
        )

    def _gas_to_bps(self, gas_cost_eur: float, size_eur: float) -> float:
        if size_eur <= 0:
            return math.inf
        return (gas_cost_eur / size_eur) * 10_000

    def _compute_confidence(self, payload: ArbCalculationInput, net_bps: float) -> float:
        headroom = payload.gross_bps - net_bps
        if payload.gross_bps <= 0:
            return 0.0
        confidence = max(0.0, min(1.0, net_bps / (payload.gross_bps + 1e-6)))
        if headroom <= 0:
            confidence = min(1.0, confidence + 0.1)
        return round(confidence, 3)
