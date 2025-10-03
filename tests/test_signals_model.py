import pytest

from mcp_app_telegram.arb.profiles import ArbProfile
from mcp_app_telegram.arb.signals import ArbCalculationInput, ArbSignalService, MarketLeg
from mcp_app_telegram.infra.store import PairMetadata


@pytest.mark.parametrize(
    "gross_bps, fees, slippage, gas, size, expected_net",
    [
        (80.0, (5.0, 5.0), 10.0, 1.0, 500.0, 80.0 - (5 + 5 + 10 + (1 / 500) * 10000) - 10),
        (50.0, (3.0, 3.0), 5.0, 0.5, 1000.0, 50.0 - (3 + 3 + 5 + (0.5 / 1000) * 10000) - 10),
    ],
)
def test_cost_model_net_bps(gross_bps, fees, slippage, gas, size, expected_net):
    service = ArbSignalService(default_mev_buffer_bps=10.0)
    payload = ArbCalculationInput(
        pair=PairMetadata(
            pair_key="base:pair1",
            symbols="PAIR1/USDC",
            base_symbol="PAIR1",
            quote_symbol="USDC",
            base_address="0x1",
            quote_address="0x2",
            dex_id="dex",
            fee_tiers=("0.05",),
        ),
        buy_leg=MarketLeg("dexA", fees[0], "PAIR1"),
        sell_leg=MarketLeg("dexB", fees[1], "PAIR1"),
        gross_bps=gross_bps,
        size_eur=size,
        slippage_bps=slippage,
        gas_cost_eur=gas,
        mev_buffer_bps=10.0,
    )
    profile = ArbProfile(min_net_bps=20.0, min_net_eur=0.5)
    signal = service.calculate(payload, profile)
    assert pytest.approx(signal.costs.net_bps, rel=1e-3) == pytest.approx(expected_net, rel=1e-3)
    net_eur = signal.costs.net_eur
    assert pytest.approx(net_eur, rel=1e-3) == pytest.approx(signal.costs.net_bps / 10000 * size, rel=1e-3)


def test_thresholds_and_confidence():
    service = ArbSignalService(default_mev_buffer_bps=8.0)
    payload = ArbCalculationInput(
        pair=PairMetadata(
            pair_key="base:pairX",
            symbols="PAIRX/USDC",
            base_symbol="PAIRX",
            quote_symbol="USDC",
            base_address=None,
            quote_address=None,
            dex_id=None,
            fee_tiers=(),
        ),
        buy_leg=MarketLeg("dexA", 5.0, "PAIRX"),
        sell_leg=MarketLeg("dexB", 5.0, "PAIRX"),
        gross_bps=80.0,
        size_eur=1000.0,
        slippage_bps=10.0,
        gas_cost_eur=2.0,
        mev_buffer_bps=0.0,
    )
    profile = ArbProfile(min_net_bps=20.0, min_net_eur=0.5)
    signal = service.calculate(payload, profile)
    assert signal.meets_threshold is True
    assert 0.0 <= signal.confidence <= 1.0
