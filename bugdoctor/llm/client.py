from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from bugdoctor.config import LLMConfig
from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.events import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from bugdoctor.llm.serializer import build_chat_completion_messages


class LLMError(Exception):
    pass


class LLMClient(ABC):
    """Layer 1: only talks to the model API; no tools, no ReAct logic."""

    @abstractmethod
    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta("")


class OpenAICompatClient(LLMClient):
    """OpenAI 流式 chunk → 标准 StreamEvent"""

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise LLMError("API key missing. Set BUGDOCTOR_API_KEY or config.yaml llm.api_key")
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=120.0,
        )

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        messages = build_chat_completion_messages(conversation.get_messages(), system)
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "max_tokens": self._config.max_output_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # 跨 chunk 缓存工具参数碎片
        pending: dict[str, dict[str, Any]] = {}

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                yield TextDelta(delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    if tc.id:
                        pending[tc.id] = {"name": tc.function.name or "", "arguments": ""}
                        yield ToolCallStart(tc.id, tc.function.name or "")
                    elif tc.function and tc.function.arguments and pending:
                        last_id = next(reversed(pending))
                        pending[last_id]["arguments"] += tc.function.arguments
                        yield ToolCallDelta(last_id, tc.function.arguments)

            if choice.finish_reason == "tool_calls":
                import json

                for tid, meta in pending.items():
                    try:
                        args = json.loads(meta["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    yield ToolCallComplete(tid, meta["name"], args)

        yield StreamEnd()


def create_client(config: LLMConfig) -> LLMClient:
    """LLM 客户端工厂"""
    if config.provider == "openai-compat":
        return OpenAICompatClient(config)
    raise LLMError(f"Unsupported provider: {config.provider}")
