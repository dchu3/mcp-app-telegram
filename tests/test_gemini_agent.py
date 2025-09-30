from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mcp_app_telegram.gemini_agent import GeminiAgent
from mcp_app_telegram.mcp_client import GasStats


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
