import json

import pytest

from mcp_app_telegram.config import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MCP_BASE_URL,
    MCP_PROTOCOL_JSONRPC,
    MCP_PROTOCOL_MCP,
    ConfigError,
    load_config,
)


def _reset_legacy_env(monkeypatch):
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    monkeypatch.delenv("MCP_EVM_BASE_URL", raising=False)
    monkeypatch.delenv("MCP_EVM_PROTOCOL", raising=False)
    monkeypatch.delenv("MCP_EVM_NETWORK", raising=False)
    monkeypatch.delenv("ONCHAIN_VALIDATION_RPC_URL", raising=False)
    monkeypatch.delenv("MCP_EVM_SERVER_COMMAND", raising=False)
    monkeypatch.delenv("DEXSCREENER_MCP_ROOT", raising=False)
    monkeypatch.delenv("DEXSCREENER_MCP_COMMAND", raising=False)
    monkeypatch.delenv("COINGECKO_MCP_COMMAND", raising=False)
    monkeypatch.delenv("COINGECKO_PRO_API_KEY", raising=False)
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    monkeypatch.delenv("COINGECKO_ENVIRONMENT", raising=False)
    monkeypatch.delenv("MCP_PRIMARY_EVM", raising=False)
    monkeypatch.delenv("MCP_PRIMARY_DEXSCREENER", raising=False)
    monkeypatch.delenv("MCP_GAS_ALERT_THRESHOLD", raising=False)
    monkeypatch.delenv("TELEGRAM_HTTP_READ_TIMEOUT", raising=False)
    monkeypatch.delenv("TELEGRAM_HTTP_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    _reset_legacy_env(monkeypatch)
    yield
    _reset_legacy_env(monkeypatch)


def test_load_config_defaults(monkeypatch):
    config = load_config()

    assert config.telegram_bot_token == "token"
    assert config.telegram_chat_id == 123
    assert config.primary_evm_server == "evm"
    assert config.primary_dexscreener_server is None
    assert config.gas_alert_threshold is None
    assert config.gemini_model == DEFAULT_GEMINI_MODEL

    assert len(config.mcp_servers) == 1
    server = config.mcp_servers[0]
    assert server.key == "evm"
    assert server.kind == "evm"
    assert server.protocol == MCP_PROTOCOL_MCP
    assert server.base_url == DEFAULT_MCP_BASE_URL
    assert server.network == "base"


def test_load_config_with_optional(monkeypatch):
    monkeypatch.setenv("MCP_EVM_BASE_URL", "https://example.com/")
    monkeypatch.setenv("MCP_GAS_ALERT_THRESHOLD", "0.75")
    monkeypatch.setenv("TELEGRAM_HTTP_READ_TIMEOUT", "20")
    monkeypatch.setenv("TELEGRAM_HTTP_CONNECT_TIMEOUT", "7")
    monkeypatch.setenv("GEMINI_MODEL", "demo-model")

    config = load_config()

    assert config.gas_alert_threshold == pytest.approx(0.75)
    assert config.telegram_read_timeout == pytest.approx(20.0)
    assert config.telegram_connect_timeout == pytest.approx(7.0)
    assert config.gemini_model == "demo-model"

    server = config.mcp_servers[0]
    assert server.base_url == "https://example.com"
    assert server.protocol == MCP_PROTOCOL_MCP


def test_load_config_json_rpc_via_onchain_env(monkeypatch):
    monkeypatch.setenv("ONCHAIN_VALIDATION_RPC_URL", "https://mainnet.base.org")

    config = load_config()

    server = config.mcp_servers[0]
    assert server.protocol == MCP_PROTOCOL_JSONRPC
    assert server.base_url == "https://mainnet.base.org"
    assert server.rpc_urls["base"] == "https://mainnet.base.org"


def test_load_config_invalid_protocol(monkeypatch):
    monkeypatch.setenv("MCP_EVM_PROTOCOL", "invalid")
    with pytest.raises(ConfigError):
        load_config()


def test_load_config_explicit_protocol(monkeypatch):
    monkeypatch.setenv("MCP_EVM_BASE_URL", "https://rpc.example")
    monkeypatch.setenv("MCP_EVM_PROTOCOL", MCP_PROTOCOL_JSONRPC)

    config = load_config()

    server = config.mcp_servers[0]
    assert server.protocol == MCP_PROTOCOL_JSONRPC
    assert server.base_url == "https://rpc.example"


def test_load_config_with_dexscreener_root(monkeypatch):
    monkeypatch.setenv("DEXSCREENER_MCP_ROOT", "/opt/drawer")

    config = load_config()

    assert config.primary_dexscreener_server == "dexscreener"
    kinds = {srv.kind for srv in config.mcp_servers}
    assert "dexscreener" in kinds
    dex = next(srv for srv in config.mcp_servers if srv.kind == "dexscreener")
    assert dex.server_command == ("node", "/opt/drawer/index.js")


def test_load_config_with_dexscreener_command(monkeypatch):
    monkeypatch.setenv("DEXSCREENER_MCP_COMMAND", "node custom/index.js --port 1234")

    config = load_config()

    dex = next(srv for srv in config.mcp_servers if srv.kind == "dexscreener")
    assert dex.server_command == ("node", "custom/index.js", "--port", "1234")


def test_load_config_with_coingecko(monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "abc123")
    config = load_config()

    coingecko = next(srv for srv in config.mcp_servers if srv.kind == "coingecko")
    assert coingecko.server_command == ("npx", "-y", "@coingecko/coingecko-mcp")
    assert coingecko.env.get("COINGECKO_PRO_API_KEY") == "abc123"
    assert coingecko.env.get("COINGECKO_ENVIRONMENT") == "pro"


def test_load_config_from_json(monkeypatch):
    servers = [
        {
            "key": "evm-mainnet",
            "kind": "evm",
            "protocol": "json-rpc",
            "base_url": "https://rpc.ankr.com/eth",
            "network": "ethereum",
            "rpc_urls": {"ethereum": "https://rpc.ankr.com/eth"},
        },
        {
            "key": "dexscreener-main",
            "kind": "dexscreener",
            "command": ["node", "dex/index.js"],
        },
        {
            "key": "coingecko-local",
            "kind": "coingecko",
            "command": ["node", "cg/index.js"],
            "env": {"COINGECKO_PRO_API_KEY": "abc"},
        },
    ]
    monkeypatch.setenv("MCP_SERVERS", json.dumps(servers))
    monkeypatch.setenv("MCP_PRIMARY_EVM", "evm-mainnet")
    monkeypatch.setenv("MCP_PRIMARY_DEXSCREENER", "dexscreener-main")

    config = load_config()

    assert config.primary_evm_server == "evm-mainnet"
    assert config.primary_dexscreener_server == "dexscreener-main"
    assert len(config.mcp_servers) == 3
    rpc_server = next(srv for srv in config.mcp_servers if srv.key == "evm-mainnet")
    assert rpc_server.protocol == MCP_PROTOCOL_JSONRPC
    assert rpc_server.rpc_urls["ethereum"] == "https://rpc.ankr.com/eth"
