from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bugdoctor.tools.base import Tool, ToolResult
from bugdoctor.tools.sandbox import resolve_in_project


class ReadFileParams(BaseModel):
    """read_file 工具的参数模型 —— pydantic 自动生成 JSON Schema"""
    file_path: str = Field(description="要读取的文件路径（相对于项目根目录）")
    offset: int = Field(default=0, description="从第几行开始（0-based）")
    limit: int = Field(default=200, description="最多读取行数")


class ReadFileTool(Tool):
    """读取项目文件内容，返回带行号的文本"""
    name = "read_file"
    description = (
        "Read a source file with line numbers. Use offset/limit to read around an error line "
        "instead of loading the entire file."
    )
    params_model = ReadFileParams
    risk = "read"

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        params = ReadFileParams.model_validate(arguments)

        # 安全检查：禁止访问项目根目录以外的路径
        resolved, err = resolve_in_project(self._project_root, params.file_path)
        if resolved is None:
            return ToolResult(err, is_error=True)
        if not resolved.exists():
            return ToolResult(f"Error: file not found: {params.file_path}", is_error=True)
        if not resolved.is_file():
            return ToolResult(f"Error: not a file: {params.file_path}", is_error=True)

        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(f"Error: cannot read {params.file_path} as UTF-8 text", is_error=True)
        except OSError as exc:
            return ToolResult(f"Error reading file: {exc}", is_error=True)

        # 截取指定范围，添加行号
        lines = text.splitlines()
        start = max(params.offset, 0)
        end = start + max(params.limit, 1)
        selected = lines[start:end]
        numbered = [f"{i + start + 1}\t{line}" for i, line in enumerate(selected)]
        header = f"File: {resolved.relative_to(self._project_root.resolve())} (lines {start + 1}-{min(end, len(lines))} of {len(lines)})"
        body = "\n".join(numbered) if numbered else "(empty slice)"
        return ToolResult(f"{header}\n{body}")
