"""MCP client integrations and shared data structures."""

from .base import (
    AccountSummary,
    GasStats,
    McpClient,
    McpClientError,
    McpToolDefinition,
    ToolClient,
    TransactionSummary,
)
from .dexscreener_client import DexscreenerMcpClient
from .coingecko_client import CoingeckoMcpClient
from .evm_client import EvmMcpClient

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
