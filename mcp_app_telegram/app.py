"""Application entrypoint for the Telegram MCP integration."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from typing import Optional

from .alerts import GasAlertManager
from .bot import TELEGRAM_COMMANDS, build_application
from .config import ConfigError, load_config
from .gemini_agent import (
    GeminiAgent,
    GeminiAgentError,
    build_dexscreener_tool_definitions,
)
from .mcp_client import DexscreenerMcpClient, EvmMcpClient
from .database import initialize_database


async def run() -> None:
    initialize_database()
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config()
    except ConfigError as exc:
        logging.getLogger(__name__).error("Configuration error: %s", exc)
        raise

    client = EvmMcpClient(
        config.mcp_base_url,
        protocol=config.mcp_protocol,
        command=config.mcp_server_command,
        network=config.mcp_network,
        rpc_urls={"base": config.mcp_base_url},
    )
    await client.start()
    dex_client: Optional[DexscreenerMcpClient] = None
    if config.dexscreener_mcp_command:
        dex_client = DexscreenerMcpClient(config.dexscreener_mcp_command)
        await dex_client.start()
    alert_manager = GasAlertManager()

    agent = None
    if config.gemini_api_key:
        try:
            agent = GeminiAgent(client, config.gemini_api_key, model=config.gemini_model)
            if dex_client is not None:
                agent.extend_tools(build_dexscreener_tool_definitions(dex_client))
        except GeminiAgentError as exc:
            logging.getLogger(__name__).warning("Gemini agent disabled: %s", exc)

    application = build_application(
        config,
        client,
        alert_manager,
        agent=agent,
        dex_client=dex_client,
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
        await client.close()
        if dex_client is not None:
            with suppress(Exception):
                await dex_client.close()


