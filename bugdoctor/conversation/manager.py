from __future__ import annotations

from dataclasses import dataclass, field

from bugdoctor.conversation.models import Message, ToolResultBlock, ToolUseBlock


@dataclass
class ConversationManager:
    """对话历史管理器"""
    history: list[Message] = field(default_factory=list)

    def add_user(self, content: str) -> None:
        self.history.append(Message(role="user", content=content))

    def add_assistant(
        self,
        content: str = "",
        tool_uses: list[ToolUseBlock] | None = None,
    ) -> None:
        self.history.append(
            Message(role="assistant", content=content, tool_uses=tool_uses or [])
        )

    def add_tool_results(self, results: list[ToolResultBlock]) -> None:
        self.history.append(Message(role="user", tool_results=results))

    def add_system_reminder(self, content: str) -> None:
        """运行时注入——skill 激活/memory 召回后插入对话，LLM 下一轮可见"""
        self.history.append(Message(role="user", content=f"[system] {content}"))

    def get_messages(self) -> list[Message]:
        return list(self.history)
