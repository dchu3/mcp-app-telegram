"""Helpers for turning MCP data into Telegram-friendly text."""

from __future__ import annotations

import json
from typing import Iterable, Mapping, Any, Optional, Sequence

from .mcp_client import AccountSummary, GasStats, TransactionSummary


def _format_gwei(value: float) -> str:
    if value >= 10:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.2f}"
    if value >= 0.01:
        return f"{value:,.4f}"
    return f"{value:.6f}"


def _format_wei(value: int) -> str:
    if value == 0:
        return "0 wei"
    ether = value / 10**18
    if ether >= 0.01:
        return f"{ether:.4f} ETH"
    gwei = value / 10**9
    if gwei >= 0.01:
        return f"{gwei:.2f} gwei"
    return f"{value} wei"


def format_gas_stats(stats: GasStats) -> str:
    lines = [
        "⚡️ Base Gas Stats",
        f"Safe: {_format_gwei(stats.safe)} gwei",
        f"Standard: {_format_gwei(stats.standard)} gwei",
        f"Fast: {_format_gwei(stats.fast)} gwei",
        f"Sequencer lag: {stats.block_lag_seconds:.4f} s",
        f"Base fee: {_format_gwei(stats.base_fee)} gwei",
    ]
    return "\n".join(lines)


def format_transaction(summary: TransactionSummary) -> str:
    value_line = f"Value: {summary.value_wei} wei" if summary.value_wei is not None else "Value: n/a"
    lines: Iterable[str] = (
        "📦 Transaction Summary",
        f"Hash: {summary.hash}",
        f"Status: {summary.status}",
        f"From: {summary.from_address}",
        f"To: {summary.to_address or 'Contract creation'}",
        f"Gas used: {summary.gas_used or 'n/a'}",
        f"Nonce: {summary.nonce or 'n/a'}",
        value_line,
    )
    return "\n".join(lines)


def format_account(summary: AccountSummary) -> str:
    lines = (
        "👤 Account Summary",
        f"Address: {summary.address}",
        f"Balance: {_format_wei(summary.balance_wei)}",
        f"Nonce: {summary.nonce}",
        "Type: Contract" if summary.is_contract else "Type: Externally Owned Account",
    )
    return "\n".join(lines)


def format_generic_tool_result(name: str, result: Mapping[str, Any]) -> str:
    """Render an MCP tool result as formatted JSON for Telegram."""

    pretty = json.dumps(result, indent=2, sort_keys=True)
    header = f"🛠️ {name} result" if name else "🛠️ Tool result"
    return f"{header}\n```json\n{pretty}\n```"


def _format_float(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value >= 1:
        return f"{value:,.2f}"
    return f"{value:.4f}"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_dexscreener_pairs(result: Any) -> Optional[str]:
    if isinstance(result, Sequence) and not isinstance(result, Mapping):
        return _format_dexscreener_pairs(result)

    if isinstance(result, Mapping):
        pairs = result.get("pairs")
        if isinstance(pairs, Sequence):
            summary = _format_dexscreener_pairs(pairs)
            if summary:
                return summary
        coins = result.get("coins")
        if isinstance(coins, Sequence):
            return _format_dexscreener_coins(coins)

    return None


def _format_dexscreener_pairs(pairs: Sequence[Any]) -> Optional[str]:
    if not pairs:
        return "📊 Dexscreener: No matching pairs returned."

    best = None
    best_volume = -1.0
    for candidate in pairs:
        if not isinstance(candidate, Mapping):
            continue
        vol = _safe_float((candidate.get("volume") or {}).get("h24")) or 0.0
        if vol > best_volume:
            best_volume = vol
            best = candidate

    if best is None:
        return None

    base = best.get("baseToken") if isinstance(best.get("baseToken"), Mapping) else {}
    quote = best.get("quoteToken") if isinstance(best.get("quoteToken"), Mapping) else {}

    base_symbol = base.get("symbol") or base.get("name") or "?"
    quote_symbol = quote.get("symbol") or quote.get("name") or "?"
    price_usd = _safe_float(best.get("priceUsd"))
    volume_24h = best_volume if best_volume >= 0 else None
    liquidity_usd = _safe_float((best.get("liquidity") or {}).get("usd"))
    chain = best.get("chainId") or "?"
    dex = best.get("dexId") or best.get("dex") or "?"

    summary = (
        f"{base_symbol}/{quote_symbol} on {chain} ({dex}) is trading at ${_format_float(price_usd)}"
        f" (24h vol ${_format_float(volume_24h)}, TVL ${_format_float(liquidity_usd)})."
    )

    extras = sum(1 for candidate in pairs if isinstance(candidate, Mapping)) - 1
    if extras > 0:
        summary += f" {extras} other match(es) available; narrow your query for specifics."

    url = best.get("url")
    if isinstance(url, str) and url:
        return f"📊 Dexscreener: {summary}\n🔗 {url}"

    return f"📊 Dexscreener: {summary}"


def _format_dexscreener_coins(coins: Sequence[Any]) -> Optional[str]:
    filtered = [coin for coin in coins if isinstance(coin, Mapping)]
    if not filtered:
        return "📊 Dexscreener: No coins returned."

    lines = ["📊 Dexscreener: Trending Coins"]
    for coin in filtered[:3]:
        symbol = coin.get("symbol") or coin.get("name") or "?"
        name = coin.get("name") or symbol
        chain = coin.get("chainId") or coin.get("chain") or "?"
        dex = coin.get("dexId") or coin.get("dex") or "?"
        price = _safe_float(coin.get("priceUsd") or coin.get("price"))
        volume = _safe_float((coin.get("volume") or {}).get("h24"))
        change = None
        price_change = coin.get("priceChange")
        if isinstance(price_change, Mapping):
            change = _safe_float(price_change.get("h1") or price_change.get("h24"))
        if change is None:
            change = _safe_float(coin.get("priceChangeH1"))

        change_text = (
            f", {change:+.2f}% in the last hour" if isinstance(change, float) else ""
        )
        volume_text = (
            f", 24h vol ${_format_float(volume)}" if isinstance(volume, float) else ""
        )
        lines.append(
            f"- {symbol} ({name}) on {chain} via {dex}: ${_format_float(price)}{change_text}{volume_text}"
        )

    if len(filtered) > 3:
        lines.append(f"…and {len(filtered) - 3} more results.")

    return "\n".join(lines)


def format_dexscreener_profiles(profiles: Sequence[Any]) -> Optional[str]:
    valid = [profile for profile in profiles if isinstance(profile, Mapping)]
    if not valid:
        return "📘 Dexscreener: No token profiles found."

    lines = ["📘 Dexscreener: Latest Token Profiles"]
    for profile in valid[:3]:
        chain = profile.get("chainId") or "?"
        address = profile.get("tokenAddress") or "?"
        links = profile.get("links") if isinstance(profile.get("links"), list) else []
        link = None
        for entry in links:
            if isinstance(entry, Mapping) and entry.get("url"):
                link = entry.get("url")
                break
        link_suffix = f" — {link}" if isinstance(link, str) else ""
        lines.append(f"- {chain}: {address}{link_suffix}")

    if len(valid) > 3:
        lines.append(f"…and {len(valid) - 3} more profiles.")

    return "\n".join(lines)


def format_dexscreener_boosts(tokens: Sequence[Any], *, heading: str) -> Optional[str]:
    valid = [token for token in tokens if isinstance(token, Mapping)]
    if not valid:
        return f"🚀 Dexscreener: No {heading.lower()} data available."

    lines = [f"🚀 Dexscreener: {heading}"]
    for token in valid[:5]:
        chain = token.get("chainId") or "?"
        address = token.get("tokenAddress") or "?"
        amount = _safe_float(token.get("amount"))
        total = _safe_float(token.get("totalAmount"))
        desc = token.get("description")
        part = f"- {chain}: {address}"
        if isinstance(amount, float):
            part += f" | Boost {amount:g}"
        if isinstance(total, float) and total != amount:
            part += f" / {total:g}"
        if isinstance(desc, str) and desc:
            part += f" — {desc}"
        lines.append(part)

    if len(valid) > 5:
        lines.append(f"…and {len(valid) - 5} more tokens.")

    return "\n".join(lines)


def format_dexscreener_orders(orders: Sequence[Any]) -> Optional[str]:
    valid = [order for order in orders if isinstance(order, Mapping)]
    if not valid:
        return "📝 Dexscreener: No paid orders found."

    lines = ["📝 Dexscreener: Token Orders"]
    from datetime import datetime, UTC

    for order in valid[:5]:
        chain = order.get("chainId") or "?"
        otype = order.get("type") or "order"
        status = order.get("status") or "unknown"
        timestamp = order.get("paymentTimestamp")
        if isinstance(timestamp, (int, float)):
            seconds = int(timestamp) // 1000
            try:
                dt = datetime.fromtimestamp(seconds, tz=UTC)
                ts_text = dt.strftime("%Y-%m-%d %H:%M UTC")
            except (OverflowError, OSError, ValueError):
                ts_text = str(timestamp)
        else:
            ts_text = "n/a"
        lines.append(f"- {chain} {otype} {status} at {ts_text}")

    if len(valid) > 5:
        lines.append(f"…and {len(valid) - 5} more orders.")

    return "\n".join(lines)
