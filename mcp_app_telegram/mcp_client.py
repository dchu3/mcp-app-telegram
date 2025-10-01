"""Backward-compatible re-exports for MCP client classes.

This module will be removed once downstream imports migrate to ``mcp_app_telegram.mcp``.
"""

from __future__ import annotations

from .mcp import (
    AccountSummary,
    CoingeckoMcpClient,
    DexscreenerMcpClient,
    EvmMcpClient,
    GasStats,
    McpClient,
    McpClientError,
    McpToolDefinition,
    ToolClient,
    TransactionSummary,
)

__all__ = [
    "AccountSummary",
    "DexscreenerMcpClient",
    "CoingeckoMcpClient",
    "EvmMcpClient",
    "GasStats",
    "McpClient",
    "McpClientError",
    "McpToolDefinition",
    "ToolClient",
    "TransactionSummary",
]
