import json

import pytest
import httpx

from mcp_app_telegram.mcp_client import AccountSummary, EvmMcpClient, GasStats, McpClientError
from mcp_app_telegram.config import MCP_PROTOCOL_JSONRPC, MCP_PROTOCOL_MCP


class StubStdioClient:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    async def call_tool(self, name, arguments=None):  # pragma: no cover - exercised in tests
        key = (name, tuple(sorted((arguments or {}).items())))
        self.calls.append(key)
        response = self._responses.get(name)
        if callable(response):
            return response(arguments or {})
        return response

    async def close(self):  # pragma: no cover - exercised in tests
        return None


@pytest.mark.asyncio
async def test_fetch_gas_stats(monkeypatch):
    responses = {
        "get_latest_block": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "baseFeePerGas": "0x3b9aca00",
                            "timestamp": hex(1_700_000_000),
                        }
                    ),
                }
            ]
        }
    }
    stub = StubStdioClient(responses)

    async def fake_ensure(self):
        self._stdio_client = stub

    monkeypatch.setattr(EvmMcpClient, "_ensure_stdio", fake_ensure, raising=False)

    mcp = EvmMcpClient("http://unused", protocol=MCP_PROTOCOL_MCP)
    stats = await mcp.fetch_gas_stats()
    await mcp.close()

    assert isinstance(stats, GasStats)
    assert stats.base_fee == pytest.approx(1.0)
    assert stats.fast > stats.standard > stats.safe


@pytest.mark.asyncio
async def test_fetch_transaction_error(monkeypatch):
    responses = {
        "get_transaction": {"content": [], "isError": True}
    }
    stub = StubStdioClient(responses)

    async def fake_ensure(self):
        self._stdio_client = stub

    monkeypatch.setattr(EvmMcpClient, "_ensure_stdio", fake_ensure, raising=False)
    mcp = EvmMcpClient("http://unused", protocol=MCP_PROTOCOL_MCP)
    with pytest.raises(McpClientError):
        await mcp.fetch_transaction("0xabc")
    await mcp.close()


@pytest.mark.asyncio
async def test_fetch_gas_stats_jsonrpc():
    responses = iter([
        httpx.Response(200, json={"result": "0x3b9aca00"}),  # gas price 1 gwei
        httpx.Response(
            200,
            json={
                "result": {
                    "baseFeePerGas": "0x3b9aca00",
                    "timestamp": hex(1_700_000_000),
                }
            },
        ),
    ])

    def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://rpc") as client:
        mcp = EvmMcpClient("http://rpc", protocol=MCP_PROTOCOL_JSONRPC, client=client)
        stats = await mcp.fetch_gas_stats()
        await mcp.close()

    assert stats.standard == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_fetch_transaction_jsonrpc():
    responses = iter([
        httpx.Response(
            200,
            json={
                "result": {
                    "hash": "0xabc",
                    "from": "0xfrom",
                    "to": None,
                    "nonce": "0x1",
                    "value": "0x5",
                }
            },
        ),
        httpx.Response(
            200,
            json={"result": {"status": "0x1", "gasUsed": "0x5208"}},
        ),
    ])

    def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://rpc") as client:
        mcp = EvmMcpClient("http://rpc", protocol=MCP_PROTOCOL_JSONRPC, client=client)
        summary = await mcp.fetch_transaction("0xabc")
        await mcp.close()

    assert summary.status == "success"
    assert summary.gas_used == int("0x5208", 16)
    assert summary.nonce == 1
    assert summary.value_wei == 5


@pytest.mark.asyncio
async def test_fetch_account_mcp(monkeypatch):
    responses = {
        "get_balance": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "address": "0x123",
                            "wei": "12345",
                        }
                    ),
                }
            ]
        },
        "is_contract": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"isContract": True}),
                }
            ]
        },
        "get_chain_info": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"rpcUrl": "http://rpc.local"}),
                }
            ]
        },
    }
    stub = StubStdioClient(responses)

    async def fake_ensure(self):
        self._stdio_client = stub

    async def fake_nonce(self, address: str) -> int:  # noqa: ARG001 - test helper
        assert address == "0x123"
        return 7

    monkeypatch.setattr(EvmMcpClient, "_ensure_stdio", fake_ensure, raising=False)
    monkeypatch.setattr(EvmMcpClient, "_fetch_nonce_via_rpc", fake_nonce, raising=False)

    mcp = EvmMcpClient("http://unused", protocol=MCP_PROTOCOL_MCP)
    summary = await mcp.fetch_account("0x123")
    await mcp.close()

    assert isinstance(summary, AccountSummary)
    assert summary.balance_wei == 12345
    assert summary.nonce == 7
    assert summary.is_contract is True


@pytest.mark.asyncio
async def test_fetch_account_jsonrpc():
    responses = iter([
        httpx.Response(200, json={"result": "0x5"}),
        httpx.Response(200, json={"result": "0x2"}),
        httpx.Response(200, json={"result": "0x1"}),
    ])

    def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://rpc") as client:
        mcp = EvmMcpClient("http://rpc", protocol=MCP_PROTOCOL_JSONRPC, client=client)
        summary = await mcp.fetch_account("0x456")
        await mcp.close()

    assert summary.balance_wei == int("0x5", 16)
    assert summary.nonce == int("0x2", 16)
    assert summary.is_contract is True
