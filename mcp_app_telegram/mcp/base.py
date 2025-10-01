"""Base abstractions for MCP client integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence


class McpClientError(RuntimeError):
    """Raised when an MCP server returns an unexpected error payload."""


@dataclass(slots=True)
class GasStats:
    safe: float
    standard: float
    fast: float
    block_lag_seconds: float
    base_fee: float


@dataclass(slots=True)
class TransactionSummary:
    hash: str
    status: str
    from_address: str
    to_address: Optional[str]
    gas_used: Optional[int]
    nonce: Optional[int]
    value_wei: Optional[int]


@dataclass(slots=True)
class AccountSummary:
    address: str
    balance_wei: int
    nonce: int
    is_contract: bool


@dataclass(slots=True)
class McpToolDefinition:
    name: str
    description: str
    arguments: Dict[str, str]


class McpClient(ABC):
    """Common interface for long-lived MCP client connections."""

    @abstractmethod
    async def start(self) -> None:
        """Open connections or subprocesses needed for the client."""

    @abstractmethod
    async def close(self) -> None:
        """Shutdown any resources held by the client."""


class ToolClient(McpClient, ABC):
    """Specialised MCP client that exposes tools discoverable at runtime."""

    @property
    @abstractmethod
    def tools(self) -> Sequence[McpToolDefinition]:
        """Return all tool definitions exposed by the MCP server."""

    @abstractmethod
    async def call_tool(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        """Invoke a tool exposed by the server and return its raw response."""

    @abstractmethod
    def parse_tool_result(self, result: Any) -> Optional[Any]:
        """Best-effort JSON parsing for tool results."""
