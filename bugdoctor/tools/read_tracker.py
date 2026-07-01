from __future__ import annotations

from pathlib import Path

from bugdoctor.conversation.models import Message
from bugdoctor.tools.sandbox import resolve_in_project


class ReadTracker:
    """Tracks files read via read_file in the current session."""

    def __init__(self) -> None:
        self._read_paths: set[str] = set()

    def mark_read(self, path: Path) -> None:
        self._read_paths.add(str(path.resolve()))

    def has_read(self, path: Path) -> bool:
        return str(path.resolve()) in self._read_paths

    def restore_from_history(self, history: list[Message], project_root: Path) -> None:
        """Re-mark paths from prior read_file tool calls (resume session)."""
        for msg in history:
            if msg.role != "assistant":
                continue
            for use in msg.tool_uses:
                if use.tool_name != "read_file":
                    continue
                file_path = use.arguments.get("file_path")
                if not isinstance(file_path, str) or not file_path:
                    continue
                resolved, err = resolve_in_project(project_root, file_path)
                if resolved is not None and not err:
                    self.mark_read(resolved)
