"""Allow running the package as a module."""

import asyncio
from .app import run


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    asyncio.run(run())