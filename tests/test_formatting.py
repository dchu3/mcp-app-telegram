from mcp_app_telegram.formatting import format_account, format_gas_stats, format_transaction
from mcp_app_telegram.mcp_client import AccountSummary, GasStats, TransactionSummary


def test_format_gas_stats():
    stats = GasStats(safe=0.5, standard=0.7, fast=0.9, block_lag_seconds=3.2, base_fee=0.6)
    text = format_gas_stats(stats)
    assert "Safe: 0.5000 gwei" in text
    assert "Sequencer lag: 3.2000 s" in text


def test_format_transaction():
    summary = TransactionSummary(
        hash="0xabc",
        status="success",
        from_address="0xfrom",
        to_address=None,
        gas_used=21000,
        nonce=1,
        value_wei=123,
    )
    text = format_transaction(summary)
    assert "Hash: 0xabc" in text
    assert "To: Contract creation" in text


def test_format_account():
    summary = AccountSummary(address="0xabc", balance_wei=10**18, nonce=5, is_contract=False)
    text = format_account(summary)
    assert "0xabc" in text
    assert "1.0000 ETH" in text
    assert "Externally Owned Account" in text
