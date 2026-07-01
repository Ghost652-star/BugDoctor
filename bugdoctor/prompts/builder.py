"""Prompt 构建器 —— 按优先级拼接多个 PromptSection"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptSection:
    """一段 prompt，有名字和优先级。优先级越低越靠前。"""
    name: str
    priority: int
    content: str


class PromptBuilder:
    """收集 PromptSection，按 priority 排序后用双换行拼接"""

    def __init__(self) -> None:
        self._sections: list[PromptSection] = []

    def add(self, section: PromptSection) -> PromptBuilder:
        self._sections.append(section)
        return self

    def build(self) -> str:
        self._sections.sort(key=lambda s: s.priority)
        parts = [s.content.strip() for s in self._sections if s.content.strip()]
        return "\n\n".join(parts)
