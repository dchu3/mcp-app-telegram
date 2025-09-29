from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mcp_app_telegram.bot import _handle_account, _handle_gas
from mcp_app_telegram.bot import TELEGRAM_COMMANDS
from mcp_app_telegram.mcp_client import AccountSummary, GasStats



class DummyMessage:
    def __init__(self) -> None:
        self.replies = []

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
    mcp_client = SimpleNamespace(fetch_account=AsyncMock(return_value=summary))
    context = DummyContext(args=[address], bot_data={"mcp_client": mcp_client})

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
    mcp_client = SimpleNamespace(fetch_gas_stats=AsyncMock(return_value=stats))
    context = DummyContext(args=[], bot_data={"mcp_client": mcp_client})

    await _handle_gas(update, context)

    mcp_client.fetch_gas_stats.assert_awaited_once()
    assert message.replies
    text, markup = message.replies[0]
    assert "Base Gas Stats" in text
    assert markup is not None


def test_commands_registered():
    names = {cmd.command for cmd in TELEGRAM_COMMANDS}
    assert {"gas", "account", "tx", "gas_sub", "gas_sub_above", "gas_clear"}.issubset(names)
