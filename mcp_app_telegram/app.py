"""Application entrypoint for the Telegram MCP integration."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from typing import Dict, Optional

from .alerts import GasAlertManager
from .bot import TELEGRAM_COMMANDS, build_application
from .config import (
    DEFAULT_MCP_BASE_URL,
    MCP_PROTOCOL_JSONRPC,
    ConfigError,
    load_config,
)
from .gemini_agent import (
    GeminiAgent,
    GeminiAgentError,
    build_coingecko_tool_definitions,
    build_dexscreener_tool_definitions,
)
from .mcp import CoingeckoMcpClient, DexscreenerMcpClient, EvmMcpClient
from .mcp.manager import McpClientRegistry
from .database import initialize_database


async def run() -> None:
    initialize_database()
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config()
    except ConfigError as exc:
        logging.getLogger(__name__).error("Configuration error: %s", exc)
        raise

    registry = McpClientRegistry()
    network_client_map: Dict[str, str] = {}

    evm_client: Optional[EvmMcpClient] = None
    dex_client: Optional[DexscreenerMcpClient] = None
    coingecko_clients: Dict[str, CoingeckoMcpClient] = {}

    for server in config.mcp_servers:
        if server.kind == "evm":
            base_url = (
                server.base_url
                or next(iter(server.rpc_urls.values()), DEFAULT_MCP_BASE_URL)
                or DEFAULT_MCP_BASE_URL
            )
            network = (server.network or "base").lower()
            rpc_urls = {str(k).lower(): str(v) for k, v in server.rpc_urls.items()}
            if server.protocol == MCP_PROTOCOL_JSONRPC:
                if not rpc_urls:
                    rpc_urls[network] = base_url
            elif not rpc_urls:
                rpc_urls[network] = base_url
            client = EvmMcpClient(
                base_url,
                protocol=server.protocol,
                command=server.server_command,
                network=network,
                rpc_urls=rpc_urls,
            )
            registry.register(server.key, client)
            if network:
                network_client_map.setdefault(network.lower(), server.key)
            for known_network in rpc_urls.keys():
                network_client_map.setdefault(str(known_network).lower(), server.key)
            if server.key == config.primary_evm_server:
                evm_client = client
        elif server.kind == "dexscreener":
            if not server.server_command:
                raise ConfigError(
                    f"Dexscreener MCP server '{server.key}' requires DEXSCREENER_MCP_COMMAND or equivalent"
                )
            client = DexscreenerMcpClient(
                server.server_command,
                env=server.env or None,
                cwd=server.cwd,
            )
            registry.register(server.key, client)
            if server.key == config.primary_dexscreener_server:
                dex_client = client
        elif server.kind == "coingecko":
            command = server.server_command or ("npx", "-y", "@coingecko/coingecko-mcp")
            client = CoingeckoMcpClient(command, env=server.env or None, cwd=server.cwd)
            registry.register(server.key, client)
            coingecko_clients[server.key] = client
        else:
            logging.getLogger(__name__).warning(
                "Ignoring MCP server '%s' with unsupported kind '%s'",
                server.key,
                server.kind,
            )

    if evm_client is None:
        raise ConfigError(
            f"Primary EVM MCP server '{config.primary_evm_server}' is not configured"
        )
    if config.primary_dexscreener_server and dex_client is None:
        raise ConfigError(
            f"Primary Dexscreener MCP server '{config.primary_dexscreener_server}' is not configured"
        )

    await registry.start_all()
    alert_manager = GasAlertManager()

    agent = None
    if config.gemini_api_key:
        try:
            agent = GeminiAgent(
                registry,
                config.primary_evm_server,
                config.gemini_api_key,
                model=config.gemini_model,
            )
            if dex_client is not None:
                agent.extend_tools(build_dexscreener_tool_definitions(dex_client))
            for cg_client in coingecko_clients.values():
                agent.extend_tools(build_coingecko_tool_definitions(cg_client))
        except GeminiAgentError as exc:
            logging.getLogger(__name__).warning("Gemini agent disabled: %s", exc)

    application = build_application(
        config,
        registry,
        alert_manager,
        agent=agent,
        dex_client=dex_client,
        coingecko_clients=coingecko_clients,
        primary_evm_key=config.primary_evm_server,
        primary_dex_key=config.primary_dexscreener_server,
        network_client_map=network_client_map,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _stop)

    try:
        await application.initialize()
        await application.start()
        try:
            await application.bot.set_my_commands(TELEGRAM_COMMANDS)
        except Exception:
            logging.getLogger(__name__).warning("Failed to register bot commands", exc_info=True)
        await application.updater.start_polling()
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
        with suppress(Exception):
            await application.updater.stop()
        with suppress(Exception):
            await application.stop()
        with suppress(Exception):
            await application.shutdown()
        with suppress(Exception):
            await registry.close_all()
