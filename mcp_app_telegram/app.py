"""Application entrypoint for the Telegram MCP integration."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from .alerts import GasAlertManager
from .bot import TELEGRAM_COMMANDS, build_application
from .config import ConfigError, load_config
from .mcp_client import EvmMcpClient


async def run() -> None:
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
    )
    await client.start()
    alert_manager = GasAlertManager()
    application = build_application(config, client, alert_manager)

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


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    main()
