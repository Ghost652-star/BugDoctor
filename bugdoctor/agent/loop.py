"""ReAct Agent 循环 —— 假设驱动的 Bug 诊断
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.conversation.models import ToolUseBlock
from bugdoctor.llm.client import LLMClient
from bugdoctor.llm.events import TextDelta, ToolCallComplete
from bugdoctor.tools.base import ToolRegistry


# ── Agent 对外事件（app.py 根据事件类型做不同展示） ──

@dataclass
class StreamText:
    """LLM 输出的文字片段，对应 ReAct 的 Thought"""
    text: str


@dataclass
class ToolUseEvent:
    """LLM 决定调用工具，对应 ReAct 的 Action"""
    tool_name: str
    arguments: dict


@dataclass
class ToolResultEvent:
    """工具执行结果，对应 ReAct 的 Observation"""
    tool_name: str
    content: str
    is_error: bool


@dataclass
class TurnComplete:
    """本轮结束"""
    pass


@dataclass
class ErrorEvent:
    """Agent 级错误"""
    message: str


AgentEvent = StreamText | ToolUseEvent | ToolResultEvent | TurnComplete | ErrorEvent


class Agent:
    

    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        conversation: ConversationManager,
        system_prompt: str,
        max_iterations: int = 30,
    ) -> None:
        self.client = client
        self.registry = registry
        self.conversation = conversation
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations

    async def run(self, user_input: str) -> AsyncIterator[AgentEvent]:
        """执行 ReAct 循环"""
        self.conversation.add_user(user_input)

        for _ in range(self.max_iterations):  # 代码只控制轮数上限，每轮做什么由 LLM 决定
            # ── 1. 调 LLM（带工具列表） ──
            assistant_text = ""
            tool_calls: list[ToolCallComplete] = []

            async for event in self.client.stream(
                self.conversation,
                system=self.system_prompt,
                tools=self.registry.get_schemas(),  # 所有工具定义一次性传给 LLM
            ):
                if isinstance(event, TextDelta):
                    assistant_text += event.text
                    yield StreamText(event.text)
                elif isinstance(event, ToolCallComplete):
                    tool_calls.append(event)

            # ── 2. 无工具调用 → LLM 判断任务完成 → 结束 ──
            if not tool_calls:
                self.conversation.add_assistant(content=assistant_text)
                yield TurnComplete()
                return

            # ── 3. 有工具调用 → 记录 LLM 的 Action ──
            uses = [
                ToolUseBlock(
                    tool_use_id=tc.tool_call_id or str(uuid.uuid4()),
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                )
                for tc in tool_calls
            ]
            self.conversation.add_assistant(content=assistant_text, tool_uses=uses)

            # ── 4. 逐个执行工具 → 收集 Observation ──
            results = []
            for use in uses:
                yield ToolUseEvent(use.tool_name, use.arguments)
                block = await self.registry.run(use.tool_name, use.arguments, use.tool_use_id)
                results.append(block)
                yield ToolResultEvent(use.tool_name, block.content, block.is_error)

            # ── 5. Observation 写回对话 → 回到步骤 1，LLM 据此决定下一步 ──
            self.conversation.add_tool_results(results)

        yield ErrorEvent(f"Agent reached maximum iterations ({self.max_iterations})")
