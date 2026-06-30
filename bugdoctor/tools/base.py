"""工具基类与注册表"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from bugdoctor.conversation.models import ToolResultBlock

ToolRisk = Literal["read", "run", "write"]


class ToolResult:
    """工具执行返回值"""
    def __init__(self, output: str, is_error: bool = False) -> None:
        self.output = output
        self.is_error = is_error


class Tool:
    """工具基类——所有工具必须继承此类"""
    name: str = ""                                  # 工具名，LLM 用这个名字调用
    description: str = ""                           # 工具描述，告诉 LLM 该工具做什么
    params_model: type[BaseModel] | None = None     # 参数结构（pydantic 模型），自动生成 JSON Schema
    risk: ToolRisk = "read"                         # 风险等级

    def get_schema(self) -> dict[str, Any]:
        """生成 OpenAI tool calling 格式的工具定义"""
        if not self.params_model:
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        schema = self.params_model.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """子类必须重写——执行工具的实际逻辑"""
        raise NotImplementedError


class ToolRegistry:
    """工具注册表——Agent 通过它查找和执行工具"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具实例"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名称查找工具"""
        return self._tools.get(name)

    def get_schemas(self) -> list[dict[str, Any]]:
        """收集所有工具的 schema → 传给 LLM"""
        return [t.get_schema() for t in self._tools.values()]

    def list_names(self) -> list[str]:
        """列出所有已注册工具名称"""
        return list(self._tools.keys())

    async def run(self, name: str, arguments: dict[str, Any], tool_use_id: str = "") -> ToolResultBlock:
        """查找 → pydantic 校验参数 → 执行 → 错误捕获。返回 ToolResultBlock 可直接写入对话历史。"""
        tool = self.get(name)
        if tool is None:
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"Error: unknown tool {name!r}",
                is_error=True,
            )
        try:
            if tool.params_model:
                params = tool.params_model.model_validate(arguments)
                args = params.model_dump()
            else:
                args = arguments
            result = await tool.execute(args)
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=result.output,
                is_error=result.is_error,
            )
        except ValidationError as exc:
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"Parameter validation error: {exc}",
                is_error=True,
            )
        except Exception as exc:
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"Tool execution error: {exc}",
                is_error=True,
            )
