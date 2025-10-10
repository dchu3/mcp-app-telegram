import json

from mcp_app_telegram.admin_state import (
    AdminState,
    AdminStateRepository,
    TokenAdminRecord,
    TokenThresholds,
)
from mcp_app_telegram.infra.store import PairMetadata


def test_admin_state_roundtrip(tmp_path):
    path = tmp_path / "admin_state.db"
    repo = AdminStateRepository(path)

    state = AdminState()
    metadata = PairMetadata(
        pair_key="base:token/quote@dex",
        symbols="TK/USDC",
        base_symbol="TK",
        quote_symbol="USDC",
        base_address="0xbase",
        quote_address="0xquote",
        dex_id="dex",
        fee_tiers=("0.30",),
    )
    state.tokens[metadata.pair_key] = TokenAdminRecord(
        metadata=metadata,
        thresholds=TokenThresholds(min_liquidity_usd=25_000.0, min_txns_24h=1_000),
    )
    state.global_thresholds = TokenThresholds(min_liquidity_usd=50_000.0)
    state.mev_buffer_bps = 12.5
    state.default_profile = {"min_net_bps": 15.0, "cooldown_seconds": 90}

    repo.save(state)

    loaded = repo.load()
    assert loaded.mev_buffer_bps == 12.5
    assert loaded.global_thresholds.min_liquidity_usd == 50_000.0
    assert loaded.tokens[metadata.pair_key].metadata.base_address == "0xbase"
    assert loaded.tokens[metadata.pair_key].thresholds.min_txns_24h == 1_000
    assert loaded.default_profile["min_net_bps"] == 15.0


def test_admin_state_handles_invalid_payload(tmp_path):
    path = tmp_path / "admin_state.db"
    legacy = tmp_path / "admin_state.json"
    legacy.write_text("not-json")
    repo = AdminStateRepository(path)
    loaded = repo.load()
    assert isinstance(loaded, AdminState)
    assert loaded.tokens == {}


def test_admin_state_migrates_from_json(tmp_path):
    db_path = tmp_path / "admin_state.db"
    legacy_path = tmp_path / "admin_state.json"
    payload = {
        "tokens": {
            "base:token/quote@dex": {
                "metadata": {
                    "pair_key": "base:token/quote@dex",
                    "symbols": "TK/USDC",
                    "base_symbol": "TK",
                    "quote_symbol": "USDC",
                    "base_address": "0xbase",
                    "quote_address": "0xquote",
                    "dex_id": "dex",
                    "fee_tiers": ["0.30"],
                },
                "thresholds": {"min_liquidity_usd": 30000},
            }
        },
        "global_thresholds": {"min_liquidity_usd": 75000},
        "mev_buffer_bps": 9.5,
        "default_profile": {"min_net_bps": 12.0},
    }
    legacy_path.write_text(json.dumps(payload))

    repo = AdminStateRepository(db_path)
    state = repo.load()

    assert "base:token/quote@dex" in state.tokens
    record = state.tokens["base:token/quote@dex"]
    assert record.metadata is not None
    assert record.metadata.dex_id == "dex"
    assert record.thresholds.min_liquidity_usd == 30000
    assert state.global_thresholds.min_liquidity_usd == 75000
    assert state.mev_buffer_bps == 9.5
    assert state.default_profile["min_net_bps"] == 12.0

    backup_candidates = list(tmp_path.glob("admin_state.json.bak"))
    assert backup_candidates, "legacy file should be archived"
