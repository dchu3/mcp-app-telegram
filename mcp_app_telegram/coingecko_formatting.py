"""Helpers for summarising Coingecko MCP responses."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Iterable, Mapping, Optional, Sequence

from .formatting import _format_float


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_items(seq: Sequence[Any], limit: int = 5) -> Sequence[Any]:
    return list(seq[:limit]) if isinstance(seq, Sequence) else []


def format_coins_markets(coins: Sequence[Mapping[str, Any]]) -> Optional[str]:
    rows = [coin for coin in coins if isinstance(coin, Mapping)]
    if not rows:
        return "📈 Coingecko: No market data returned."

    lines = ["📈 Coingecko: Market Snapshot"]
    for coin in rows[:5]:
        name = coin.get("name") or coin.get("id") or "?"
        symbol = coin.get("symbol") or "?"
        price = _safe_float(coin.get("current_price"))
        change = _safe_float(coin.get("price_change_percentage_24h"))
        market_cap = _safe_float(coin.get("market_cap"))
        line = f"- {name} ({symbol.upper()}): ${_format_float(price)}"
        if isinstance(change, float):
            line += f" ({change:+.2f}% 24h)"
        if isinstance(market_cap, float):
            line += f", MC ${_format_float(market_cap)}"
        lines.append(line)

    if len(rows) > 5:
        lines.append(f"…and {len(rows) - 5} more coins.")

    return "\n".join(lines)


def format_top_gainers_losers(data: Mapping[str, Any]) -> Optional[str]:
    gainers = [coin for coin in data.get("top_gainers", []) if isinstance(coin, Mapping)]
    losers = [coin for coin in data.get("top_losers", []) if isinstance(coin, Mapping)]
    if not gainers and not losers:
        return "📈 Coingecko: No gainers or losers returned."

    lines = ["📈 Coingecko: Top Movers"]
    if gainers:
        lines.append("Top Gainers:")
        for coin in gainers[:5]:
            name = coin.get("name") or coin.get("id") or "?"
            change = _safe_float(coin.get("usd_24h_change") or coin.get("usd_change_24h"))
            price = _safe_float(coin.get("usd"))
            lines.append(
                f"  • {name}: ${_format_float(price)} ({change:+.2f}% 24h)"
                if isinstance(change, float)
                else f"  • {name}: ${_format_float(price)}"
            )
    if losers:
        lines.append("Top Losers:")
        for coin in losers[:5]:
            name = coin.get("name") or coin.get("id") or "?"
            change = _safe_float(coin.get("usd_24h_change") or coin.get("usd_change_24h"))
            price = _safe_float(coin.get("usd"))
            lines.append(
                f"  • {name}: ${_format_float(price)} ({change:+.2f}% 24h)"
                if isinstance(change, float)
                else f"  • {name}: ${_format_float(price)}"
            )
    return "\n".join(lines)


def format_simple_price(data: Mapping[str, Any]) -> Optional[str]:
    if not data:
        return "💲 Coingecko: No price data returned."
    lines = ["💲 Coingecko: Simple Prices"]
    for key, payload in list(data.items())[:10]:
        if not isinstance(payload, Mapping):
            continue
        prices = [f"{currency.upper()} {payload[currency]}" for currency in list(payload.keys())[:5]]
        if not prices:
            continue
        lines.append(f"- {key}: {', '.join(prices)}")
    return "\n".join(lines) if len(lines) > 1 else "💲 Coingecko: No price data returned."


def format_token_price(data: Mapping[str, Any]) -> Optional[str]:
    return format_simple_price(data)


def format_global(data: Mapping[str, Any]) -> Optional[str]:
    stats = data.get("data") if isinstance(data, Mapping) else None
    if not isinstance(stats, Mapping):
        return "🌐 Coingecko: Global data unavailable."
    total_market = stats.get("total_market_cap") or {}
    total_volume = stats.get("total_volume") or {}
    dominances = stats.get("market_cap_percentage") or {}
    lines = ["🌐 Coingecko: Global Snapshot"]
    btc_dom = _safe_float(dominances.get("btc"))
    eth_dom = _safe_float(dominances.get("eth"))
    market_usd = _safe_float(total_market.get("usd"))
    volume_usd = _safe_float(total_volume.get("usd"))
    if isinstance(market_usd, float):
        lines.append(f"- Total Market Cap: ${_format_float(market_usd)}")
    if isinstance(volume_usd, float):
        lines.append(f"- Total 24h Volume: ${_format_float(volume_usd)}")
    if isinstance(btc_dom, float) or isinstance(eth_dom, float):
        dom_parts = []
        if isinstance(btc_dom, float):
            dom_parts.append(f"BTC {btc_dom:.2f}%")
        if isinstance(eth_dom, float):
            dom_parts.append(f"ETH {eth_dom:.2f}%")
        lines.append("- Dominance: " + ", ".join(dom_parts))
    change = stats.get("market_cap_change_percentage_24h_usd")
    change_val = _safe_float(change)
    if isinstance(change_val, float):
        lines.append(f"- Market Cap Δ 24h: {change_val:+.2f}%")
    return "\n".join(lines)


def format_asset_platforms(platforms: Sequence[Mapping[str, Any]]) -> Optional[str]:
    entries = [p for p in platforms if isinstance(p, Mapping)]
    if not entries:
        return "🧱 Coingecko: No asset platforms returned."
    lines = ["🧱 Coingecko: Asset Platforms"]
    for platform in entries[:10]:
        name = platform.get("name") or platform.get("id") or "?"
        chain = platform.get("shortname") or platform.get("id")
        native = platform.get("native_coin_id") or platform.get("nativeCoinId")
        extra = f" (native: {native})" if native else ""
        lines.append(f"- {name} ({chain}){extra}")
    if len(entries) > 10:
        lines.append(f"…and {len(entries) - 10} more platforms.")
    return "\n".join(lines)


def format_search(data: Mapping[str, Any]) -> Optional[str]:
    if not isinstance(data, Mapping):
        return "🔍 Coingecko: No search results."
    coins = [item for item in data.get("coins", []) if isinstance(item, Mapping)]
    nfts = [item for item in data.get("nfts", []) if isinstance(item, Mapping)]
    lines = ["🔍 Coingecko: Search Results"]
    if coins:
        lines.append("Coins:")
        for coin in coins[:5]:
            name = coin.get("name") or coin.get("id")
            symbol = coin.get("symbol")
            market_cap_rank = coin.get("market_cap_rank")
            lines.append(
                f"  • {name} ({symbol})"
                + (f" — rank #{market_cap_rank}" if isinstance(market_cap_rank, int) else "")
            )
    if nfts:
        lines.append("NFTs:")
        for nft in nfts[:5]:
            lines.append(f"  • {nft.get('name')} ({nft.get('symbol')})")
    if len(lines) == 1:
        lines.append("No results found.")
    return "\n".join(lines)


def format_search_trending(data: Mapping[str, Any]) -> Optional[str]:
    if not isinstance(data, Mapping):
        return "🔥 Coingecko: No trending data."
    coins = [item for item in data.get("coins", []) if isinstance(item, Mapping)]
    lines = ["🔥 Coingecko: Trending Searches"]
    if coins:
        for coin in coins[:5]:
            item = coin.get("item") if isinstance(coin.get("item"), Mapping) else coin
            name = item.get("name") or item.get("id") or "?"
            symbol = item.get("symbol")
            market_cap_rank = item.get("market_cap_rank")
            lines.append(
                f"- {name} ({symbol})"
                + (f" — rank #{market_cap_rank}" if isinstance(market_cap_rank, int) else "")
            )
    else:
        lines.append("No trending coins found.")
    return "\n".join(lines)


def format_nft(data: Mapping[str, Any]) -> Optional[str]:
    if not isinstance(data, Mapping):
        return "🖼️ Coingecko: NFT data unavailable."
    name = data.get("name") or data.get("id") or "?"
    floor = _safe_float((data.get("floor_price") or {}).get("usd"))
    volume = _safe_float((data.get("volume_24h") or {}).get("usd"))
    lines = [f"🖼️ Coingecko NFT: {name}"]
    if isinstance(floor, float):
        lines.append(f"- Floor Price: ${_format_float(floor)}")
    if isinstance(volume, float):
        lines.append(f"- 24h Volume: ${_format_float(volume)}")
    total_supply = data.get("total_supply")
    if isinstance(total_supply, (int, float)):
        lines.append(f"- Total Supply: {int(total_supply)}")
    owners = data.get("number_of_unique_addresses")
    if isinstance(owners, (int, float)):
        lines.append(f"- Holders: {int(owners)}")
    return "\n".join(lines)


def format_coin_detail(data: Mapping[str, Any]) -> Optional[str]:
    if not isinstance(data, Mapping):
        return "🪙 Coingecko: Coin data unavailable."
    name = data.get("name") or data.get("id") or "?"
    symbol = data.get("symbol")
    market = data.get("market_data") if isinstance(data.get("market_data"), Mapping) else {}
    price = _safe_float((market.get("current_price") or {}).get("usd"))
    change = _safe_float(market.get("price_change_percentage_24h"))
    market_cap = _safe_float((market.get("market_cap") or {}).get("usd"))
    lines = [f"🪙 Coingecko: {name} ({(symbol or '').upper()})"]
    if isinstance(price, float):
        lines.append(f"- Price: ${_format_float(price)}")
    if isinstance(change, float):
        lines.append(f"- 24h Change: {change:+.2f}%")
    if isinstance(market_cap, float):
        lines.append(f"- Market Cap: ${_format_float(market_cap)}")
    rank = data.get("market_cap_rank") or market.get("market_cap_rank") if isinstance(market, Mapping) else None
    if isinstance(rank, int):
        lines.append(f"- Rank: #{rank}")
    return "\n".join(lines)


def format_coin_history(data: Mapping[str, Any]) -> Optional[str]:
    market = data.get("market_data") if isinstance(data.get("market_data"), Mapping) else {}
    price = _safe_float((market.get("current_price") or {}).get("usd"))
    cap = _safe_float((market.get("market_cap") or {}).get("usd"))
    volume = _safe_float((market.get("total_volume") or {}).get("usd"))
    lines = ["📜 Coingecko: Historical Snapshot"]
    if isinstance(price, float):
        lines.append(f"- Price: ${_format_float(price)}")
    if isinstance(cap, float):
        lines.append(f"- Market Cap: ${_format_float(cap)}")
    if isinstance(volume, float):
        lines.append(f"- Volume: ${_format_float(volume)}")
    return "\n".join(lines)


def format_token_holders(data: Mapping[str, Any]) -> Optional[str]:
    holders = data.get("data") if isinstance(data, Mapping) else None
    if isinstance(holders, Mapping):
        holders = holders.get("attributes")
    if isinstance(holders, Mapping):
        holders = holders.get("holders")
    if not isinstance(holders, Sequence):
        return None
    rows = [row for row in holders if isinstance(row, Mapping)]
    if not rows:
        return "👥 Coingecko: No holder data." 
    lines = ["👥 Coingecko: Top Holders"]
    for row in rows[:10]:
        address = row.get("address") or "?"
        percentage = row.get("percentage")
        amount = row.get("amount")
        line = f"- {address}"
        if amount:
            line += f" | Amount {amount}"
        if percentage:
            line += f" ({percentage}%)"
        lines.append(line)
    if len(rows) > 10:
        lines.append(f"…and {len(rows) - 10} more holders.")
    return "\n".join(lines)


def format_trades(data: Mapping[str, Any], *, heading: str) -> Optional[str]:
    rows = data.get("data") if isinstance(data, Mapping) else None
    if not isinstance(rows, Sequence):
        return None
    trades = [row for row in rows if isinstance(row, Mapping)]
    if not trades:
        return None
    lines = [heading]
    for trade in trades[:5]:
        attributes = trade.get("attributes") if isinstance(trade.get("attributes"), Mapping) else trade
        kind = attributes.get("kind") or attributes.get("type") or "trade"
        volume = _safe_float(attributes.get("volume_in_usd"))
        price = _safe_float(attributes.get("price_from_in_usd") or attributes.get("price_to_in_usd"))
        timestamp = format_timestamp(attributes.get("block_timestamp")) or attributes.get("block_timestamp")
        line = f"- {kind}" + (f" • ${_format_float(price)}" if isinstance(price, float) else "")
        if isinstance(volume, float):
            line += f" • Vol ${_format_float(volume)}"
        if timestamp:
            line += f" • {timestamp}"
        lines.append(line)
    if len(trades) > 5:
        lines.append(f"…and {len(trades) - 5} more trades.")
    return "\n".join(lines)


def format_list(entries: Sequence[Mapping[str, Any]], heading: str) -> Optional[str]:
    items = [entry for entry in entries if isinstance(entry, Mapping)]
    if not items:
        return None
    lines = [heading]
    for entry in items[:10]:
        name = entry.get("name") or entry.get("id") or entry.get("symbol") or "?"
        details = entry.get("symbol")
        suffix = f" ({details})" if details and details != name else ""
        lines.append(f"- {name}{suffix}")
    if len(items) > 10:
        lines.append(f"…and {len(items) - 10} more entries.")
    return "\n".join(lines)


def format_market_chart(data: Mapping[str, Any], *, heading: str) -> Optional[str]:
    if not isinstance(data, Mapping):
        return None
    prices = data.get("prices")
    if isinstance(prices, Sequence) and prices:
        start = prices[0][1] if isinstance(prices[0], Sequence) and len(prices[0]) > 1 else None
        end = prices[-1][1] if isinstance(prices[-1], Sequence) and len(prices[-1]) > 1 else None
    else:
        start = end = None
    lines = [heading]
    if start is not None and end is not None:
        lines.append(f"- Start: ${_format_float(float(start))}")
        lines.append(f"- End: ${_format_float(float(end))}")
    total_points = len(prices) if isinstance(prices, Sequence) else 0
    lines.append(f"- Data points: {total_points}")
    return "\n".join(lines)


def format_ohlc(data: Sequence[Any]) -> Optional[str]:
    if not data:
        return "📊 Coingecko: No OHLC data." 
    first = data[0] if isinstance(data[0], Sequence) else None
    last = data[-1] if isinstance(data[-1], Sequence) else None
    if not first or not last or len(first) < 5 or len(last) < 5:
        return "📊 Coingecko: OHLC data not recognised."
    lines = ["📊 Coingecko: OHLC"]
    lines.append(f"- Open: ${_format_float(float(first[1]))}")
    lines.append(f"- Close: ${_format_float(float(last[4]))}")
    high = max(float(c[2]) for c in data if isinstance(c, Sequence) and len(c) > 2)
    low = min(float(c[3]) for c in data if isinstance(c, Sequence) and len(c) > 3)
    lines.append(f"- High: ${_format_float(high)}")
    lines.append(f"- Low: ${_format_float(low)}")
    return "\n".join(lines)


def format_onchain_list(payload: Mapping[str, Any], *, heading: str) -> Optional[str]:
    data = payload.get("data")
    if not isinstance(data, Sequence):
        return None
    entries = [item for item in data if isinstance(item, Mapping)]
    if not entries:
        return None
    lines = [heading]
    for entry in entries[:5]:
        attributes = entry.get("attributes") if isinstance(entry.get("attributes"), Mapping) else {}
        name = attributes.get("name") or entry.get("id") or "?"
        reserve = attributes.get("reserve_in_usd") or attributes.get("reserveInUsd")
        volume = attributes.get("h24_volume_usd") or attributes.get("volume_usd") or {}
        volume_val = None
        if isinstance(volume, Mapping):
            volume_val = volume.get("h24") or volume.get("usd")
        else:
            volume_val = volume
        reserve_val = _safe_float(reserve)
        volume_val = _safe_float(volume_val)
        line = f"- {name}"
        if reserve_val is not None:
            line += f" | Liquidity ${_format_float(reserve_val)}"
        if volume_val is not None:
            line += f" | 24h Vol ${_format_float(volume_val)}"
        lines.append(line)
    if len(entries) > 5:
        lines.append(f"…and {len(entries) - 5} more entries.")
    return "\n".join(lines)


def format_generic_list(items: Sequence[Any], heading: str) -> Optional[str]:
    entries = [item for item in items if isinstance(item, Mapping)]
    if not entries:
        return None
    lines = [heading]
    for entry in entries[:5]:
        name = entry.get("name") or entry.get("id") or entry.get("symbol") or "?"
        lines.append(f"- {name}")
    if len(entries) > 5:
        lines.append(f"…and {len(entries) - 5} more entries.")
    return "\n".join(lines)


def format_timestamp(ts: Any) -> Optional[str]:
    value = _safe_int(ts)
    if value is None:
        return None
    if value > 1_000_000_000_000:
        value = value // 1000
    try:
        return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return None
