"""Application entrypoint for the Telegram MCP integration."""

from __future__ import annotations

import asyncio
import logging
import signal
import os
from contextlib import suppress
from pathlib import Path
from typing import Dict, Optional

from telegram import (
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)


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
from .arb.profiles import ArbProfile, ProfileService
from .arb.signals import ArbSignalService
from .infra.ratelimit import RequestRateLimiter
from .infra.scheduler import CentralScheduler, PollingTier
from .infra.store import InMemoryStore
from .infra.swr import SwrCache
from .market.fetcher import MarketDataFetcher


async def run() -> None:
    initialize_database()
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root_logger.addHandler(handler)

    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper().strip()
    try:
        log_level = getattr(logging, log_level_name, logging.INFO)
    except AttributeError:  # pragma: no cover - defensive guard
        log_level = logging.INFO
    root_logger.setLevel(log_level)
    logging.captureWarnings(True)
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

    snapshot_env = os.getenv("STORE_SNAPSHOT_PATH", "data/store_snapshot.json").strip()
    snapshot_path = Path(snapshot_env) if snapshot_env else None
    store = InMemoryStore(snapshot_path)
    await store.load_snapshot()
    await store.initialize_pairs(config.scan_pairs, config.scan_size)

    rate_limiter = RequestRateLimiter(
        global_rate_per_min=config.global_reqs_per_min,
        per_host_rate_per_min={
            "dexscreener": 60,
            "evm": 45,
            "coingecko": 30,
        },
    )
    swr_cache = SwrCache(store, default_ttl=config.swr_ttl)

    default_profile = ArbProfile()
    profile_service = ProfileService(store, default_profile=default_profile)
    signal_service = ArbSignalService(default_mev_buffer_bps=config.mev_buffer_bps)
    fetcher = MarketDataFetcher(
        evm_client,
        default_size_eur=default_profile.test_size_eur,
        mev_buffer_bps=config.mev_buffer_bps,
    )

    scheduler = CentralScheduler(
        store=store,
        swr_cache=swr_cache,
        rate_limiter=rate_limiter,
        cadences={
            PollingTier.HOT: config.scan_cadence_hot,
            PollingTier.WARM: config.scan_cadence_warm,
            PollingTier.COLD: config.scan_cadence_cold,
        },
        fetcher=fetcher.fetch_pair,
    )

    agent = None
    if config.gemini_api_key:
        try:
            agent = GeminiAgent(
                registry,
                config.primary_evm_server,
                config.gemini_api_key,
                model=config.gemini_model,
                persona=config.gemini_persona,
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
        store=store,
        rate_limiter=rate_limiter,
        swr_cache=swr_cache,
        scheduler=scheduler,
        profile_service=profile_service,
        signal_service=signal_service,
        market_fetcher=fetcher,
    )

    logging.getLogger(__name__).debug("Starting market scheduler")
    await scheduler.start()
    logging.getLogger(__name__).debug("Market scheduler started")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _stop)

    application_started = False

    logger = logging.getLogger(__name__)

    try:
        logger.debug("Initializing Telegram application")
        await application.initialize()
        logger.debug("Application.initialize() completed")
        try:
            try:
                await application.bot.delete_webhook(drop_pending_updates=True)
                logger.info("Cleared existing Telegram webhook")
            except Exception:
                logger.warning("Failed to clear Telegram webhook", exc_info=True)
            logger.info(
                "Registering %d global commands", len(TELEGRAM_COMMANDS)
            )
            await application.bot.set_my_commands(TELEGRAM_COMMANDS)
            logger.info(
                "Registered global commands: %s", ", ".join(cmd.command for cmd in TELEGRAM_COMMANDS)
            )

            for scope_name, scope in (
                ("default", BotCommandScopeDefault()),
                ("private", BotCommandScopeAllPrivateChats()),
                ("group", BotCommandScopeAllGroupChats()),
            ):
                try:
                    await application.bot.set_my_commands(TELEGRAM_COMMANDS, scope=scope)
                    logger.info("Registered %s commands", scope_name)
                except Exception as exc:  # pragma: no cover - log only
                    logger.warning("Failed to register %s commands: %s", scope_name, exc)
        except Exception:
            logger.warning("Failed to register bot commands", exc_info=True)
        logger.info("Starting Telegram long polling")
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info(
            "Telegram long polling started (running=%s)", getattr(application.updater, "running", None)
        )
        await application.start()
        application_started = True
        logger.info("Application worker tasks started")
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
        with suppress(Exception):
            await application.updater.stop()
        if application_started:
            with suppress(Exception):
                await application.stop()
        with suppress(Exception):
            await application.shutdown()
        with suppress(Exception):
            await scheduler.stop()
        with suppress(Exception):
            await fetcher.close()
        with suppress(Exception):
            await registry.close_all()
