"""Client wrapper for interacting with the Coingecko MCP server."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping, Optional, Sequence

from ..mcp_stdio import McpStdioClient, McpStdioError
from .base import McpClientError, McpToolDefinition, ToolClient


class CoingeckoMcpClient(ToolClient):
    """Client wrapper for the Coingecko MCP stdio server."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> None:
        if not command:
            raise McpClientError("Coingecko MCP command must not be empty")
        resolved_command = list(command)
        if resolved_command and resolved_command[0] == "npx":
            from shutil import which

            npx_path = which("npx")
            if npx_path:
                resolved_command[0] = npx_path
        environment = dict(os.environ)
        if env:
            environment.update(env)
        self._stdio = McpStdioClient(tuple(resolved_command), env=environment, cwd=cwd)
        self._tools: list[McpToolDefinition] = []

    async def start(self) -> None:
        try:
            await self._stdio.start()
        except McpStdioError as exc:
            raise McpClientError(f"Failed to start Coingecko MCP server: {exc}") from exc

        try:
            listing = await self._stdio.list_tools()
        except McpStdioError as exc:
            raise McpClientError(f"Failed to list Coingecko MCP tools: {exc}") from exc

        tools_payload = listing.get("tools") if isinstance(listing, Mapping) else None
        if not isinstance(tools_payload, Sequence):
            raise McpClientError("Coingecko MCP server returned invalid tool listing")

        parsed: list[McpToolDefinition] = []
        for raw_tool in tools_payload:
            if not isinstance(raw_tool, Mapping):
                continue
            name = raw_tool.get("name")
            description = raw_tool.get("description")
            input_schema = raw_tool.get("inputSchema")
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(description, str) or not description:
                description = "Coingecko tool"
            arguments: Dict[str, str] = {}
            if isinstance(input_schema, Mapping):
                properties = input_schema.get("properties")
                if isinstance(properties, Mapping):
                    for key, spec in properties.items():
                        if not isinstance(spec, Mapping):
                            continue
                        desc = spec.get("description")
                        if not isinstance(desc, str) or not desc.strip():
                            title = spec.get("title")
                            if isinstance(title, str) and title.strip():
                                desc = title.strip()
                            else:
                                type_hint = spec.get("type")
                                desc = str(type_hint) if isinstance(type_hint, str) else "(value)"
                        arguments[str(key)] = desc.strip()
            parsed.append(McpToolDefinition(name=name, description=description.strip(), arguments=arguments))

        self._tools = parsed

    async def close(self) -> None:
        await self._stdio.close()

    @property
    def tools(self) -> Sequence[McpToolDefinition]:
        return tuple(self._tools)

    async def call_tool(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        try:
            return await self._stdio.call_tool(name, arguments)
        except McpStdioError as exc:
            raise McpClientError(f"Coingecko tool '{name}' failed: {exc}") from exc

    def parse_tool_result(self, result: Any) -> Optional[Any]:
        if not isinstance(result, Mapping):
            return None

        content = result.get("content")
        if isinstance(content, Sequence):
            for item in content:
                if isinstance(item, Mapping) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            continue

        tool_result = result.get("toolResult")
        if isinstance(tool_result, Mapping):
            return dict(tool_result)

        return None
