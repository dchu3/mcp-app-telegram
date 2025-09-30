import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mcp_app_telegram.gemini_agent import (
    GeminiAgent,
    ToolDefinition,
    build_dexscreener_tool_definitions,
)
from mcp_app_telegram.mcp_client import GasStats, McpToolDefinition


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
    client = SimpleNamespace(
        fetch_gas_stats=AsyncMock(return_value=gas_stats),
        fetch_account=AsyncMock(),
        fetch_transaction=AsyncMock(),
    )
    llm = FakeLLM([
        '{"tool": "gas_stats", "arguments": {}, "reply": "Here are the latest gas metrics."}'
    ])

    agent = GeminiAgent(client, llm=llm)
    answer = await agent.answer("What are gas fees right now?")

    client.fetch_gas_stats.assert_awaited_once()
    assert "Here are the latest gas metrics." in answer
    assert "Base Gas Stats" in answer


@pytest.mark.asyncio
async def test_agent_handles_direct_reply_only():
    client = SimpleNamespace(
        fetch_gas_stats=AsyncMock(),
        fetch_account=AsyncMock(),
        fetch_transaction=AsyncMock(),
    )
    llm = FakeLLM([
        '{"tool": null, "arguments": {}, "reply": "I don\'t need a tool for that."}'
    ])

    agent = GeminiAgent(client, llm=llm)
    answer = await agent.answer("Say hi")

    assert answer == "I don't need a tool for that."
    client.fetch_gas_stats.assert_not_called()


@pytest.mark.asyncio
async def test_agent_reports_when_tool_arguments_missing():
    client = SimpleNamespace(
        fetch_gas_stats=AsyncMock(),
        fetch_account=AsyncMock(),
        fetch_transaction=AsyncMock(),
    )
    llm = FakeLLM([
        '{"tool": "account_overview", "arguments": {}, "reply": "Let me check that."}'
    ])

    agent = GeminiAgent(client, llm=llm)
    answer = await agent.answer("What about 0x?")

    assert "Let me check that." in answer
    assert "couldn't retrieve" in answer


@pytest.mark.asyncio
async def test_agent_supports_added_tools():
    client = SimpleNamespace(
        fetch_gas_stats=AsyncMock(),
        fetch_account=AsyncMock(),
        fetch_transaction=AsyncMock(),
    )
    llm = FakeLLM([
        '{"tool": "dex_tool", "arguments": {"symbol": "WETH"}, "reply": "Fetching pair data."}'
    ])

    async def fake_handler(args):
        assert args == {"symbol": "WETH"}
        return "Dex output"

    agent = GeminiAgent(client, llm=llm)
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

    assert "couldn't summarise" in response
