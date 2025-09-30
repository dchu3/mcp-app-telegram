"""Gemini-powered agent that selects MCP tools to answer questions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .formatting import format_account, format_gas_stats, format_transaction
from .mcp_client import EvmMcpClient, McpClientError

_LOGGER = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-1.5-flash-latest"


class GeminiAgentError(RuntimeError):
    """Raised when the Gemini agent cannot fulfil a request."""


@dataclass(slots=True)
class _AgentPlan:
    tool: Optional[str]
    arguments: Dict[str, Any]
    reply: Optional[str]


_TOOL_DESCRIPTIONS: Dict[str, Dict[str, Any]] = {
    "gas_stats": {
        "description": "Retrieve latest Base gas tiers (safe/standard/fast), base fee, and sequencer lag.",
        "arguments": {},
    },
    "account_overview": {
        "description": "Summarise account balance, nonce, and contract status for a 0x-prefixed address.",
        "arguments": {"address": "Hex string 0x... (42 chars)"},
    },
    "transaction_status": {
        "description": "Get transaction status, gas used, participants, and value for a transaction hash.",
        "arguments": {"tx_hash": "Hex string 0x... (66 chars)"},
    },
}


class GeminiAgent:
    """Routes natural language questions to MCP-backed tool calls via Gemini."""

    def __init__(
        self,
        mcp_client: EvmMcpClient,
        api_key: Optional[str] = None,
        *,
        model: str = DEFAULT_GEMINI_MODEL,
        llm: Optional["_GeminiModelWrapper"] = None,
    ) -> None:
        if llm is None and not api_key:
            raise GeminiAgentError("Gemini API key is required when llm wrapper is not provided")
        self._client = mcp_client
        self._llm = llm or _GeminiModelWrapper(api_key or "", model=model)

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
            handler = {
                "gas_stats": self._run_gas_stats,
                "account_overview": self._run_account_overview,
                "transaction_status": self._run_transaction_status,
            }.get(plan.tool)

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
        stats = await self._client.fetch_gas_stats()
        return format_gas_stats(stats)

    async def _run_account_overview(self, args: Mapping[str, Any]) -> str:
        address = args.get("address")
        if not isinstance(address, str):
            raise GeminiAgentError("Gemini did not supply an address for the account overview")
        addr = address.strip().lower()
        if not (addr.startswith("0x") and len(addr) == 42):
            raise GeminiAgentError("Provided address is not a valid 42-character hex string")
        summary = await self._client.fetch_account(addr)
        return format_account(summary)

    async def _run_transaction_status(self, args: Mapping[str, Any]) -> str:
        tx_hash = args.get("tx_hash")
        if not isinstance(tx_hash, str):
            raise GeminiAgentError("Gemini did not supply a transaction hash")
        tx = tx_hash.strip().lower()
        if not (tx.startswith("0x") and len(tx) == 66):
            raise GeminiAgentError("Provided transaction hash must be a 66-character hex string")
        summary = await self._client.fetch_transaction(tx)
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
        for name, spec in _TOOL_DESCRIPTIONS.items():
            args_text = json.dumps(spec.get("arguments", {}))
            tool_lines.append(f"- {name}: {spec['description']} Arguments: {args_text}")

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
