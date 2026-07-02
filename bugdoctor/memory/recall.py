"""记忆召回 — LLM 驱动的相关记忆选择与注入"""

from __future__ import annotations

import json

from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.client import LLMClient
from bugdoctor.llm.events import StreamEnd, TextDelta
from bugdoctor.memory.store import MemoryStore

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

MEMORY_INJECTION_FOOTER = """\

---
注意：以上记忆可能已过时，请以当前代码为准进行验证。"""


async def recall_relevant(
    user_input: str,
    store: MemoryStore,
    client: LLMClient,
) -> str:
    """检索与用户报错相关的历史记忆，返回拼接好的 memory_section。

    Args:
        user_input: 用户本轮报错描述
        store: MemoryStore 实例
        client: 复用的 LLM 客户端

    Returns:
        memory_section 字符串，可直接传入 build_system_prompt()
        没有相关记忆时返回 ""
    """
    manifest = store.list_manifest()
    if not manifest or manifest == "(empty)":
        return ""

    user_message = (
        f"User bug report:\n{user_input}\n\n"
        f"Available memories:\n{manifest}"
    )

    conv = ConversationManager()
    conv.add_user(user_message)

    collected = ""
    try:
        async for event in client.stream(
            conv,
            system=SELECTOR_SYSTEM_PROMPT,
            tools=None,
        ):
            if isinstance(event, TextDelta):
                collected += event.text
            elif isinstance(event, StreamEnd):
                pass
    except Exception:
        return ""

    selected = _parse_selector_response(collected)
    if not selected:
        return ""

    parts: list[str] = [MEMORY_INJECTION_HEADER]
    for i, filename in enumerate(selected, 1):
        content = store.read_memory(filename)
        if content is None:
            continue
        fm = store.parse_frontmatter(content)
        symptoms = fm.get("symptoms", "不清楚")
        root_cause = fm.get("root_cause", "不清楚")
        fix_approach = fm.get("fix_approach", "不清楚")
        name = fm.get("name", filename.replace(".md", ""))

        parts.append(f"### 记忆 {i}: {name}\n")
        parts.append(f"- 症状: {symptoms}\n")
        parts.append(f"- 根因: {root_cause}\n")
        parts.append(f"- 修复方向: {fix_approach}\n")

    if len(parts) == 1:
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
    try:
        parsed = json.loads(text)
        arr = parsed.get("selected_memories", [])
        if isinstance(arr, list):
            return [f for f in arr if isinstance(f, str) and f]
    except json.JSONDecodeError:
        pass
    return []
