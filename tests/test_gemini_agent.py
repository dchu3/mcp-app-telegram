import json
from unittest.mock import AsyncMock

import pytest

from mcp_app_telegram.gemini_agent import (
    GeminiAgent,
    ToolDefinition,
    build_dexscreener_tool_definitions,
    build_coingecko_tool_definitions,
)
from mcp_app_telegram.mcp_client import CoingeckoMcpClient, EvmMcpClient, GasStats, McpToolDefinition
from mcp_app_telegram.mcp.manager import McpClientRegistry


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def generate_json(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise RuntimeError("No more fake responses queued")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_agent_runs_gas_tool():
    gas_stats = GasStats(safe=0.5, standard=0.7, fast=1.1, block_lag_seconds=2.0, base_fee=0.6)
    client = StubEvmClient()
    client.fetch_gas_stats.return_value = gas_stats
    llm = FakeLLM([
        '{"tool": "gas_stats", "arguments": {}, "reply": "Here are the latest gas metrics."}'
    ])

    agent = build_agent(client, llm)
    answer = await agent.answer("What are gas fees right now?")

    client.fetch_gas_stats.assert_awaited_once()
    assert "Here are the latest gas metrics." in answer
    assert "Gas Snapshot" in answer


@pytest.mark.asyncio
async def test_agent_handles_direct_reply_only():
    client = StubEvmClient()
    llm = FakeLLM([
        '{"tool": null, "arguments": {}, "reply": "I don\'t need a tool for that."}'
    ])

    agent = build_agent(client, llm)
    answer = await agent.answer("Say hi")

    assert answer == "I don't need a tool for that."
    client.fetch_gas_stats.assert_not_called()


@pytest.mark.asyncio
async def test_agent_reports_when_tool_arguments_missing():
    client = StubEvmClient()
    llm = FakeLLM([
        '{"tool": "account_overview", "arguments": {}, "reply": "Let me check that."}'
    ])

    agent = build_agent(client, llm)
    answer = await agent.answer("What about 0x?")

    assert "Let me check that." in answer
    assert "couldn't retrieve" in answer


@pytest.mark.asyncio
async def test_agent_supports_added_tools():
    client = StubEvmClient()
    llm = FakeLLM([
        '{"tool": "dex_tool", "arguments": {"symbol": "WETH"}, "reply": "Fetching pair data."}'
    ])

    async def fake_handler(args):
        assert args == {"symbol": "WETH"}
        return "Dex output"

    agent = build_agent(client, llm)
    agent.extend_tools(
        [
            ToolDefinition(
                name="dex_tool",
                description="Example dex tool",
                arguments={"symbol": "Token symbol"},
                handler=fake_handler,
            )
        ]
    )

    answer = await agent.answer("Find a pair")

    assert "Fetching pair data." in answer
    assert "Dex output" in answer


@pytest.mark.asyncio
async def test_build_dexscreener_tool_definitions_runs_handler():
    captured = {}

    class FakeDexClient:
        def __init__(self) -> None:
            self.tools = (
                McpToolDefinition(
                    name="dexscreener__searchPairs",
                    description="Search pairs",
                    arguments={"query": "Search term"},
                ),
            )

        async def call_tool(self, name, arguments):
            captured["name"] = name
            captured["arguments"] = arguments
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "pairs": [
                                    {
                                        "baseToken": {"symbol": "AVNT"},
                                        "quoteToken": {"symbol": "WETH"},
                                        "priceUsd": 1.23,
                                        "volume": {"h24": 4567.89},
                                        "liquidity": {"usd": 7890.12},
                                        "chainId": "base",
                                        "dexId": "uniswap",
                                        "url": "https://dexscreener.com/base/example",
                                    }
                                ]
                            }
                        ),
                    }
                ],
            }

        def parse_tool_result(self, result):
            content = result.get("content")
            if isinstance(content, list) and content:
                payload = content[0]
                if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                    return json.loads(payload["text"])
            return None

    client = FakeDexClient()
    definitions = build_dexscreener_tool_definitions(client)

    assert len(definitions) == 1
    handler = definitions[0].handler
    result = await handler({"query": "WETH"})

    assert captured == {"name": "dexscreener__searchPairs", "arguments": {"query": "WETH"}}
    assert "Dexscreener:" in result
    assert "AVNT/WETH" in result


@pytest.mark.asyncio
async def test_build_coingecko_tool_definitions_runs_handler():
    class FakeCoingeckoClient:
        def __init__(self) -> None:
            self.tools = (
                McpToolDefinition(
                    name="get_coins_markets",
                    description="Markets",
                    arguments={},
                ),
            )

        async def call_tool(self, name, arguments):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            [
                                {
                                    "name": "Bitcoin",
                                    "symbol": "btc",
                                    "current_price": 68000,
                                    "price_change_percentage_24h": 2.1,
                                    "market_cap": 1_200_000_000_000,
                                }
                            ]
                        ),
                    }
                ]
            }

        def parse_tool_result(self, result):
            return None

    client = FakeCoingeckoClient()
    definitions = build_coingecko_tool_definitions(client)  # type: ignore[arg-type]
    handler = definitions[0].handler
    output = await handler({})
    assert "Coingecko" in output
    assert "Bitcoin" in output


@pytest.mark.asyncio
async def test_persona_in_prompt():
    gas_stats = GasStats(safe=0.5, standard=0.7, fast=1.1, block_lag_seconds=2.0, base_fee=0.6)
    client = StubEvmClient()
    client.fetch_gas_stats.return_value = gas_stats
    llm = FakeLLM(['{"tool": null, "arguments": {}, "reply": "hi"}'])
    persona = "You are a friendly Base network analyst."
    agent = build_agent(client, llm, persona=persona)

    await agent.answer("hello")

    assert any(persona in prompt for prompt in llm.prompts)


def test_format_dexscreener_pairs_handles_empty():
    from mcp_app_telegram.formatting import format_dexscreener_pairs

    message = format_dexscreener_pairs({"pairs": []})
    assert message == "📊 Dexscreener: No matching pairs returned."


def test_format_dexscreener_pairs_selects_top_volume():
    from mcp_app_telegram.formatting import format_dexscreener_pairs

    message = format_dexscreener_pairs(
        {
            "pairs": [
                {
                    "baseToken": {"symbol": "AVNT"},
                    "quoteToken": {"symbol": "USDC"},
                    "volume": {"h24": 10},
                    "priceUsd": 1,
                    "liquidity": {"usd": 100},
                    "chainId": "base",
                    "dexId": "wap",
                    "url": "https://a",
                },
                {
                    "baseToken": {"symbol": "AVNT"},
                    "quoteToken": {"symbol": "USDC"},
                    "volume": {"h24": 100},
                    "priceUsd": 1.2,
                    "liquidity": {"usd": 200},
                    "chainId": "base",
                    "dexId": "wap2",
                    "url": "https://b",
                },
            ]
        }
    )

    assert "wap2" in message


@pytest.mark.asyncio
async def test_dexscreener_handler_unparseable_payload():
    class FakeDexClient:
        def __init__(self) -> None:
            self.tools = (
                McpToolDefinition(
                    name="dexscreener__searchPairs",
                    description="Search pairs",
                    arguments={},
                ),
            )

        async def call_tool(self, name, arguments):
            return {"unexpected": "value"}

        def parse_tool_result(self, result):
            return None

    client = FakeDexClient()
    definitions = build_dexscreener_tool_definitions(client)
    handler = definitions[0].handler

    response = await handler({})

    assert "dexscreener__searchPairs result" in response
    assert "```json" in response
class StubEvmClient(EvmMcpClient):
    def __init__(self) -> None:
        # Bypass parent initialisation.
        # pylint: disable=super-init-not-called
        self.fetch_gas_stats = AsyncMock()
        self.fetch_account = AsyncMock()
        self.fetch_transaction = AsyncMock()


def build_agent(client: StubEvmClient, llm: FakeLLM, *, persona: str | None = None) -> GeminiAgent:
    registry = McpClientRegistry()
    registry.register("evm", client)
    return GeminiAgent(registry, "evm", llm=llm, persona=persona or "")
