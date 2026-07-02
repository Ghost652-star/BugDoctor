"""记忆召回 — LLM 驱动的相关记忆选择与注入"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.client import LLMClient
from bugdoctor.llm.events import StreamEnd, TextDelta
from bugdoctor.memory.store import MemoryStore

RECALL_TIMEOUT_SEC = 30

SELECTOR_SYSTEM_PROMPT = (
    "You are selecting memories that will help BugDoctor diagnose a bug. "
    "Given the user's bug report and a list of available memory files with "
    "their descriptions, return up to 3 most relevant filenames.\n\n"
    "- Only include memories that are clearly relevant to the reported symptoms or error type.\n"
    "- If nothing matches, return an empty list.\n\n"
    'Respond with valid JSON only, no markdown, in this exact shape: '
    '{"selected_memories": ["file1.md", "file2.md"]}'
)

MEMORY_INJECTION_HEADER = """\
## 相关历史诊断

以下是你之前诊断过的类似 bug，请优先参考。如果当前症状匹配，
优先验证这些根因方向，而不是从零提出假设。

"""

MEMORY_INJECTION_FOOTER = """

---
注意：以上记忆可能已过时，请以当前代码为准进行验证。"""


@dataclass
class RecallResult:
    reminder: str = ""
    status: str = "empty"  # hit | empty | timeout | error


async def recall_relevant(
    user_input: str,
    store: MemoryStore,
    client: LLMClient,
) -> RecallResult:
    """检索与用户报错相关的历史记忆，返回可注入 system-reminder 的正文。

    由 app.py / agent.run 在 user 消息之后通过 add_system_reminder() 注入。
    没有相关记忆、超时或 API 失败时 reminder 为空，不阻塞诊断。
    """
    manifest = store.list_manifest()
    if not manifest or manifest == "(empty)":
        return RecallResult(status="empty")

    try:
        reminder = await asyncio.wait_for(
            _recall_with_selector(user_input, manifest, store, client),
            timeout=RECALL_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        return RecallResult(status="timeout")
    except Exception:
        return RecallResult(status="error")

    if reminder:
        return RecallResult(reminder=reminder, status="hit")
    return RecallResult(status="empty")


async def _recall_with_selector(
    user_input: str,
    manifest: str,
    store: MemoryStore,
    client: LLMClient,
) -> str:
    user_message = (
        f"User bug report:\n{user_input}\n\n"
        f"Available memories:\n{manifest}"
    )

    conv = ConversationManager()
    conv.add_user(user_message)

    collected = ""
    async for event in client.stream(
        conv,
        system=SELECTOR_SYSTEM_PROMPT,
        tools=None,
    ):
        if isinstance(event, TextDelta):
            collected += event.text
        elif isinstance(event, StreamEnd):
            pass

    selected = _parse_selector_response(collected)
    if not selected:
        return ""

    parts: list[str] = [MEMORY_INJECTION_HEADER]
    injected = 0
    for i, filename in enumerate(selected, 1):
        content = store.read_memory(filename)
        if content is None:
            continue
        basename = Path(filename).name
        parts.append(f"## 记忆 {i}: {basename}\n")
        parts.append(content.strip())
        parts.append("\n---\n")
        injected += 1

    if injected == 0:
        return ""

    parts.append(MEMORY_INJECTION_FOOTER)
    return "\n".join(parts)


def _parse_selector_response(raw: str) -> list[str]:
    """从 LLM 回复中提取 selected_memories 列表"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
        arr = parsed.get("selected_memories", [])
        if isinstance(arr, list):
            return [f for f in arr if isinstance(f, str) and f]
    except json.JSONDecodeError:
        pass
    return []
