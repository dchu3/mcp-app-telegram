from mcp_app_telegram.formatting import (
    format_account,
    format_gas_stats,
    format_transaction,
    format_dexscreener_pairs,
    format_dexscreener_profiles,
    format_dexscreener_boosts,
    format_dexscreener_orders,
)
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


def test_format_dexscreener_pairs_handles_pairs_list():
    message = format_dexscreener_pairs(
        [
            {
                "baseToken": {"symbol": "AAA"},
                "quoteToken": {"symbol": "BBB"},
                "volume": {"h24": 100},
                "priceUsd": 1.23,
                "liquidity": {"usd": 5000},
                "chainId": "base",
                "dexId": "baseswap",
                "url": "https://dexscreener.com/base/aaa",
            }
        ]
    )

    assert message is not None
    assert "AAA/BBB" in message


def test_format_dexscreener_coins_list():
    message = format_dexscreener_pairs(
        {
            "coins": [
                {
                    "symbol": "AAA",
                    "name": "Alpha",
                    "chainId": "base",
                    "dexId": "dex",
                    "priceUsd": "2.5",
                    "priceChange": {"h1": "3.0"},
                    "volume": {"h24": "10000"},
                },
                {
                    "symbol": "BBB",
                    "chain": "base",
                    "dex": "other",
                    "price": "0.5",
                },
            ]
        }
    )

    assert message is not None
    assert "Trending Coins" in message
    assert "AAA" in message


def test_format_dexscreener_profiles():
    message = format_dexscreener_profiles(
        [
            {
                "chainId": "base",
                "tokenAddress": "0x123",
                "links": [{"url": "https://example.com"}],
            },
            {"chainId": "eth", "tokenAddress": "0x456"},
        ]
    )

    assert "Latest Token Profiles" in message
    assert "0x123" in message


def test_format_dexscreener_boosts():
    message = format_dexscreener_boosts(
        [
            {
                "chainId": "base",
                "tokenAddress": "0xabc",
                "amount": 10,
                "totalAmount": 20,
                "description": "Hot token",
            }
        ],
        heading="Latest Boosted Tokens",
    )

    assert "Latest Boosted Tokens" in message
    assert "0xabc" in message


def test_format_dexscreener_orders():
    message = format_dexscreener_orders(
        [
            {
                "chainId": "base",
                "type": "tokenProfile",
                "status": "approved",
                "paymentTimestamp": 1_734_046_682_984,
            }
        ]
    )

    assert "Token Orders" in message
    assert "approved" in message
