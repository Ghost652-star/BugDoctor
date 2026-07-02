"""Bug 模式记忆存储 — 管理 ~/.bugdoctor/memory/ 目录"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from bugdoctor.llm.client import LLMClient

MEMORY_DIR = Path.home() / ".bugdoctor" / "memory"
INDEX_NAME = "MEMORY.md"
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

EXTRACTION_PROMPT = """\
你是一个 Bug 模式提取助手。分析下面的诊断对话，判断是否有值得记录的新 Bug 模式。

已有记忆：
{existing_manifest}

用户报错：
{user_report}

诊断结论：
{diagnosis}

如果本轮发现了一个新的、之前没记录过的 Bug 模式，按以下格式输出一条记忆。
如果与已有记忆重复、或本轮没有新发现，只输出 "SKIP"。

输出格式：
---
name: <kebab-case-slug>
description: <一句话描述，用于索引检索>
metadata:
  type: bug_pattern
  symptoms: [关键词1, 关键词2]
  key_symbols: [函数名, 类名]
  root_cause: <一句话根因>
  fix_approach: <一句话修复方向>
---

## 症状

（描述用户看到的错误现象）

## 诊断过程

（关键发现步骤，Agent 如何确认根因的）

## 修复方向

（推荐的修复方法，不写具体代码，描述思路即可）

只输出记忆内容或 "SKIP"，不要输出任何其他文字。"""


class MemoryStore:
    """管理 ~/.bugdoctor/memory/ 下的 Bug 模式记忆文件"""

    def __init__(self) -> None:
        self.memory_dir = MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def list_manifest(self) -> str:
        """读取 MEMORY.md 索引文件，返回文本内容"""
        path = self.memory_dir / INDEX_NAME
        if not path.exists():
            return "(empty)"
        try:
            content = path.read_text(encoding="utf-8").strip()
            return content if content else "(empty)"
        except OSError:
            return "(empty)"

    def read_memory(self, name: str) -> str | None:
        """读取单条记忆的完整内容（name 含或不含 .md 后缀均可）"""
        if not name.endswith(".md"):
            name = f"{name}.md"
        path = self.memory_dir / name
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return None

    def write_memory(self, name: str, content: str) -> None:
        """写入 {name}.md 文件，并追加一行到 MEMORY.md 索引"""
        if not name.endswith(".md"):
            name = f"{name}.md"

        # 写记忆文件
        md_path = self.memory_dir / name
        md_path.write_text(content.strip() + "\n", encoding="utf-8")

        # 提取 description 用于索引行
        fm = self.parse_frontmatter(content)
        description = fm.get("description", "")

        # 追加索引行
        index_path = self.memory_dir / INDEX_NAME
        stem = name.replace(".md", "")
        index_line = f"- [{stem}]({name}) — {description}\n"

        if index_path.exists():
            existing = index_path.read_text(encoding="utf-8")
            if stem in existing:
                return  # 已存在，不重复添加
            index_path.write_text(existing.rstrip("\n") + "\n" + index_line, encoding="utf-8")
        else:
            index_path.write_text(index_line, encoding="utf-8")

    def parse_frontmatter(self, content: str) -> dict[str, Any]:
        """从 YAML frontmatter 中提取字段"""
        m = FRONTMATTER_RE.match(content)
        if not m:
            return {}
        try:
            parsed = yaml.safe_load(m.group(1))
            if not isinstance(parsed, dict):
                return {}
            return {k: v for k, v in parsed.items() if v}
        except yaml.YAMLError:
            return {}

    async def extract_and_save(self, client: LLMClient, user_report: str, diagnosis: str) -> str:
        """调用 LLM 提取 Bug 模式 → 写入记忆文件。

        返回记忆的 name（不含 .md），如果 LLM 判断不值得记则返回空字符串。
        """
        existing_manifest = self.list_manifest()

        prompt = EXTRACTION_PROMPT.format(
            existing_manifest=existing_manifest,
            user_report=user_report,
            diagnosis=diagnosis,
        )

        # 调 LLM 提取（不流式，直接收完整回复）
        from bugdoctor.conversation.manager import ConversationManager
        from bugdoctor.llm.events import TextDelta, StreamEnd

        conv = ConversationManager()
        conv.add_user(prompt)

        collected = ""
        try:
            async for event in client.stream(conv, system="你是一个记忆提取助手。", tools=None):
                if isinstance(event, TextDelta):
                    collected += event.text
                elif isinstance(event, StreamEnd):
                    pass
        except Exception:
            return ""  # 失败不阻塞主流程

        collected = collected.strip()
        if not collected or collected.upper().strip() == "SKIP":
            return ""

        # 提取 name 字段
        fm = self.parse_frontmatter(collected)
        name = fm.get("name", "")
        if not name:
            return ""

        self.write_memory(name, collected)
        return name
