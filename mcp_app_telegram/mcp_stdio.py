"""Async stdio transport for interacting with MCP servers."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from asyncio.subprocess import Process
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

_LOGGER = logging.getLogger(__name__)


class McpStdioError(RuntimeError):
    """Raised when the stdio MCP transport encounters an unrecoverable error."""


def _split_command(command: str | Sequence[str]) -> Sequence[str]:
    if isinstance(command, str):
        return tuple(shlex.split(command))
    return tuple(command)


class McpStdioClient:
    """Lightweight JSON-RPC client that speaks the MCP stdio protocol."""

    def __init__(
        self,
        command: str | Sequence[str],
        *,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self._command = _split_command(command)
        if not self._command:
            raise ValueError("MCP command must not be empty")
        self._env = dict(env or {})
        self._cwd = cwd
        self._loop = loop or asyncio.get_event_loop()
        self._process: Optional[Process] = None
        self._stdout_task: Optional[asyncio.Task[None]] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._buffer = bytearray()
        self._pending: MutableMapping[Any, asyncio.Future[Dict[str, Any]]] = {}
        self._next_request_id = 1
        self._server_capabilities: Dict[str, Any] | None = None

    @property
    def server_capabilities(self) -> Optional[Dict[str, Any]]:
        return self._server_capabilities

    async def start(self) -> None:
        if self._process is not None:
            raise McpStdioError("MCP stdio client already started")

        _LOGGER.info("Starting MCP server: %s", " ".join(self._command))
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env or None,
            cwd=self._cwd,
        )

        if self._process.stdin is None or self._process.stdout is None:
            raise McpStdioError("MCP process did not provide stdio pipes")

        self._stdout_task = asyncio.create_task(self._read_stdout(), name="mcp-stdio-read")
        self._stderr_task = asyncio.create_task(self._read_stderr(), name="mcp-stdio-stderr")

        try:
            init_result = await self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "mcp-app-telegram",
                        "version": "0.1.0",
                    },
                },
            )
        except Exception:
            await self.close()
            raise

        if not isinstance(init_result, Mapping):
            await self.close()
            raise McpStdioError(f"Unexpected initialize result: {init_result!r}")

        self._server_capabilities = dict(init_result.get("capabilities") or {})

        await self.notification("notifications/initialized")

    async def close(self) -> None:
        if self._process is None:
            return

        if self._process.stdin:
            with suppress_exception():
                self._process.stdin.write_eof()

        if self._stdout_task:
            self._stdout_task.cancel()
            with suppress_exception():
                await self._stdout_task

        if self._stderr_task:
            self._stderr_task.cancel()
            with suppress_exception():
                await self._stderr_task

        with suppress_exception():
            await asyncio.wait_for(self._process.wait(), timeout=5)

        self._process = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpStdioError("MCP connection closed"))
        self._pending.clear()

    async def request(self, method: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if self._process is None or self._process.stdin is None:
            raise McpStdioError("MCP client is not connected")

        request_id = self._next_request_id
        self._next_request_id += 1

        message: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        future: asyncio.Future[Dict[str, Any]] = self._loop.create_future()
        self._pending[request_id] = future

        await self._send(message)
        response = await future

        if "error" in response:
            error = response["error"]
            message_text = error.get("message", "Unknown MCP error") if isinstance(error, Mapping) else str(error)
            raise McpStdioError(message_text)

        result = response.get("result")
        if not isinstance(result, Mapping):
            raise McpStdioError(f"Invalid MCP result payload: {response!r}")
        return dict(result)

    async def notification(self, method: str, params: Optional[Mapping[str, Any]] = None) -> None:
        if self._process is None or self._process.stdin is None:
            raise McpStdioError("MCP client is not connected")

        message: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def call_tool(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = dict(arguments)
        return await self.request("tools/call", params)

    async def list_tools(self) -> Dict[str, Any]:
        return await self.request("tools/list")

    async def ping(self) -> None:
        await self.request("ping")

    async def _send(self, message: Mapping[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        payload = json.dumps(message, separators=(",", ":")) + "\n"
        self._process.stdin.write(payload.encode("utf-8"))
        await self._process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                self._buffer.extend(chunk)
                while True:
                    newline_index = self._buffer.find(b"\n")
                    if newline_index == -1:
                        break
                    line = self._buffer[:newline_index]
                    del self._buffer[: newline_index + 1]
                    if not line:
                        continue
                    self._handle_line(bytes(line))
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive logging
            _LOGGER.exception("Error reading MCP stdout: %s", exc)
        finally:
            self._fail_pending("MCP stdout closed")

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        stream = self._process.stderr
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                _LOGGER.info("[MCP] %s", line.decode("utf-8", errors="ignore").rstrip())
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive logging
            _LOGGER.exception("Error reading MCP stderr: %s", exc)

    def _handle_line(self, data: bytes) -> None:
        try:
            message = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            _LOGGER.warning("Failed to decode MCP message: %s", exc)
            return

        if not isinstance(message, Mapping):
            _LOGGER.debug("Ignoring non-object MCP message: %r", message)
            return

        if "id" in message and ("result" in message or "error" in message):
            request_id = message["id"]
            future = self._pending.pop(request_id, None)
            if future is None:
                _LOGGER.debug("Dropping response for unknown request id %r", request_id)
                return
            if not future.done():
                future.set_result(dict(message))
            return

        method = message.get("method")
        if method:
            _LOGGER.debug("Received MCP notification %s", method)

    def _fail_pending(self, reason: str) -> None:
        if not self._pending:
            return
        exc = McpStdioError(reason)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()


class suppress_exception:
    """Context manager that suppresses any exception (used for cleanup)."""

    def __enter__(self) -> "suppress_exception":  # pragma: no cover - trivial
        return self

    def __exit__(self, *_exc: object) -> bool:  # pragma: no cover - trivial
        return True
