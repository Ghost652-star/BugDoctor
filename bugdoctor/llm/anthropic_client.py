"""Anthropic Messages API 流式客户端 → 标准 StreamEvent。

与 OpenAI 兼容客户端的差异:
- 工具调用嵌在 ``content_block`` 数组里，按 ``index`` 索引位置而非稳定 ID
- 参数按 ``input_json_delta`` 字符级别增量，必须手动累积 JSON 字符串
- 结束信号是 ``message_delta.stop_reason == 'end_turn'`` 与 ``message_stop``
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from bugdoctor.config import LLMConfig
from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.anthropic_serializer import build_anthropic_messages
from bugdoctor.llm.client import LLMClient, LLMError
from bugdoctor.llm.events import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)


_DEFAULT_API_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 4096


class AnthropicClient(LLMClient):
    """Anthropic Messages API 流式 → 标准 StreamEvent。"""

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise LLMError(
                "Anthropic API key missing. Set BUGDOCTOR_API_KEY or "
                "config.yaml llm.api_key with provider=anthropic."
            )
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url or _DEFAULT_API_URL,
            timeout=httpx.Timeout(120.0),
        )

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        messages, system_text = build_anthropic_messages(
            conversation.get_messages(), system
        )

        body: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_output_tokens
            or _DEFAULT_MAX_TOKENS,
            "messages": messages,
            "stream": True,
        }
        if system_text:
            body["system"] = system_text
        if tools:
            body["tools"] = [_convert_tool_schema(t) for t in tools]

        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
            "accept": "text/event-stream",
        }

        pending_args: dict[int, str] = {}
        tool_meta: dict[int, str] = {}
        tool_id_by_index: dict[int, str] = {}

        async with self._client.stream(
            "POST", "/v1/messages", json=body, headers=headers
        ) as response:
            if response.status_code != 200:
                err_bytes = await response.aread()
                raise LLMError(
                    f"Anthropic API error {response.status_code}: "
                    f"{err_bytes.decode('utf-8', errors='replace')[:300]}"
                )

            event_name = ""
            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                    continue
                if not line.startswith("data:"):
                    continue

                payload = line[len("data:"):].strip()
                if not payload or payload == "[DONE]":
                    continue

                try:
                    evt = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                for out in _dispatch_sse_event(
                    evt, event_name, pending_args, tool_meta, tool_id_by_index
                ):
                    yield out

        yield StreamEnd()


def _dispatch_sse_event(
    evt: dict,
    event_name: str,
    pending_args: dict[int, str],
    tool_meta: dict[int, str],
    tool_id_by_index: dict[int, str],
) -> list[StreamEvent]:
    """把单个 SSE event 翻译成 0 或多个 StreamEvent。"""
    etype = evt.get("type", event_name)
    out: list[StreamEvent] = []

    if etype == "content_block_start":
        block = evt.get("content_block", {})
        if block.get("type") == "tool_use":
            idx = evt.get("index")
            tid = block.get("id") or f"anthropic-tool-{idx}"
            tool_id_by_index[idx] = tid
            tool_meta[idx] = block.get("name", "")
            pending_args[idx] = ""
            out.append(ToolCallStart(tool_call_id=tid, tool_name=tool_meta[idx]))

    elif etype == "content_block_delta":
        delta = evt.get("delta", {})
        dtype = delta.get("type")
        idx = evt.get("index")
        if dtype == "text_delta":
            out.append(TextDelta(delta.get("text", "")))
        elif dtype == "input_json_delta":
            chunk = delta.get("partial_json", "")
            pending_args[idx] = pending_args.get(idx, "") + chunk
            tid = tool_id_by_index.get(idx, f"anthropic-tool-{idx}")
            out.append(
                ToolCallDelta(tool_call_id=tid, arguments_delta=chunk)
            )

    elif etype == "content_block_stop":
        idx = evt.get("index")
        if idx in pending_args:
            tid = tool_id_by_index[idx]
            name = tool_meta[idx]
            raw = pending_args[idx] or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
            out.append(
                ToolCallComplete(tool_call_id=tid, tool_name=name, arguments=args)
            )
            pending_args.pop(idx, None)
            tool_meta.pop(idx, None)
            tool_id_by_index.pop(idx, None)

    return out


def _convert_tool_schema(openai_schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI 工具 schema → Anthropic 工具 schema。

    OpenAI:    ``{type:"function", function:{name, description, parameters}}``
    Anthropic: ``{name, description, input_schema}``
    """
    if openai_schema.get("type") == "function":
        fn = openai_schema["function"]
        return {
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get(
                "parameters", {"type": "object", "properties": {}}
            ),
        }
    return openai_schema
