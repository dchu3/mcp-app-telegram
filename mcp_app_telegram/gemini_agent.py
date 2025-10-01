"""Gemini-powered agent that selects MCP tools to answer questions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from .formatting import (
    format_account,
    format_gas_stats,
    format_generic_tool_result,
    format_transaction,
    format_dexscreener_pairs,
    format_dexscreener_profiles,
    format_dexscreener_boosts,
    format_dexscreener_orders,
)
from .coingecko_formatting import (
    format_asset_platforms,
    format_coin_detail,
    format_coin_history,
    format_coins_markets,
    format_generic_list,
    format_global,
    format_list,
    format_market_chart,
    format_nft,
    format_ohlc,
    format_search,
    format_search_trending,
    format_simple_price,
    format_token_price,
    format_top_gainers_losers,
    format_onchain_list,
    format_token_holders,
    format_trades,
)
from .mcp_client import (
    CoingeckoMcpClient,
    DexscreenerMcpClient,
    EvmMcpClient,
    McpClientError,
    McpToolDefinition,
)
from .mcp.manager import McpClientRegistry

_LOGGER = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-1.5-flash-latest"


class GeminiAgentError(RuntimeError):
    """Raised when the Gemini agent cannot fulfil a request."""


@dataclass(slots=True)
class _AgentPlan:
    tool: Optional[str]
    arguments: Dict[str, Any]
    reply: Optional[str]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    arguments: Mapping[str, str]
    handler: Callable[[Mapping[str, Any]], Awaitable[str]]


class GeminiAgent:
    """Routes natural language questions to MCP-backed tool calls via Gemini."""

    def __init__(
        self,
        registry: McpClientRegistry,
        primary_evm_key: str,
        api_key: Optional[str] = None,
        *,
        model: str = DEFAULT_GEMINI_MODEL,
        llm: Optional["_GeminiModelWrapper"] = None,
        tools: Optional[Sequence[ToolDefinition]] = None,
    ) -> None:
        if llm is None and not api_key:
            raise GeminiAgentError("Gemini API key is required when llm wrapper is not provided")
        self._registry = registry
        self._primary_evm_key = primary_evm_key
        self._evm_client = registry.require_typed(primary_evm_key, EvmMcpClient)
        self._llm = llm or _GeminiModelWrapper(api_key or "", model=model)
        self._tool_definitions: List[ToolDefinition] = list(tools or self._default_tool_definitions())
        self._tool_handlers: Dict[str, Callable[[Mapping[str, Any]], Awaitable[str]]] = {
            tool.name: tool.handler for tool in self._tool_definitions
        }

    def extend_tools(self, tools: Sequence[ToolDefinition]) -> None:
        for tool in tools:
            self._tool_definitions.append(tool)
            self._tool_handlers[tool.name] = tool.handler

    async def answer(self, question: str) -> str:
        """Return a response for ``question`` using MCP data where helpful."""

        question = question.strip()
        if not question:
            return "Please include a question for me to answer."

        try:
            plan = await self._plan(question)
        except GeminiAgentError as exc:
            _LOGGER.warning("Gemini planning failed: %s", exc)
            return "I couldn't work out how to answer that just now. Please try again."

        message_parts = []
        if plan.reply:
            message_parts.append(plan.reply.strip())

        tool_result: Optional[str] = None
        if plan.tool:
            handler = self._tool_handlers.get(plan.tool)

            if handler is None:
                _LOGGER.warning("Unsupported tool requested by Gemini: %s", plan.tool)
            else:
                try:
                    tool_result = await handler(plan.arguments)
                except (GeminiAgentError, McpClientError) as exc:
                    _LOGGER.warning("Gemini tool execution failed for %s: %s", plan.tool, exc)
                    message_parts.append("I couldn't retrieve that data right now. Please try again later.")
                except Exception as exc:  # pragma: no cover - defensive guard
                    _LOGGER.exception("Unexpected failure during tool execution")
                    message_parts.append("An unexpected error occurred while retrieving that data.")

        if tool_result:
            message_parts.append(tool_result)

        if not message_parts:
            return "I wasn't able to craft a response. Could you rephrase the request?"

        return "\n\n".join(part for part in message_parts if part)

    async def _run_gas_stats(self, _: Mapping[str, Any]) -> str:
        stats = await self._evm_client.fetch_gas_stats()
        return format_gas_stats(stats)

    async def _run_account_overview(self, args: Mapping[str, Any]) -> str:
        address = args.get("address")
        if not isinstance(address, str):
            raise GeminiAgentError("Gemini did not supply an address for the account overview")
        addr = address.strip().lower()
        if not (addr.startswith("0x") and len(addr) == 42):
            raise GeminiAgentError("Provided address is not a valid 42-character hex string")
        summary = await self._evm_client.fetch_account(addr)
        return format_account(summary)

    async def _run_transaction_status(self, args: Mapping[str, Any]) -> str:
        tx_hash = args.get("tx_hash")
        if not isinstance(tx_hash, str):
            raise GeminiAgentError("Gemini did not supply a transaction hash")
        tx = tx_hash.strip().lower()
        if not (tx.startswith("0x") and len(tx) == 66):
            raise GeminiAgentError("Provided transaction hash must be a 66-character hex string")
        summary = await self._evm_client.fetch_transaction(tx)
        return format_transaction(summary)

    async def _plan(self, question: str) -> _AgentPlan:
        prompt = self._build_prompt(question)
        raw_response = await self._llm.generate_json(prompt)
        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError as exc:  # pragma: no cover - depends on external model
            raise GeminiAgentError(f"Gemini returned invalid JSON: {raw_response!r}") from exc

        tool_value = data.get("tool")
        tool = str(tool_value) if isinstance(tool_value, str) and tool_value else None
        arguments = data.get("arguments") if isinstance(data.get("arguments"), Mapping) else {}
        reply_value = data.get("reply")
        reply = str(reply_value) if isinstance(reply_value, str) and reply_value else None

        return _AgentPlan(tool=tool, arguments=dict(arguments), reply=reply)

    def _build_prompt(self, question: str) -> str:
        tool_lines = []
        for tool in self._tool_definitions:
            args_text = json.dumps(tool.arguments)
            tool_lines.append(f"- {tool.name}: {tool.description} Arguments: {args_text}")

        tools_block = "\n".join(tool_lines)
        prompt = (
            "You are an assistant embedded in a Telegram bot for Base network data. "
            "You can optionally invoke at most one tool to satisfy the user's question.\n"
            "Tools available:\n"
            f"{tools_block}\n"
            "Return a JSON object with keys 'tool', 'arguments', and 'reply'.\n"
            "- 'tool' must be one of the names above or null if no tool fits.\n"
            "- 'arguments' must be a JSON object containing the parameters needed for the tool (use {} when none).\n"
            "- 'reply' should be a short sentence to show the user before appending any tool output.\n"
            "Use information in the question to choose the best tool.\n"
            "Question:\n"
            f"{question}\n"
            "Respond with valid JSON and nothing else."
        )
        return prompt

    def _default_tool_definitions(self) -> Sequence[ToolDefinition]:
        return (
            ToolDefinition(
                name="gas_stats",
                description="Retrieve latest Base gas tiers (safe/standard/fast), base fee, and sequencer lag.",
                arguments={},
                handler=self._run_gas_stats,
            ),
            ToolDefinition(
                name="account_overview",
                description="Summarise account balance, nonce, and contract status for a 0x-prefixed address.",
                arguments={"address": "Hex string 0x... (42 chars)"},
                handler=self._run_account_overview,
            ),
            ToolDefinition(
                name="transaction_status",
                description="Get transaction status, gas used, participants, and value for a transaction hash.",
                arguments={"tx_hash": "Hex string 0x... (66 chars)"},
                handler=self._run_transaction_status,
            ),
        )


def build_dexscreener_tool_definitions(
    client: DexscreenerMcpClient,
) -> Sequence[ToolDefinition]:
    def _extract_payload(raw: Mapping[str, Any]) -> Optional[Any]:
        content = raw.get("content") if isinstance(raw, Mapping) else None
        if isinstance(content, Sequence):
            for item in content:
                if isinstance(item, Mapping) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            continue
        inner = raw.get("toolResult") if isinstance(raw, Mapping) else None
        if isinstance(inner, Mapping):
            return inner
        return None

    def _formatter_pairs(data: Any) -> Optional[str]:
        return format_dexscreener_pairs(data)

    formatters: Dict[str, Callable[[Any], Optional[str]]] = {
        "searchPairs": _formatter_pairs,
        "getPairByChainAndAddress": _formatter_pairs,
        "getTokenPools": _formatter_pairs,
        "getPairsByToken": _formatter_pairs,
        "getLatestTokenProfiles": lambda data: format_dexscreener_profiles(
            data if isinstance(data, Sequence) else []
        ),
        "getLatestBoostedTokens": lambda data: format_dexscreener_boosts(
            data if isinstance(data, Sequence) else [], heading="Latest Boosted Tokens"
        ),
        "getMostActiveBoostedTokens": lambda data: format_dexscreener_boosts(
            data if isinstance(data, Sequence) else [], heading="Most Active Boosted Tokens"
        ),
        "checkTokenOrders": lambda data: format_dexscreener_orders(
            data if isinstance(data, Sequence) else []
        ),
    }

    definitions: List[ToolDefinition] = []
    for tool in client.tools:

        async def _handler(args: Mapping[str, Any], *, _tool=tool) -> str:
            try:
                raw_result = await client.call_tool(_tool.name, args)
            except McpClientError as exc:
                return f"Dexscreener error: {exc}"

            parsed = client.parse_tool_result(raw_result)
            if parsed is None and isinstance(raw_result, Mapping):
                parsed = _extract_payload(raw_result)

            formatter_fn = formatters.get(_tool.name.split("__", 1)[-1])
            if formatter_fn is not None and parsed is not None:
                try:
                    formatted = formatter_fn(parsed)
                except Exception:  # pragma: no cover - defensive
                    _LOGGER.exception("Failed to format Dexscreener result for %s", _tool.name)
                else:
                    if formatted:
                        return formatted

            if isinstance(parsed, Mapping):
                return format_generic_tool_result(_tool.name, parsed)
            if parsed is not None:
                return format_generic_tool_result(_tool.name, {"items": parsed})

            return format_generic_tool_result(
                _tool.name,
                raw_result if isinstance(raw_result, Mapping) else {"result": raw_result},
            )

        definitions.append(
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                arguments=tool.arguments,
                handler=_handler,
            )
        )
    return definitions


def build_coingecko_tool_definitions(client: CoingeckoMcpClient) -> Sequence[ToolDefinition]:
    def _ensure_sequence(value: Any) -> Sequence[Any]:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return value
        if isinstance(value, Mapping):
            return []
        return []

    formatters: Dict[str, Callable[[Any], Optional[str]]] = {
        "get_coins_markets": lambda data: format_coins_markets(data if isinstance(data, Sequence) else []),
        "get_coins_top_gainers_losers": lambda data: format_top_gainers_losers(data) if isinstance(data, Mapping) else None,
        "get_simple_price": lambda data: format_simple_price(data) if isinstance(data, Mapping) else None,
        "get_id_simple_token_price": lambda data: format_token_price(data) if isinstance(data, Mapping) else None,
        "get_simple_supported_vs_currencies": lambda data: format_generic_list(data if isinstance(data, Sequence) else [], "💱 Coingecko: Supported Currencies"),
        "get_asset_platforms": lambda data: format_asset_platforms(data if isinstance(data, Sequence) else []),
        "get_coins_list": lambda data: format_list(data if isinstance(data, Sequence) else [], "🪙 Coingecko: Coins"),
        "get_new_coins_list": lambda data: format_list(data if isinstance(data, Sequence) else [], "🆕 Coingecko: New Coins"),
        "get_list_coins_categories": lambda data: format_list(data if isinstance(data, Sequence) else [], "📚 Coingecko: Coin Categories"),
        "get_search": lambda data: format_search(data) if isinstance(data, Mapping) else None,
        "get_search_trending": lambda data: format_search_trending(data) if isinstance(data, Mapping) else None,
        "get_global": lambda data: format_global(data) if isinstance(data, Mapping) else None,
        "get_id_coins": lambda data: format_coin_detail(data) if isinstance(data, Mapping) else None,
        "get_coins_history": lambda data: format_coin_history(data) if isinstance(data, Mapping) else None,
        "get_coins_contract": lambda data: format_coin_detail(data) if isinstance(data, Mapping) else None,
        "get_range_coins_market_chart": lambda data: format_market_chart(data, heading="📊 Coingecko: Market Chart") if isinstance(data, Mapping) else None,
        "get_range_contract_coins_market_chart": lambda data: format_market_chart(data, heading="📊 Coingecko: Contract Market Chart") if isinstance(data, Mapping) else None,
        "get_nfts_market_chart": lambda data: format_market_chart(data, heading="📊 Coingecko: NFT Market Chart") if isinstance(data, Mapping) else None,
        "get_range_coins_ohlc": lambda data: format_ohlc(data) if isinstance(data, Sequence) else None,
        "get_id_nfts": lambda data: format_nft(data) if isinstance(data, Mapping) else None,
        "get_list_nfts": lambda data: format_list(data if isinstance(data, Sequence) else [], "🖼️ Coingecko: NFT Collections"),
        "get_onchain_categories": lambda data: format_onchain_list(data, heading="🧾 Coingecko Onchain Categories") if isinstance(data, Mapping) else None,
        "get_pools_onchain_categories": lambda data: format_onchain_list(data, heading="🏊 Coingecko Pool Categories") if isinstance(data, Mapping) else None,
        "get_onchain_networks": lambda data: format_onchain_list(data, heading="🌐 Coingecko Networks") if isinstance(data, Mapping) else None,
        "get_networks_onchain_new_pools": lambda data: format_onchain_list(data, heading="🆕 Coingecko New Pools") if isinstance(data, Mapping) else None,
        "get_network_networks_onchain_new_pools": lambda data: format_onchain_list(data, heading="🆕 Coingecko Network Pools") if isinstance(data, Mapping) else None,
        "get_networks_onchain_trending_pools": lambda data: format_onchain_list(data, heading="📈 Coingecko Trending Pools") if isinstance(data, Mapping) else None,
        "get_network_networks_onchain_trending_pools": lambda data: format_onchain_list(data, heading="📈 Coingecko Network Trending Pools") if isinstance(data, Mapping) else None,
        "get_networks_onchain_dexes": lambda data: format_onchain_list(data, heading="💱 Coingecko DEXes") if isinstance(data, Mapping) else None,
        "get_pools_networks_onchain_dexes": lambda data: format_onchain_list(data, heading="💱 Coingecko DEX Pools") if isinstance(data, Mapping) else None,
        "get_networks_onchain_pools": lambda data: format_onchain_list(data, heading="🏊 Coingecko Pools") if isinstance(data, Mapping) else None,
        "get_address_networks_onchain_pools": lambda data: format_onchain_list(data, heading="🏊 Coingecko Address Pools") if isinstance(data, Mapping) else None,
        "get_pools_networks_onchain_info": lambda data: format_onchain_list(data, heading="ℹ️ Coingecko Pool Info") if isinstance(data, Mapping) else None,
        "get_timeframe_pools_networks_onchain_ohlcv": lambda data: format_market_chart(data if isinstance(data, Mapping) else {}, heading="📊 Coingecko Pool OHLCV"),
        "get_pools_networks_onchain_trades": lambda data: format_trades(data, heading="🛒 Coingecko Pool Trades") if isinstance(data, Mapping) else None,
        "get_address_networks_onchain_tokens": lambda data: format_onchain_list(data, heading="🔍 Coingecko Address Tokens") if isinstance(data, Mapping) else None,
        "get_tokens_networks_onchain_info": lambda data: format_onchain_list(data, heading="ℹ️ Coingecko Token Info") if isinstance(data, Mapping) else None,
        "get_tokens_networks_onchain_top_holders": lambda data: format_token_holders(data) if isinstance(data, Mapping) else None,
        "get_tokens_networks_onchain_pools": lambda data: format_onchain_list(data, heading="🏊 Coingecko Token Pools") if isinstance(data, Mapping) else None,
        "get_tokens_networks_onchain_trades": lambda data: format_trades(data, heading="🛒 Coingecko Token Trades") if isinstance(data, Mapping) else None,
        "get_pools_onchain_megafilter": lambda data: format_onchain_list(data, heading="🧮 Coingecko Megafilter") if isinstance(data, Mapping) else None,
        "get_pools_onchain_trending_search": lambda data: format_onchain_list(data, heading="📈 Coingecko Trending Pools") if isinstance(data, Mapping) else None,
        "get_search_onchain_pools": lambda data: format_onchain_list(data, heading="🔍 Coingecko Pool Search") if isinstance(data, Mapping) else None,
        "get_addresses_networks_simple_onchain_token_price": lambda data: format_simple_price(data) if isinstance(data, Mapping) else None,
    }

    definitions: List[ToolDefinition] = []
    for tool in client.tools:

        async def _handler(args: Mapping[str, Any], *, _tool=tool) -> str:
            try:
                raw_result = await client.call_tool(_tool.name, args)
            except McpClientError as exc:
                return f"Coingecko error: {exc}"

            parsed = client.parse_tool_result(raw_result)
            if parsed is None and isinstance(raw_result, Mapping):
                content = raw_result.get("content")
                if isinstance(content, Sequence):
                    for item in content:
                        if isinstance(item, Mapping) and item.get("type") == "text":
                            text = item.get("text")
                            if isinstance(text, str):
                                try:
                                    parsed = json.loads(text)
                                except json.JSONDecodeError:
                                    continue
                                else:
                                    break

            formatter_fn = formatters.get(_tool.name)
            if formatter_fn is not None and parsed is not None:
                try:
                    formatted = formatter_fn(parsed)
                except Exception:  # pragma: no cover - defensive guard
                    _LOGGER.exception("Failed to format Coingecko result for %s", _tool.name)
                else:
                    if formatted:
                        return formatted

            if isinstance(parsed, Mapping):
                return format_generic_tool_result(_tool.name, parsed)
            if parsed is not None:
                return format_generic_tool_result(_tool.name, {"result": parsed})

            return format_generic_tool_result(
                _tool.name,
                raw_result if isinstance(raw_result, Mapping) else {"result": raw_result},
            )

        definitions.append(
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                arguments=tool.arguments,
                handler=_handler,
            )
        )

    return definitions


class _GeminiModelWrapper:
    """Thin wrapper around the google-generativeai client."""

    def __init__(self, api_key: str, *, model: str) -> None:
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard
            raise GeminiAgentError(
                "google-generativeai package is not installed; install it to enable the Gemini agent."
            ) from exc

        if not api_key:
            raise GeminiAgentError("Gemini API key is required")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    async def generate_json(self, prompt: str) -> str:
        response = await self._model.generate_content_async(  # type: ignore[attr-defined]
            prompt,
            generation_config={
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        )

        text = getattr(response, "text", None)
        if text:
            return text

        candidates = getattr(response, "candidates", None)
        if candidates:
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                if content is None:
                    continue
                parts = getattr(content, "parts", None)
                if not parts:
                    continue
                part_text = getattr(parts[0], "text", None)
                if part_text:
                    return part_text

        raise GeminiAgentError("Gemini returned an empty response")
