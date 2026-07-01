from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bugdoctor.tools.base import Tool, ToolResult
from bugdoctor.tools.read_tracker import ReadTracker
from bugdoctor.tools.sandbox import resolve_in_project


class EditFileParams(BaseModel):
    file_path: str = Field(
        description="File path relative to project root.",
    )
    old_string: str = Field(
        description="Exact text to find and replace. Must appear exactly once — copy from read_file output.",
    )
    new_string: str = Field(
        description="Replacement text.",
    )


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Apply a surgical fix by replacing an exact string in a source file. "
        "old_string must match exactly once (copy precisely from read_file). "
        "You MUST read_file the target file first — this tool rejects edits otherwise. "
        "Use only after diagnosing root cause. After editing, run_command to verify the fix."
    )
    params_model = EditFileParams
    risk = "write"

    def __init__(self, project_root: Path, read_tracker: ReadTracker) -> None:
        self._project_root = project_root
        self._read_tracker = read_tracker

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        params = EditFileParams.model_validate(arguments)
        resolved, err = resolve_in_project(self._project_root, params.file_path)
        if resolved is None:
            return ToolResult(err, is_error=True)
        if not resolved.exists():
            return ToolResult(f"Error: file not found: {params.file_path}", is_error=True)
        if not resolved.is_file():
            return ToolResult(f"Error: not a file: {params.file_path}", is_error=True)
        if not self._read_tracker.has_read(resolved):
            rel = resolved.relative_to(self._project_root.resolve())
            return ToolResult(
                f"Error: you must read_file({rel}) before edit_file on this path",
                is_error=True,
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(f"Error reading file: {exc}", is_error=True)

        count = content.count(params.old_string)
        if count == 0:
            return ToolResult("Error: old_string not found in file", is_error=True)
        if count > 1:
            return ToolResult(
                f"Error: old_string found {count} times, must be unique",
                is_error=True,
            )

        new_content = content.replace(params.old_string, params.new_string, 1)
        try:
            resolved.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(f"Error writing file: {exc}", is_error=True)

        rel = resolved.relative_to(self._project_root.resolve())
        return ToolResult(f"Successfully edited {rel}")
