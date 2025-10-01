"""Registry for managing multiple MCP client integrations."""

from __future__ import annotations

from typing import Dict, Iterable, Iterator, Mapping, MutableMapping, Optional, Tuple, Type, TypeVar

from .base import McpClient

TClient = TypeVar("TClient", bound=McpClient)


class McpClientRegistry:
    """Keeps track of MCP clients and coordinates lifecycle management."""

    def __init__(self) -> None:
        self._clients: MutableMapping[str, McpClient] = {}

    def register(self, key: str, client: McpClient) -> None:
        key = key.strip()
        if not key:
            raise ValueError("Registry key must not be empty")
        if key in self._clients:
            raise ValueError(f"An MCP client is already registered under key '{key}'")
        self._clients[key] = client

    def unregister(self, key: str) -> Optional[McpClient]:
        return self._clients.pop(key, None)

    def get(self, key: str) -> Optional[McpClient]:
        return self._clients.get(key)

    def require(self, key: str) -> McpClient:
        client = self.get(key)
        if client is None:
            raise KeyError(f"MCP client '{key}' is not registered")
        return client

    def require_typed(self, key: str, expected_type: Type[TClient]) -> TClient:
        client = self.require(key)
        if not isinstance(client, expected_type):
            raise TypeError(
                f"MCP client '{key}' is not of expected type {expected_type.__name__}; "
                f"got {type(client).__name__}"
            )
        return client

    def items(self) -> Iterable[Tuple[str, McpClient]]:
        return tuple(self._clients.items())

    def values(self) -> Iterable[McpClient]:
        return tuple(self._clients.values())

    def keys(self) -> Iterable[str]:
        return tuple(self._clients.keys())

    def __iter__(self) -> Iterator[str]:
        return iter(self._clients)

    async def start_all(self) -> None:
        for client in self._clients.values():
            await client.start()

    async def close_all(self) -> None:
        for client in self._clients.values():
            await client.close()

    def snapshot(self) -> Mapping[str, McpClient]:
        return dict(self._clients)
