"""Configuration loading for the Telegram MCP application."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Final, Optional, Tuple

MCP_PROTOCOL_MCP = "mcp"
MCP_PROTOCOL_JSONRPC = "json-rpc"

DEFAULT_MCP_BASE_URL: Final[str] = "http://localhost:8080"
DEFAULT_GEMINI_MODEL: Final[str] = "gemini-1.5-flash-latest"


class ConfigError(RuntimeError):
    """Raised when required environment configuration is missing."""


@dataclass(slots=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: int
    mcp_base_url: str = DEFAULT_MCP_BASE_URL
    gas_alert_threshold: Optional[float] = None
    telegram_read_timeout: float = 15.0
    telegram_connect_timeout: float = 5.0
    mcp_protocol: str = MCP_PROTOCOL_MCP
    mcp_server_command: Optional[Tuple[str, ...]] = None
    mcp_network: str = "base"
    gemini_api_key: Optional[str] = None
    gemini_model: str = DEFAULT_GEMINI_MODEL
    dexscreener_mcp_command: Optional[Tuple[str, ...]] = None


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ConfigError(f"Environment variable '{key}' must be set")
    return value


def load_config() -> Config:
    token = _require_env("TELEGRAM_MCP_BOT_TOKEN")
    chat_id_raw = _require_env("TELEGRAM_CHAT_ID")
    try:
        chat_id = int(chat_id_raw)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise ConfigError("TELEGRAM_CHAT_ID must be an integer") from exc

    base_url = (
        os.getenv("MCP_EVM_BASE_URL")
        or os.getenv("ONCHAIN_VALIDATION_RPC_URL")
        or DEFAULT_MCP_BASE_URL
    )
    threshold_raw = os.getenv("MCP_GAS_ALERT_THRESHOLD")
    threshold = float(threshold_raw) if threshold_raw else None

    protocol = os.getenv("MCP_EVM_PROTOCOL")
    if protocol not in {None, MCP_PROTOCOL_MCP, MCP_PROTOCOL_JSONRPC}:
        raise ConfigError("MCP_EVM_PROTOCOL must be 'mcp' or 'json-rpc'")

    if protocol is None and os.getenv("MCP_EVM_BASE_URL") is None and os.getenv("ONCHAIN_VALIDATION_RPC_URL"):
        protocol = MCP_PROTOCOL_JSONRPC

    command_env = os.getenv("MCP_EVM_SERVER_COMMAND")
    command: Optional[Tuple[str, ...]] = None
    if command_env:
        parts = tuple(shlex.split(command_env))
        if not parts:
            raise ConfigError("MCP_EVM_SERVER_COMMAND must not be empty")
        command = parts

    network = os.getenv("MCP_EVM_NETWORK", "base").strip() or "base"
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL

    dexscreener_command: Optional[Tuple[str, ...]] = None
    dexscreener_command_env = os.getenv("DEXSCREENER_MCP_COMMAND")
    dexscreener_root = os.getenv("DEXSCREENER_MCP_ROOT")
    if dexscreener_command_env:
        parts = tuple(shlex.split(dexscreener_command_env))
        if not parts:
            raise ConfigError("DEXSCREENER_MCP_COMMAND must not be empty when set")
        dexscreener_command = parts
    elif dexscreener_root:
        entry = f"{dexscreener_root.rstrip('/')}/index.js"
        dexscreener_command = ("node", entry)

    return Config(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        mcp_base_url=base_url.rstrip("/") or DEFAULT_MCP_BASE_URL,
        gas_alert_threshold=threshold,
        telegram_read_timeout=float(os.getenv("TELEGRAM_HTTP_READ_TIMEOUT", 15.0)),
        telegram_connect_timeout=float(os.getenv("TELEGRAM_HTTP_CONNECT_TIMEOUT", 5.0)),
        mcp_protocol=protocol or MCP_PROTOCOL_MCP,
        mcp_server_command=command,
        mcp_network=network,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        dexscreener_mcp_command=dexscreener_command,
    )
