from __future__ import annotations

from pathlib import Path

from bugdoctor.tools.base import ToolRegistry
from bugdoctor.tools.read_file import ReadFileTool


def create_registry(project_root: Path) -> ToolRegistry:
    """工具注册表工厂——启动时调用，注册所有可用工具"""
    registry = ToolRegistry()
    registry.register(ReadFileTool(project_root))
    return registry
