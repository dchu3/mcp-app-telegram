"""Client wrapper for interacting with the Dexscreener MCP server."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..mcp_stdio import McpStdioClient, McpStdioError
from .base import McpClientError, McpToolDefinition, ToolClient


def _extract_arguments_from_schema(schema: Mapping[str, Any]) -> Dict[str, str]:
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    if not isinstance(properties, Mapping):
        return {}

    arguments: Dict[str, str] = {}
    for key, spec in properties.items():
        if not isinstance(spec, Mapping):
            continue
        description = spec.get("description")
        if not isinstance(description, str) or not description.strip():
            title = spec.get("title")
            if isinstance(title, str) and title.strip():
                description = title.strip()
            else:
                type_hint = spec.get("type")
                description = f"{type_hint}" if isinstance(type_hint, str) else "(value)"
        arguments[str(key)] = description.strip()
    return arguments


class DexscreenerMcpClient(ToolClient):
    """Client wrapper for interacting with the Dexscreener MCP server."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> None:
        if not command:
            raise McpClientError("Dexscreener MCP command must not be empty")
        self._stdio = McpStdioClient(command, env=env, cwd=cwd)
        self._tools: List[McpToolDefinition] = []

    async def start(self) -> None:
        try:
            await self._stdio.start()
        except McpStdioError as exc:
            raise McpClientError(f"Failed to start Dexscreener MCP server: {exc}") from exc

        try:
            listing = await self._stdio.list_tools()
        except McpStdioError as exc:
            raise McpClientError(f"Failed to list Dexscreener MCP tools: {exc}") from exc

        tools_payload = listing.get("tools") if isinstance(listing, Mapping) else None
        if not isinstance(tools_payload, list):
            raise McpClientError("Dexscreener MCP server returned invalid tool listing")

        parsed: List[McpToolDefinition] = []
        for raw_tool in tools_payload:
            if not isinstance(raw_tool, Mapping):
                continue
            name = raw_tool.get("name")
            description = raw_tool.get("description")
            input_schema = raw_tool.get("inputSchema")
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(description, str) or not description:
                description = "Dexscreener tool"
            arguments = (
                _extract_arguments_from_schema(input_schema)
                if isinstance(input_schema, Mapping)
                else {}
            )
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
            raise McpClientError(f"Dexscreener tool '{name}' failed: {exc}") from exc

    def parse_tool_result(self, result: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(result, Mapping):
            return None

        content = result.get("content")
        if isinstance(content, Sequence):
            for item in content:
                if isinstance(item, Mapping) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        try:
                            parsed = json.loads(text)
                        except json.JSONDecodeError:
                            continue
                        return parsed if isinstance(parsed, dict) else None

        tool_result = result.get("toolResult")
        if isinstance(tool_result, Mapping):
            return dict(tool_result)

        return None
