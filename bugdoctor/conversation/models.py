from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any

_CHARS_PER_TOKEN = 3.5


def estimate_tokens(messages: list) -> int:
    """粗略估算消息列表的 token 数——chars / 3.5。"""
    total = 0
    for msg in messages:
        total += len(msg.content or "")
        for tu in msg.tool_uses:
            total += len(tu.tool_name) + len(_json.dumps(tu.arguments, ensure_ascii=False))
        for tr in msg.tool_results:
            total += len(tr.content)
    return int(total / _CHARS_PER_TOKEN)


@dataclass
class ToolUseBlock:
    """LLM 决定调用某个工具时产生的结构化请求。"""
    tool_use_id: str          # 唯一 ID，用于把工具结果匹配回这次调用
    tool_name: str            # 工具名，如 "plot_distribution"
    arguments: dict[str, Any] # LLM 填的参数，如 {"column": "age", "bins": 20}


@dataclass
class ToolResultBlock:
    """工具执行完后返回的结果，通过 tool_use_id 与 ToolUseBlock 对应。"""
    tool_use_id: str
    content: str              # 工具返回值（文字或文件路径）
    is_error: bool = False    # 标记工具是否执行失败，方便上层做错误处理


@dataclass
class Message:
    """与任何 LLM 厂商格式无关的内部消息模型。
    """
    role: str                             
    content: str = ""
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)
