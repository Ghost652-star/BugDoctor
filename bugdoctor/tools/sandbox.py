from __future__ import annotations

from pathlib import Path


def resolve_in_project(project_root: Path, file_path: str) -> tuple[Path | None, str]:
    """路径解析 + 沙箱检查——禁止访问 project_root 以外的路径"""
    root = project_root.resolve()
    p = Path(file_path)
    if not p.is_absolute():
        p = root / p
    try:
        resolved = p.resolve()
        resolved.relative_to(root)  # 如果路径在 root 之外会抛 ValueError
    except (OSError, ValueError):
        return None, f"Error: path {file_path!r} is outside project root {root}"
    return resolved, ""
