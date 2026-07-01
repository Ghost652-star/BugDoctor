from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bugdoctor.tools.base import Tool, ToolResult
from bugdoctor.tools.read_tracker import ReadTracker
from bugdoctor.tools.sandbox import resolve_in_project


class ReadFileParams(BaseModel):
    file_path: str = Field(
        description="File path relative to project root (e.g. 'main.py', 'src/app.py'). Not an absolute path.",
    )
    offset: int = Field(
        default=0,
        description="0-based line number to start reading. Use with limit to read around a traceback line.",
    )
    limit: int = Field(
        default=200,
        description="Maximum lines to return (default 200). Increase only when necessary.",
    )


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a source file inside the project with line numbers. "
        "file_path is relative to project root. Default limit is 200 lines — use offset/limit "
        "to read around the error line in large files. "
        "Prefer this tool over run_command (cat/type/more) for reading code. "
        "You MUST read_file a file before edit_file on the same path."
    )
    params_model = ReadFileParams
    risk = "read"

    def __init__(self, project_root: Path, read_tracker: ReadTracker | None = None) -> None:
        self._project_root = project_root
        self._read_tracker = read_tracker

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
        if self._read_tracker is not None:
            self._read_tracker.mark_read(resolved)

        header = f"File: {resolved.relative_to(self._project_root.resolve())} (lines {start + 1}-{min(end, len(lines))} of {len(lines)})"
        body = "\n".join(numbered) if numbered else "(empty slice)"
        return ToolResult(f"{header}\n{body}")
