from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bugdoctor.tools.base import Tool, ToolResult
from bugdoctor.tools.sandbox import resolve_in_project, should_skip_path

MAX_MATCHES = 100
MAX_CONTEXT = 5


class GrepCodeParams(BaseModel):
    pattern: str = Field(
        description="Regex pattern to search, e.g. 'def get_data', 'load_payload', or 'TypeError'.",
    )
    path: str = Field(
        default=".",
        description="Directory to search, relative to project root.",
    )
    include: str = Field(
        default="",
        description="Optional filename filter as glob, e.g. '*.py' or '*.java'. Empty means all text files.",
    )
    context: int = Field(
        default=0,
        description="Lines of context before/after each match (0-5). Use 1-2 to see surrounding code.",
    )


class GrepCodeTool(Tool):
    name = "grep_code"
    description = (
        "Search file contents by regex inside the project. "
        "Use when the traceback mentions a symbol, error text, or import but not where it is defined. "
        "Returns matches as path:line:content (up to 100 matches). Read-only. "
        "Prefer grep_code over run_command (grep/findstr) for searching code. "
        "After finding a match, use read_file to inspect the full function or block."
    )
    params_model = GrepCodeParams
    risk = "read"

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        params = GrepCodeParams.model_validate(arguments)
        base, err = resolve_in_project(self._project_root, params.path)
        if base is None:
            return ToolResult(err, is_error=True)
        if not base.exists():
            return ToolResult(f"Error: path not found: {params.path}", is_error=True)
        if not base.is_dir():
            return ToolResult(f"Error: not a directory: {params.path}", is_error=True)

        try:
            regex = re.compile(params.pattern)
        except re.error as exc:
            return ToolResult(f"Error: invalid regex: {exc}", is_error=True)

        context = max(0, min(params.context, MAX_CONTEXT))
        glob_pattern = params.include if params.include else "**/*"
        if not glob_pattern.startswith("**/"):
            glob_pattern = "**/" + glob_pattern

        results: list[str] = []
        truncated = False
        for file_path in sorted(base.glob(glob_pattern)):
            if not file_path.is_file() or should_skip_path(file_path):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            lines = text.splitlines()
            for line_num, line in enumerate(lines, 1):
                if not regex.search(line):
                    continue
                rel = file_path.relative_to(base)
                if context == 0:
                    results.append(f"{rel}:{line_num}:{line}")
                else:
                    start = max(1, line_num - context)
                    end = min(len(lines), line_num + context)
                    for i in range(start, end + 1):
                        prefix = ":" if i == line_num else "-"
                        results.append(f"{rel}:{i}{prefix}{lines[i - 1]}")
                if len(results) >= MAX_MATCHES:
                    truncated = True
                    break
            if truncated:
                break

        if not results:
            return ToolResult("No matches found.")

        output = "\n".join(results[:MAX_MATCHES])
        if truncated:
            output += f"\n(truncated, showing first {MAX_MATCHES} lines)"
        return ToolResult(output)
