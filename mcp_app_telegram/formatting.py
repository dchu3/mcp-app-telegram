"""Helpers for turning MCP data into Telegram-friendly text."""

from __future__ import annotations

from typing import Iterable

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
        f"Sequencer lag: {stats.block_lag_seconds:.1f} s",
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
