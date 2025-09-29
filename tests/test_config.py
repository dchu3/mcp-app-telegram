import os

import pytest

from mcp_app_telegram.config import ConfigError, DEFAULT_MCP_BASE_URL, load_config


def test_load_config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.delenv("MCP_EVM_BASE_URL", raising=False)
    monkeypatch.delenv("MCP_GAS_ALERT_THRESHOLD", raising=False)
    monkeypatch.delenv("ONCHAIN_VALIDATION_RPC_URL", raising=False)

    config = load_config()

    assert config.telegram_bot_token == "token"
    assert config.telegram_chat_id == 123
    assert config.mcp_base_url == DEFAULT_MCP_BASE_URL
    assert config.gas_alert_threshold is None
    assert config.telegram_read_timeout == pytest.approx(15.0)
    assert config.telegram_connect_timeout == pytest.approx(5.0)
    assert config.mcp_protocol == 'mcp'


def test_load_config_with_optional(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    monkeypatch.setenv("MCP_EVM_BASE_URL", "https://example.com/")
    monkeypatch.setenv("MCP_GAS_ALERT_THRESHOLD", "0.75")

    config = load_config()

    assert config.mcp_base_url == "https://example.com"
    assert config.gas_alert_threshold == pytest.approx(0.75)
    assert config.telegram_read_timeout == pytest.approx(15.0)
    assert config.telegram_connect_timeout == pytest.approx(5.0)
    assert config.mcp_protocol == 'mcp'


def test_load_config_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_MCP_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_with_timeouts(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "789")
    monkeypatch.setenv("TELEGRAM_HTTP_READ_TIMEOUT", "20")
    monkeypatch.setenv("TELEGRAM_HTTP_CONNECT_TIMEOUT", "7")
    monkeypatch.delenv("MCP_EVM_BASE_URL", raising=False)
    monkeypatch.delenv("ONCHAIN_VALIDATION_RPC_URL", raising=False)
    monkeypatch.delenv("MCP_EVM_PROTOCOL", raising=False)

    config = load_config()

    assert config.telegram_read_timeout == pytest.approx(20.0)
    assert config.telegram_connect_timeout == pytest.approx(7.0)
    assert config.mcp_protocol == 'mcp'



def test_load_config_json_rpc_via_onchain_env(monkeypatch):
    monkeypatch.setenv('TELEGRAM_MCP_BOT_TOKEN', 'token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '321')
    monkeypatch.delenv('MCP_EVM_BASE_URL', raising=False)
    monkeypatch.delenv('MCP_EVM_PROTOCOL', raising=False)
    monkeypatch.setenv('ONCHAIN_VALIDATION_RPC_URL', 'https://mainnet.base.org')

    config = load_config()

    assert config.mcp_protocol == 'json-rpc'
    assert config.mcp_base_url == 'https://mainnet.base.org'


def test_load_config_invalid_protocol(monkeypatch):
    monkeypatch.setenv('TELEGRAM_MCP_BOT_TOKEN', 'token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '654')
    monkeypatch.setenv('MCP_EVM_PROTOCOL', 'invalid')

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_explicit_protocol(monkeypatch):
    monkeypatch.setenv('TELEGRAM_MCP_BOT_TOKEN', 'token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '987')
    monkeypatch.setenv('MCP_EVM_BASE_URL', 'https://rpc.example')
    monkeypatch.setenv('MCP_EVM_PROTOCOL', 'json-rpc')

    config = load_config()

    assert config.mcp_protocol == 'json-rpc'
    assert config.mcp_base_url == 'https://rpc.example'

