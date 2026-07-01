from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bugdoctor.tools.base import Tool, ToolResult

DEPENDENCY_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Pipfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)
MAX_FILE_LINES = 30


class GetEnvironmentParams(BaseModel):
    language: str = Field(
        default="auto",
        description="'auto' (detect from project files), 'python', or 'java'.",
    )


class GetEnvironmentTool(Tool):
    name = "get_environment"
    description = (
        "Collect structured environment info: platform, interpreter version, and dependency files "
        "(requirements.txt, pyproject.toml, pom.xml, etc.). "
        "Use when the user did not provide environment details or a bug may involve version/dependency mismatch. "
        "Prefer this over run_command for environment overview. "
        "Use run_command only for reproducing the actual failing script."
    )
    params_model = GetEnvironmentParams
    risk = "read"

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        params = GetEnvironmentParams.model_validate(arguments)
        root = self._project_root.resolve()
        language = self._detect_language(root, params.language)

        parts: list[str] = [f"Project root: {root}", f"Platform: {sys.platform}"]

        if language in ("auto", "python"):
            parts.append(f"Python runtime: {sys.version.split()[0]} ({sys.version.split()[1]})")
            py_ver = await self._run_command("python --version")
            if py_ver:
                parts.append(f"python --version: {py_ver}")

        if language in ("auto", "java") and self._has_java_project(root):
            java_ver = await self._run_command("java -version")
            if java_ver:
                parts.append(f"java -version:\n{java_ver}")

        dep_section = self._read_dependency_files(root)
        if dep_section:
            parts.append("Dependency files:")
            parts.append(dep_section)
        else:
            parts.append("Dependency files: (none found)")

        return ToolResult("\n".join(parts))

    def _detect_language(self, root: Path, hint: str) -> str:
        if hint in ("python", "java"):
            return hint
        if (root / "pom.xml").exists() or (root / "build.gradle").exists():
            return "java"
        return "python"

    def _has_java_project(self, root: Path) -> bool:
        return any((root / name).exists() for name in ("pom.xml", "build.gradle", "build.gradle.kts"))

    def _read_dependency_files(self, root: Path) -> str:
        blocks: list[str] = []
        for name in DEPENDENCY_FILES:
            path = root / name
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                blocks.append(f"  {name}: (unreadable)")
                continue
            preview = lines[:MAX_FILE_LINES]
            body = "\n".join(f"    {line}" for line in preview)
            suffix = f"\n    ... ({len(lines) - MAX_FILE_LINES} more lines)" if len(lines) > MAX_FILE_LINES else ""
            blocks.append(f"  {name}:\n{body}{suffix}")
        return "\n".join(blocks)

    async def _run_command(self, command: str) -> str:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self._project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except (asyncio.TimeoutError, OSError):
            return ""
        return stdout.decode(errors="replace").strip()
