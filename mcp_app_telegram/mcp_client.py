"""Async client for interacting with MCP or JSON-RPC EVM endpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

import httpx

from .config import MCP_PROTOCOL_JSONRPC, MCP_PROTOCOL_MCP
from .mcp_stdio import McpStdioClient, McpStdioError


class McpClientError(RuntimeError):
    """Raised when the MCP server returns an error payload."""


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


class EvmMcpClient:
    """Thin wrapper over HTTP requests to an MCP or JSON-RPC endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        protocol: str = MCP_PROTOCOL_MCP,
        client: Optional[httpx.AsyncClient] = None,
        command: Optional[Sequence[str]] = None,
        network: str = "base",
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._endpoint = "/invoke" if protocol == MCP_PROTOCOL_MCP else ""
        self._protocol = protocol
        self._rpc_id = 0
        self._network = network
        self._stdio_client: Optional[McpStdioClient] = None
        self._stdio_command = tuple(command) if command else ("npx", "-y", "@mcpdotdirect/evm-mcp-server")

    async def close(self) -> None:
        if self._protocol == MCP_PROTOCOL_MCP and self._stdio_client is not None:
            await self._stdio_client.close()
            self._stdio_client = None
        if self._owns_client:
            await self._client.aclose()

    async def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._protocol != MCP_PROTOCOL_MCP:
            raise McpClientError("call() is only available when using the MCP protocol")
        await self._ensure_stdio()
        if self._stdio_client is None:
            raise McpClientError("MCP stdio client is not available")
        result = await self._stdio_client.call_tool(method, params or {})
        return result

    async def fetch_gas_stats(self) -> GasStats:
        if self._protocol == MCP_PROTOCOL_JSONRPC:
            return await self._fetch_gas_stats_jsonrpc()
        return await self._fetch_gas_stats_mcp()

    async def fetch_transaction(self, tx_hash: str) -> TransactionSummary:
        if self._protocol == MCP_PROTOCOL_JSONRPC:
            return await self._fetch_transaction_jsonrpc(tx_hash)
        return await self._fetch_transaction_mcp(tx_hash)

    async def fetch_account(self, address: str) -> AccountSummary:
        if self._protocol == MCP_PROTOCOL_JSONRPC:
            return await self._fetch_account_jsonrpc(address)
        return await self._fetch_account_mcp(address)

    async def start(self) -> None:
        if self._protocol != MCP_PROTOCOL_MCP:
            return
        await self._ensure_stdio()

    async def _json_rpc(self, method: str, params: Optional[List[Any]] = None) -> Any:
        self._rpc_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": self._rpc_id,
        }
        response = await self._client.post(self._endpoint, json=payload)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise McpClientError(str(data["error"]))
        return data.get("result")

    async def _fetch_gas_stats_jsonrpc(self) -> GasStats:
        gas_price_hex = await self._json_rpc("eth_gasPrice")
        if gas_price_hex is None:
            raise McpClientError("eth_gasPrice returned no result")
        gas_price_wei = int(gas_price_hex, 16)

        block = await self._json_rpc("eth_getBlockByNumber", ["latest", False])
        if block is None:
            raise McpClientError("Failed to load latest block")

        base_fee_hex = block.get("baseFeePerGas") or "0x0"
        base_fee_wei = int(base_fee_hex, 16)
        block_timestamp_hex = block.get("timestamp") or "0x0"
        block_timestamp = int(block_timestamp_hex, 16)

        current_time = time()
        block_lag = max(0.0, current_time - block_timestamp)

        gas_price_gwei = gas_price_wei / 1_000_000_000
        base_fee_gwei = base_fee_wei / 1_000_000_000
        safe = max(base_fee_gwei, gas_price_gwei * 0.9)
        standard = gas_price_gwei
        fast = gas_price_gwei * 1.1

        return GasStats(
            safe=safe,
            standard=standard,
            fast=fast,
            block_lag_seconds=block_lag,
            base_fee=base_fee_gwei,
        )

    async def _fetch_transaction_jsonrpc(self, tx_hash: str) -> TransactionSummary:
        tx = await self._json_rpc("eth_getTransactionByHash", [tx_hash])
        receipt = await self._json_rpc("eth_getTransactionReceipt", [tx_hash])

        if tx is None:
            raise McpClientError(f"Transaction {tx_hash} not found")

        status = "pending"
        gas_used = None
        if isinstance(receipt, dict):
            status_hex = receipt.get("status")
            if status_hex is not None:
                status = "success" if int(status_hex, 16) == 1 else "failed"
            gas_used_hex = receipt.get("gasUsed")
            if gas_used_hex is not None:
                gas_used = int(gas_used_hex, 16)

        nonce = int(tx.get("nonce", "0x0"), 16) if tx.get("nonce") is not None else None
        value_hex = tx.get("value")
        value_wei = int(value_hex, 16) if value_hex else None

        return TransactionSummary(
            hash=tx.get("hash", tx_hash),
            status=status,
            from_address=tx.get("from"),
            to_address=tx.get("to"),
            gas_used=gas_used,
            nonce=nonce,
            value_wei=value_wei,
        )

    async def _fetch_account_jsonrpc(self, address: str) -> AccountSummary:
        balance_hex = await self._json_rpc("eth_getBalance", [address, "latest"])
        if balance_hex is None:
            raise McpClientError("eth_getBalance returned no result")
        nonce_hex = await self._json_rpc("eth_getTransactionCount", [address, "latest"])
        code_hex = await self._json_rpc("eth_getCode", [address, "latest"])

        balance = int(balance_hex, 16)
        nonce = int(nonce_hex, 16) if nonce_hex is not None else 0
        is_contract = bool(code_hex and code_hex != "0x" and int(code_hex, 16) != 0)

        return AccountSummary(
            address=address,
            balance_wei=balance,
            nonce=nonce,
            is_contract=is_contract,
        )

    async def _ensure_stdio(self) -> None:
        if self._protocol != MCP_PROTOCOL_MCP:
            return
        if self._stdio_client is not None:
            return
        client = McpStdioClient(self._stdio_command)
        await client.start()
        self._stdio_client = client

    async def _call_tool_json(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        await self._ensure_stdio()
        if self._stdio_client is None:
            raise McpClientError("MCP stdio client not running")
        try:
            result = await self._stdio_client.call_tool(name, arguments or {})
        except McpStdioError as exc:
            raise McpClientError(str(exc)) from exc
        if result.get("isError"):
            raise McpClientError(f"MCP tool {name} reported error")
        content = result.get("content")
        if not isinstance(content, list):
            tool_result = result.get("toolResult")
            if isinstance(tool_result, Mapping):
                return dict(tool_result)
            raise McpClientError(f"Invalid MCP tool response from {name}: {result!r}")
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                text = item.get("text", "")
                if not isinstance(text, str):
                    continue
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    continue
        raise McpClientError(f"Tool {name} did not return JSON content")

    async def _fetch_gas_stats_mcp(self) -> GasStats:
        block = await self._call_tool_json(
            "get_latest_block",
            {"network": self._network},
        )
        base_fee_hex = str(block.get("baseFeePerGas") or "0x0")
        base_fee_wei = int(base_fee_hex, 16)
        base_fee_gwei = base_fee_wei / 1_000_000_000 if base_fee_wei else 0.0

        timestamp_hex = str(block.get("timestamp") or "0x0")
        try:
            block_timestamp = int(timestamp_hex, 16)
        except ValueError:
            block_timestamp = 0
        block_lag = max(0.0, time() - block_timestamp) if block_timestamp else 0.0

        # Heuristic tiers derived from the latest base fee.
        safe = max(base_fee_gwei * 1.05, base_fee_gwei)
        standard = max(base_fee_gwei * 1.15, base_fee_gwei)
        fast = max(base_fee_gwei * 1.25, base_fee_gwei)

        return GasStats(
            safe=safe,
            standard=standard,
            fast=fast,
            block_lag_seconds=block_lag,
            base_fee=base_fee_gwei,
        )

    async def _fetch_transaction_mcp(self, tx_hash: str) -> TransactionSummary:
        tx = await self._call_tool_json(
            "get_transaction",
            {"txHash": tx_hash, "network": self._network},
        )

        receipt = await self._call_tool_json(
            "get_transaction_receipt",
            {"txHash": tx_hash, "network": self._network},
        )

        status_hex = str(receipt.get("status") or "0x0")
        status = "success" if int(status_hex, 16) == 1 else "failed"
        gas_used = receipt.get("gasUsed")
        gas_used_int = int(gas_used, 16) if isinstance(gas_used, str) else None

        nonce_hex = tx.get("nonce")
        nonce = int(nonce_hex, 16) if isinstance(nonce_hex, str) else None

        value_hex = tx.get("value")
        value_wei = int(value_hex, 16) if isinstance(value_hex, str) else None

        return TransactionSummary(
            hash=tx.get("hash", tx_hash),
            status=status,
            from_address=tx.get("from"),
            to_address=tx.get("to"),
            gas_used=gas_used_int,
            nonce=nonce,
            value_wei=value_wei,
        )

    async def _fetch_account_mcp(self, address: str) -> AccountSummary:
        balance = await self._call_tool_json(
            "get_balance",
            {"address": address, "network": self._network},
        )
        is_contract_info = await self._call_tool_json(
            "is_contract",
            {"address": address, "network": self._network},
        )

        balance_wei = int(balance.get("wei", 0)) if isinstance(balance.get("wei"), str) else int(balance.get("wei", 0) or 0)

        nonce = await self._fetch_nonce_via_rpc(address)

        return AccountSummary(
            address=balance.get("address", address),
            balance_wei=balance_wei,
            nonce=nonce,
            is_contract=bool(is_contract_info.get("isContract")),
        )

    async def _fetch_nonce_via_rpc(self, address: str) -> int:
        try:
            info = await self._call_tool_json("get_chain_info", {"network": self._network})
        except McpClientError:
            info = {}
        rpc_url = info.get("rpcUrl") if isinstance(info, Mapping) else None
        if not rpc_url:
            return 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getTransactionCount",
                "params": [address, "latest"],
                "id": 1,
            }
            response = await client.post(str(rpc_url), json=payload)
            response.raise_for_status()
            data = response.json()
            result = data.get("result")
            if isinstance(result, str):
                return int(result, 16)
        return 0
