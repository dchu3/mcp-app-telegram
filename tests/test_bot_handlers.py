from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mcp_app_telegram.bot import (
    _handle_account,
    _handle_gas,
    _handle_help,
    _handle_text_query,
    _handle_unknown_command,
    TELEGRAM_COMMANDS,
)
from mcp_app_telegram.mcp_client import AccountSummary, EvmMcpClient, GasStats
from mcp_app_telegram.mcp.manager import McpClientRegistry


class StubEvmClient(EvmMcpClient):
    def __init__(self) -> None:
        # Bypass parent initialisation since tests stub methods directly.
        # pylint: disable=super-init-not-called
        self.fetch_account = AsyncMock()
        self.fetch_gas_stats = AsyncMock()


def build_bot_data(client: StubEvmClient) -> dict:
    registry = McpClientRegistry()
    registry.register("evm", client)  # type: ignore[arg-type]
    return {
        "mcp_registry": registry,
        "primary_evm_key": "evm",
        "network_client_map": {"base": "evm"},
        "primary_evm_network": "base",
    }



class DummyMessage:
    def __init__(self, text: str = "") -> None:
        self.replies = []
        self.text = text

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class DummyApplication:
    def __init__(self, bot_data):
        self.bot_data = bot_data


class DummyContext:
    def __init__(self, args, bot_data):
        self.args = args
        self.application = DummyApplication(bot_data)


@pytest.mark.asyncio
async def test_unknown_command_is_suppressed_for_known_alias():
    message = DummyMessage("/gas")
    update = SimpleNamespace(effective_message=message)
    context = DummyContext(args=[], bot_data={"known_commands": {"gas"}})

    await _handle_unknown_command(update, context)

    assert message.replies == []


@pytest.mark.asyncio
async def test_unknown_command_replies_for_unregistered():
    message = DummyMessage("/notreal")
    update = SimpleNamespace(effective_message=message)
    context = DummyContext(args=[], bot_data={"known_commands": {"gas", "help"}})

    await _handle_unknown_command(update, context)

    assert message.replies
    assert "Unknown command" in message.replies[0][0]


@pytest.mark.asyncio
async def test_handle_account_validates_address():
    message = DummyMessage()
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=1))
    context = DummyContext(args=["not-an-address"], bot_data={})

    await _handle_account(update, context)

    assert message.replies
    assert "42-character" in message.replies[0][0]


@pytest.mark.asyncio
async def test_handle_account_success():
    address = "0x" + "ab" * 20
    summary = AccountSummary(address=address.lower(), balance_wei=10**18, nonce=3, is_contract=False)

    message = DummyMessage()
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=1))
    mcp_client = StubEvmClient()
    mcp_client.fetch_account.return_value = summary
    context = DummyContext(args=[address], bot_data=build_bot_data(mcp_client))

    await _handle_account(update, context)

    mcp_client.fetch_account.assert_awaited_once_with(address.lower())
    assert message.replies
    assert "Account Summary" in message.replies[0][0]
    assert "1.0000 ETH" in message.replies[0][0]


@pytest.mark.asyncio
async def test_handle_gas_reports_stats():
    message = DummyMessage()
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=1))
    stats = GasStats(safe=0.5, standard=0.7, fast=1.1, block_lag_seconds=2.0, base_fee=0.6)
    mcp_client = StubEvmClient()
    mcp_client.fetch_gas_stats.return_value = stats
    context = DummyContext(args=[], bot_data=build_bot_data(mcp_client))

    await _handle_gas(update, context)

    mcp_client.fetch_gas_stats.assert_awaited_once()
    assert message.replies
    text, markup = message.replies[0]
    assert "Base Gas Snapshot" in text
    assert markup is not None


@pytest.mark.asyncio
async def test_handle_help_outputs_summary():
    message = DummyMessage()
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=1))
    context = DummyContext(args=[], bot_data={})

    await _handle_help(update, context)

    assert message.replies
    help_text, _ = message.replies[0]
    assert "Send a normal message" in help_text
    assert "/gas" in help_text
    assert "/account" in help_text
    assert "/transaction <hash> (alias /tx)" in help_text
    assert "\n- /cleargasalerts (alias /gas_clear)" in help_text
    assert "\n- /pairs" in help_text
    assert "\n- /sub <index|pair>" in help_text
    assert "\n- /unsub <index|pair>" in help_text
    assert "\n- /suball" in help_text
    assert "\n- /unsuball" in help_text


@pytest.mark.asyncio
async def test_handle_text_query_requires_agent():
    message = DummyMessage("What is gas?")
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=1))
    context = DummyContext(args=[], bot_data={})

    await _handle_text_query(update, context)

    assert message.replies
    assert "GEMINI_API_KEY" in message.replies[0][0]


@pytest.mark.asyncio
async def test_handle_text_query_success():
    message = DummyMessage("hello")
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=1))
    agent = SimpleNamespace(answer=AsyncMock(return_value="hi there"))
    bot_data = build_bot_data(StubEvmClient())
    bot_data["agent"] = agent
    context = DummyContext(args=[], bot_data=bot_data)

    await _handle_text_query(update, context)

    agent.answer.assert_awaited_once_with("hello")
    assert message.replies
    assert message.replies[0][0] == "hi there"


def test_commands_registered():
    names = {cmd.command for cmd in TELEGRAM_COMMANDS}
    assert {
        "help",
        "gas",
        "account",
        "transaction",
        "gasalert",
        "gasalertabove",
        "cleargasalerts",
        "gasalerts",
    }.issubset(names)
