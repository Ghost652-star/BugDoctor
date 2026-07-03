"""Auto-compact — 对话摘要压缩（精简版 MewCode Layer 2）

触发 → 保留尾部 K 轮原文 → LLM 摘前缀 → 重建 history。
不做 token 锚点、不落盘工具结果、不恢复附件。
"""

from __future__ import annotations

from dataclasses import dataclass

from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.conversation.models import Message, estimate_tokens
from bugdoctor.llm.client import LLMClient
from bugdoctor.llm.events import StreamEnd, TextDelta

# ── 常量 ──────────────────────────────────────────────

KEEP_RECENT_TURNS = 5          # 尾部保留的完整轮数
MIN_PREFIX_TOKENS = 500        # 前缀不足此 token 数不压（避免"压了个寂寞"）

COMPACT_BOUNDARY_PREFIX = """\
上下文空间不足，早期对话已被压缩为以下摘要。

"""

COMPACT_BOUNDARY_SUFFIX = """

---
注意：以上为摘要。涉及具体代码行、报错原文时，请 **重新 read_file**，不要凭摘要猜测。"""

SUMMARY_SYSTEM_PROMPT = (
    "你是 BugDoctor 的诊断对话摘要助手。你只能输出纯文本，不要调用任何工具。"
)

SUMMARY_PROMPT = """\
请对下面的诊断对话生成结构化摘要。

先在 <analysis> 标签中梳理对话里发生了什么（这部分会被丢弃），然后在 <summary> 标签中输出正式摘要。

<summary> 必须包含以下 5 个部分：

1. **Bug 症状** — 报错信息和 traceback（原文保留！）
2. **涉及文件** — 读了哪些文件，关键代码位置（file:line）
3. **根因结论** — 最终定位到的根因是什么
4. **修复方向** — 给出的修复建议（描述思路，不要贴代码）
5. **诊断路径** — 假设→验证→结论的顺序（推理链）

如果对话中包含多个 Bug，按「问题 A / 问题 B」分段。

注意：不要调用任何工具。只需输出纯文本。"""


# ── 事件 ──────────────────────────────────────────────

@dataclass
class CompactEvent:
    before_tokens: int
    after_tokens: int
    summary: str = ""
    keep_count: int = 0


# ── 摘要生成 ──────────────────────────────────────────

def _extract_summary(llm_output: str) -> str:
    """从 LLM 输出中提取 <summary> 标签内容；找不到时返回原文。"""
    start = llm_output.find("<summary>")
    end = llm_output.find("</summary>")
    if start == -1 or end == -1:
        return llm_output.strip()
    return llm_output[start + len("<summary>"):end].strip()


async def _generate_summary(
    messages: list[Message],
    client: LLMClient,
) -> str:
    """调用 LLM 对前缀消息生成摘要（tools=None，两阶段 <analysis> + <summary>）。"""
    conv = ConversationManager()
    conv.history = list(messages)

    collected = ""
    try:
        async for event in client.stream(conv, system=SUMMARY_SYSTEM_PROMPT, tools=None):
            if isinstance(event, TextDelta):
                collected += event.text
            elif isinstance(event, StreamEnd):
                pass
    except Exception:
        return ""

    return _extract_summary(collected)


# ── 保留窗口 ──────────────────────────────────────────

def _compute_keep_start(messages: list[Message], keep_turns: int) -> int:
    """从尾部回溯，找到应保留的起始下标。

    保留最近 keep_turns 轮原文。一轮以 assistant（不含 tool_uses）结束。
    从后向前数，找到第 keep_turns+1 个轮次边界（即保留窗的前一道边界），
    keep_start 就是那道边界之后的第一条消息。
    同时确保不会从 tool_result 处切开，破坏 tool_use ↔ tool_result 配对。
    """
    n = len(messages)
    if n == 0:
        return 0

    turns_found = 0
    for i in range(n - 1, -1, -1):
        msg = messages[i]
        if msg.role == "assistant" and not msg.tool_uses:
            turns_found += 1
            if turns_found > keep_turns:
                # 这道边界之前就属于被摘要的前缀了
                keep_start = i + 1
                return _align_keep_start_to_tool_pair(messages, keep_start)

    return 0  # 轮数不足 keep_turns → 全部保留


def _align_keep_start_to_tool_pair(messages: list[Message], keep_start: int) -> int:
    """把 keep_start 往前挪，确保不会保留孤立的 tool_result。

    如果 keep_start 落在 user(tool_results) 上，回退到配对的 assistant(tool_uses)。
    """
    n = len(messages)
    while 0 < keep_start < n:
        msg = messages[keep_start]
        if msg.role == "user" and msg.tool_results:
            prev = messages[keep_start - 1]
            if prev.role == "assistant" and prev.tool_uses:
                keep_start -= 1
                continue
        break
    return keep_start


# ── 主入口 ────────────────────────────────────────────

async def auto_compact(
    conversation: ConversationManager,
    client: LLMClient,
    threshold: int,
    *,
    keep_turns: int = KEEP_RECENT_TURNS,
) -> CompactEvent | None:
    """检查对话 token 数；超阈值时 LLM 摘要前缀、原样保留尾部、重建 history。

    Returns:
        CompactEvent(before, after) 如果执行了压缩；None 如果不需要。
    """
    # 1. 阈值检查
    current = estimate_tokens(conversation.history)
    if current < threshold:
        return None

    # 2. 分割：前缀被摘要 / 尾部原样保留
    keep_start = _compute_keep_start(conversation.history, keep_turns)
    to_summarize = conversation.history[:keep_start]
    keep_tail = conversation.history[keep_start:]

    # 没有可摘要的前缀 → 跳过
    if not to_summarize:
        return None

    # 前缀太小 → 压了也不值，跳过
    if estimate_tokens(to_summarize) < MIN_PREFIX_TOKENS:
        return None

    # 3. 调 LLM 生成摘要
    summary = await _generate_summary(to_summarize, client)
    if not summary:
        return None

    # 4. 构建摘要边界消息 → 重建 history
    boundary_content = COMPACT_BOUNDARY_PREFIX + summary + COMPACT_BOUNDARY_SUFFIX
    boundary_msg = Message(role="user", content=boundary_content)

    new_history = [boundary_msg] + list(keep_tail)
    conversation.replace_history(new_history)

    after = estimate_tokens(conversation.history)
    return CompactEvent(
        before_tokens=current,
        after_tokens=after,
        summary=summary,
        keep_count=len(keep_tail),
    )
