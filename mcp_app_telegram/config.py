"""Configuration loading for the Telegram MCP application."""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from typing import Dict, Final, Iterable, Mapping, Optional, Sequence, Tuple

MCP_PROTOCOL_MCP = "mcp"
MCP_PROTOCOL_JSONRPC = "json-rpc"

DEFAULT_MCP_BASE_URL: Final[str] = "http://localhost:8080"
DEFAULT_GEMINI_MODEL: Final[str] = "gemini-1.5-flash-latest"

# A Base RPC endpoint is required for on-chain validation
ONCHAIN_VALIDATION_RPC_URL = os.environ.get("ONCHAIN_VALIDATION_RPC_URL", "https://mainnet.base.org")

# The path to the SQLite database file
DATABASE_FILE = os.environ.get("DATABASE_FILE", "gas_alerts.db")


class ConfigError(RuntimeError):
    """Raised when required environment configuration is missing."""


@dataclass(slots=True)
class McpServerConfig:
    """Definition of a single MCP server integration."""

    key: str
    kind: str
    protocol: str = MCP_PROTOCOL_MCP
    base_url: Optional[str] = None
    network: Optional[str] = None
    server_command: Optional[Tuple[str, ...]] = None
    rpc_urls: Dict[str, str] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None


@dataclass(slots=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: int
    gas_alert_threshold: Optional[float]
    telegram_read_timeout: float
    telegram_connect_timeout: float
    gemini_api_key: Optional[str]
    gemini_model: str
    mcp_servers: Tuple[McpServerConfig, ...]
    primary_evm_server: str
    primary_dexscreener_server: Optional[str]


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ConfigError(f"Environment variable '{key}' must be set")
    return value


def _ensure_protocol(value: Optional[str]) -> str:
    if value is None:
        return MCP_PROTOCOL_MCP
    value = value.strip().lower()
    if value not in {MCP_PROTOCOL_MCP, MCP_PROTOCOL_JSONRPC}:
        raise ConfigError("MCP protocol must be 'mcp' or 'json-rpc'")
    return value


def _parse_command(value: Optional[Sequence[str] | str]) -> Optional[Tuple[str, ...]]:
    if value is None:
        return None
    if isinstance(value, str):
        parts = tuple(shlex.split(value))
    else:
        parts = tuple(str(item) for item in value if str(item))
    if not parts:
        raise ConfigError("MCP server command must not be empty")
    return parts


def _parse_mapping(value: Optional[Mapping[str, str] | Sequence[Sequence[str]]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if value is None:
        return result
    if isinstance(value, Mapping):
        iterator: Iterable[Tuple[str, str]] = value.items()
    else:
        iterator = ((str(k), str(v)) for k, v in value)
    for key, val in iterator:
        if key:
            result[str(key)] = str(val)
    return result


def _parse_servers_from_json(raw_json: str) -> Tuple[McpServerConfig, ...]:
    try:
        decoded = json.loads(raw_json)
    except json.JSONDecodeError as exc:  # pragma: no cover - depends on user input
        raise ConfigError("MCP_SERVERS must be valid JSON") from exc

    if isinstance(decoded, Mapping) and "servers" in decoded:
        servers_payload = decoded.get("servers")
    else:
        servers_payload = decoded

    if not isinstance(servers_payload, Sequence):
        raise ConfigError("MCP_SERVERS must decode to a list of server definitions")

    servers: list[McpServerConfig] = []
    for entry in servers_payload:
        if not isinstance(entry, Mapping):
            raise ConfigError("Each MCP server definition must be an object")
        key = str(entry.get("key") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        if not key:
            raise ConfigError("MCP server definitions require a 'key'")
        if not kind:
            raise ConfigError(f"MCP server '{key}' is missing a 'kind'")
        protocol = _ensure_protocol(entry.get("protocol"))
        raw_base_url = entry.get("base_url") or entry.get("baseUrl")
        if raw_base_url is not None:
            base_url = str(raw_base_url).strip() or None
        else:
            base_url = None
        raw_network = entry.get("network")
        if raw_network is not None:
            network = str(raw_network).strip() or None
        else:
            network = None
        command = _parse_command(entry.get("command"))
        rpc_urls = _parse_mapping(entry.get("rpc_urls") or entry.get("rpcUrls"))
        env = _parse_mapping(entry.get("env"))
        cwd_raw = entry.get("cwd")
        cwd = str(cwd_raw) if isinstance(cwd_raw, str) and cwd_raw.strip() else None

        if base_url:
            base_url = base_url.rstrip("/")

        servers.append(
            McpServerConfig(
                key=key,
                kind=kind,
                protocol=protocol,
                base_url=base_url,
                network=network,
                server_command=command,
                rpc_urls=rpc_urls,
                env=env,
                cwd=cwd,
            )
        )

    return tuple(servers)


def _build_legacy_servers() -> Tuple[McpServerConfig, ...]:
    base_url = (
        os.getenv("MCP_EVM_BASE_URL")
        or os.getenv("ONCHAIN_VALIDATION_RPC_URL")
        or DEFAULT_MCP_BASE_URL
    )
    base_url = (base_url.rstrip("/") if base_url else DEFAULT_MCP_BASE_URL) or DEFAULT_MCP_BASE_URL

    protocol_env = os.getenv("MCP_EVM_PROTOCOL")
    if protocol_env is None and os.getenv("MCP_EVM_BASE_URL") is None and os.getenv("ONCHAIN_VALIDATION_RPC_URL"):
        protocol = MCP_PROTOCOL_JSONRPC
    else:
        protocol = _ensure_protocol(protocol_env)

    command = _parse_command(os.getenv("MCP_EVM_SERVER_COMMAND"))
    network = os.getenv("MCP_EVM_NETWORK", "base").strip() or "base"

    rpc_urls: Dict[str, str] = {}
    if protocol == MCP_PROTOCOL_JSONRPC:
        rpc_urls[network] = base_url

    servers: list[McpServerConfig] = [
        McpServerConfig(
            key="evm",
            kind="evm",
            protocol=protocol,
            base_url=base_url,
            network=network,
            server_command=command,
            rpc_urls=rpc_urls,
        )
    ]

    dexscreener_command_env = os.getenv("DEXSCREENER_MCP_COMMAND")
    dexscreener_root = os.getenv("DEXSCREENER_MCP_ROOT")
    dexscreener_command: Optional[Tuple[str, ...]] = None
    if dexscreener_command_env:
        dexscreener_command = _parse_command(dexscreener_command_env)
    elif dexscreener_root:
        entry = f"{dexscreener_root.rstrip('/')}/index.js"
        dexscreener_command = ("node", entry)

    if dexscreener_command:
        servers.append(
            McpServerConfig(
                key="dexscreener",
                kind="dexscreener",
                server_command=dexscreener_command,
            )
        )

    return tuple(servers)


def _resolve_primary_servers(servers: Sequence[McpServerConfig]) -> Tuple[str, Optional[str]]:
    if not servers:
        raise ConfigError("At least one MCP server must be configured")

    primary_evm = os.getenv("MCP_PRIMARY_EVM")
    if primary_evm:
        if not any(server.key == primary_evm for server in servers):
            raise ConfigError(f"MCP_PRIMARY_EVM references unknown server '{primary_evm}'")
    else:
        for server in servers:
            if server.kind == "evm":
                primary_evm = server.key
                break
        if primary_evm is None:
            raise ConfigError("No EVM MCP server configured; at least one is required")

    primary_dex = os.getenv("MCP_PRIMARY_DEXSCREENER")
    if primary_dex:
        if not any(server.key == primary_dex for server in servers):
            raise ConfigError(f"MCP_PRIMARY_DEXSCREENER references unknown server '{primary_dex}'")
    else:
        for server in servers:
            if server.kind == "dexscreener":
                primary_dex = server.key
                break

    return str(primary_evm), str(primary_dex) if primary_dex is not None else None


def load_config() -> Config:
    token = _require_env("TELEGRAM_MCP_BOT_TOKEN")
    chat_id_raw = _require_env("TELEGRAM_CHAT_ID")
    try:
        chat_id = int(chat_id_raw)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise ConfigError("TELEGRAM_CHAT_ID must be an integer") from exc

    threshold_raw = os.getenv("MCP_GAS_ALERT_THRESHOLD")
    threshold = float(threshold_raw) if threshold_raw else None

    servers_env = os.getenv("MCP_SERVERS")
    if servers_env:
        servers = _parse_servers_from_json(servers_env)
    else:
        servers = _build_legacy_servers()

    primary_evm, primary_dex = _resolve_primary_servers(servers)

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL

    return Config(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        gas_alert_threshold=threshold,
        telegram_read_timeout=float(os.getenv("TELEGRAM_HTTP_READ_TIMEOUT", 15.0)),
        telegram_connect_timeout=float(os.getenv("TELEGRAM_HTTP_CONNECT_TIMEOUT", 5.0)),
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        mcp_servers=servers,
        primary_evm_server=primary_evm,
        primary_dexscreener_server=primary_dex,
    )
