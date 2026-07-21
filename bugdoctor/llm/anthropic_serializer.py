"""内部 Message list → Anthropic Messages API 格式。

Anthropic 的 Messages API 与 OpenAI chat.completions 的两个最大差异:
1. system 字段是顶层独立参数，不在 messages 数组里
2. assistant 的 content 可以是 text block + tool_use block 的混合数组
3. 工具结果以 user role 消息里的 `tool_result` content block 形式返回
"""

from __future__ import annotations

from typing import Any

from bugdoctor.conversation.models import Message


def build_anthropic_messages(
    history: list[Message],
    system: str,
) -> tuple[list[dict[str, Any]], str]:
    """把内部 Message 列表翻成 Anthropic 协议要求的消息格式。

    顶层 ``system`` 字段在 Anthropic 协议里独立于 messages 数组，
    所以这里把它从 system 参数里拿走、和 messages 分开返回。
    """
    out: list[dict[str, Any]] = []

    for msg in history:
        if msg.tool_results:
            for tr in msg.tool_results:
                content_text = tr.content if not tr.is_error else f"[Error] {tr.content}"
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tr.tool_use_id,
                                "content": content_text,
                                **({"is_error": True} if tr.is_error else {}),
                            }
                        ],
                    }
                )
            continue

        if msg.tool_uses:
            blocks: list[dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for tu in msg.tool_uses:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tu.tool_use_id,
                        "name": tu.tool_name,
                        "input": tu.arguments,
                    }
                )
            out.append({"role": "assistant", "content": blocks})
            continue

        out.append({"role": msg.role, "content": msg.content or ""})

    return out, system
