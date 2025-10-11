from __future__ import annotations

from mcp_app_telegram.admin_seed import (
    ensure_tokens_seeded,
    metadata_from_scan_pairs,
    metadata_from_state,
)
from mcp_app_telegram.admin_state import AdminStateRepository, TokenThresholds
from mcp_app_telegram.config import ScanPairDefinition


def _sample_pairs() -> tuple[ScanPairDefinition, ...]:
    return (
        ScanPairDefinition(
            pair_key="base:alpha/usdc@dex",
            symbols="ALPHA/USDC",
            base_symbol="ALPHA",
            quote_symbol="USDC",
            base_address="0x1",
            quote_address="0x2",
            dex_id="dex",
            fee_tiers=("0.30",),
        ),
        ScanPairDefinition(
            pair_key="base:beta/usdc@dex",
            symbols="BETA/USDC",
            base_symbol="BETA",
            quote_symbol="USDC",
            base_address="0x3",
            quote_address="0x4",
            dex_id="dex",
            fee_tiers=("0.05",),
        ),
    )


def test_ensure_tokens_seeded_writes_metadata(tmp_path) -> None:
    repo = AdminStateRepository(tmp_path / "admin_state.db")
    state = repo.load()

    defaults = TokenThresholds(
        min_liquidity_usd=10_000.0,
        min_volume_24h_usd=50_000.0,
        min_txns_24h=250,
    )

    changed = ensure_tokens_seeded(
        repository=repo,
        state=state,
        scan_pairs=_sample_pairs(),
        default_thresholds=defaults,
    )

    assert changed is True

    reloaded = repo.load()
    alpha_record = reloaded.tokens["base:alpha/usdc@dex"]
    assert alpha_record.metadata is not None
    assert alpha_record.metadata.symbols == "ALPHA/USDC"
    assert alpha_record.thresholds.min_liquidity_usd == 10_000.0
    assert alpha_record.thresholds.min_volume_24h_usd == 50_000.0
    assert alpha_record.thresholds.min_txns_24h == 250

    metadata = metadata_from_state(reloaded)
    assert {item.pair_key for item in metadata} == {
        "base:alpha/usdc@dex",
        "base:beta/usdc@dex",
    }


def test_ensure_tokens_seeded_is_idempotent(tmp_path) -> None:
    repo = AdminStateRepository(tmp_path / "admin_state.db")
    state = repo.load()

    ensure_tokens_seeded(
        repository=repo,
        state=state,
        scan_pairs=_sample_pairs(),
        default_thresholds=TokenThresholds(),
    )

    updated_state = repo.load()
    beta_record = updated_state.tokens["base:beta/usdc@dex"]
    beta_record.thresholds = TokenThresholds(min_liquidity_usd=1234.0)
    repo.save(updated_state)

    subsequent_state = repo.load()
    ensure_tokens_seeded(
        repository=repo,
        state=subsequent_state,
        scan_pairs=_sample_pairs(),
        default_thresholds=TokenThresholds(
            min_liquidity_usd=9999.0,
            min_volume_24h_usd=8888.0,
            min_txns_24h=777,
        ),
    )

    reloaded = repo.load()
    beta_thresholds = reloaded.tokens["base:beta/usdc@dex"].thresholds
    assert beta_thresholds.min_liquidity_usd == 1234.0
    assert beta_thresholds.min_volume_24h_usd is None


def test_metadata_from_scan_pairs(tmp_path) -> None:
    pairs = _sample_pairs()
    metadata = metadata_from_scan_pairs(pairs)
    assert len(metadata) == 2
    assert metadata[0].pair_key == pairs[0].pair_key
    assert metadata[1].fee_tiers == pairs[1].fee_tiers
