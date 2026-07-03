"""Session JSONL persistence — cross-run conversation history."""

from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bugdoctor.conversation.models import Message, ToolResultBlock, ToolUseBlock

SESSIONS_DIR = ".bugdoctor/sessions"
TITLE_MAX_LENGTH = 50


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _short_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=4))


def _session_title(first_user_message: str) -> str:
    text = first_user_message.replace("\r\n", "\n").strip()
    first_line = text.split("\n", 1)[0].strip()
    if len(first_line) <= TITLE_MAX_LENGTH:
        return first_line
    return first_line[: TITLE_MAX_LENGTH - 3] + "..."


@dataclass
class SessionInfo:
    session_id: str
    created_at: str
    updated_at: str
    title: str
    message_count: int

    @property
    def display_time(self) -> str:
        try:
            dt = datetime.fromisoformat(self.updated_at)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return self.updated_at[:16]


def message_to_dict(message: Message) -> dict[str, Any]:
    data: dict[str, Any] = {"role": message.role}
    if message.content:
        data["content"] = message.content
    if message.tool_uses:
        data["tool_uses"] = [
            {
                "tool_use_id": tu.tool_use_id,
                "tool_name": tu.tool_name,
                "arguments": tu.arguments,
            }
            for tu in message.tool_uses
        ]
    if message.tool_results:
        data["tool_results"] = [
            {
                "tool_use_id": tr.tool_use_id,
                "content": tr.content,
                "is_error": tr.is_error,
            }
            for tr in message.tool_results
        ]
    return data


def compact_boundary_dict(summary: str, keep_count: int) -> dict[str, Any]:
    return {
        "type": "compact_boundary",
        "summary": summary,
        "keep_count": keep_count,
        "compacted_at": _now_iso(),
    }


def is_compact_boundary(data: dict[str, Any]) -> bool:
    return data.get("type") == "compact_boundary"


def message_from_dict(data: dict[str, Any]) -> Message | None:
    try:
        role = data["role"]
        tool_uses = [
            ToolUseBlock(
                tool_use_id=tu["tool_use_id"],
                tool_name=tu["tool_name"],
                arguments=tu.get("arguments", {}),
            )
            for tu in data.get("tool_uses", [])
        ]
        tool_results = [
            ToolResultBlock(
                tool_use_id=tr["tool_use_id"],
                content=tr.get("content", ""),
                is_error=bool(tr.get("is_error", False)),
            )
            for tr in data.get("tool_results", [])
        ]
        return Message(
            role=role,
            content=data.get("content", ""),
            tool_uses=tool_uses,
            tool_results=tool_results,
        )
    except (KeyError, TypeError):
        return None


class SessionStore:
    """Session JSONL — {data_root}/.bugdoctor/sessions/（不存在则自动创建）"""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.sessions_dir = self.data_root / SESSIONS_DIR
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _jsonl_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    def _meta_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.meta.json"

    def create(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = f"{stamp}_{_short_id()}"
        now = _now_iso()
        meta = {
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "title": "新对话",
            "message_count": 0,
        }
        self._meta_path(session_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._jsonl_path(session_id).touch()
        return session_id

    def list_sessions(self) -> list[SessionInfo]:
        sessions: list[SessionInfo] = []
        for meta_path in self.sessions_dir.glob("*.meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            session_id = meta.get("session_id") or meta_path.stem.replace(".meta", "")
            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    created_at=meta.get("created_at", ""),
                    updated_at=meta.get("updated_at", ""),
                    title=meta.get("title", session_id),
                    message_count=int(meta.get("message_count", 0)),
                )
            )
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def exists(self, session_id: str) -> bool:
        return self._meta_path(session_id).exists() or self._jsonl_path(session_id).exists()

    def load_history(self, session_id: str) -> list[Message]:
        """读取会话历史。如果 JSONL 中有 compact_boundary 记录，
        只返回最后一条 compact_boundary 之后的消息（压缩后的精简版）。
        """
        path = self._jsonl_path(session_id)
        if not path.exists():
            return []
        messages: list[Message] = []
        last_boundary_idx = -1
        all_lines = path.read_text(encoding="utf-8").splitlines()

        for i, line in enumerate(all_lines):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_compact_boundary(data):
                last_boundary_idx = i
                continue
            msg = message_from_dict(data)
            if msg is not None:
                messages.append(msg)

        # 找到最后一条 boundary 的位置 → 只保留它之后的消息
        if last_boundary_idx >= 0:
            messages = _messages_after_index(all_lines, last_boundary_idx)

        return messages

    def load_full_history(self, session_id: str) -> list[Message]:
        """读取全部历史（忽略 compact_boundary），用于终端展示。"""
        path = self._jsonl_path(session_id)
        if not path.exists():
            return []
        messages: list[Message] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_compact_boundary(data):
                continue
            msg = message_from_dict(data)
            if msg is not None:
                messages.append(msg)
        return messages

    def append_compact_boundary(self, session_id: str, summary: str, keep_count: int) -> None:
        """在 JSONL 末尾写入 compact_boundary 标记。"""
        path = self._jsonl_path(session_id)
        record = compact_boundary_dict(summary, keep_count)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_messages(self, session_id: str, messages: list[Message]) -> None:
        if not messages:
            return
        path = self._jsonl_path(session_id)
        with path.open("a", encoding="utf-8") as fh:
            for msg in messages:
                fh.write(json.dumps(message_to_dict(msg), ensure_ascii=False) + "\n")
        self._touch_meta(session_id, new_messages=messages)

    def _touch_meta(self, session_id: str, new_messages: list[Message]) -> None:
        meta_path = self._meta_path(session_id)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            now = _now_iso()
            meta = {
                "session_id": session_id,
                "created_at": now,
                "updated_at": now,
                "title": "新对话",
                "message_count": 0,
            }

        meta["updated_at"] = _now_iso()
        meta["message_count"] = int(meta.get("message_count", 0)) + len(new_messages)

        if meta.get("title") in {"", "新对话"}:
            for msg in new_messages:
                if msg.role == "user" and msg.content and not msg.tool_results:
                    meta["title"] = _session_title(msg.content)
                    break

        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _messages_after_index(lines: list[str], boundary_idx: int) -> list[Message]:
    """从 boundary 行之后解析 Message。"""
    messages: list[Message] = []
    for line in lines[boundary_idx + 1:]:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if is_compact_boundary(data):
            continue
        msg = message_from_dict(data)
        if msg is not None:
            messages.append(msg)
    return messages


def choose_session_interactive(store: SessionStore) -> tuple[str, list[Message]]:
    """Prompt user to start a new session or resume an existing one."""
    sessions = store.list_sessions()

    print("对话历史（保存在 .bugdoctor/sessions/）：")
    print("  [N] 新对话")
    for idx, info in enumerate(sessions, start=1):
        title = info.title.replace("\n", " ")
        print(f"  [{idx}] {info.display_time}  {title}  ({info.message_count} 条)")

    while True:
        try:
            choice = input("\n选择 [N/编号]: ").strip()
        except EOFError:
            choice = "N"

        if not choice or choice.upper() == "N":
            session_id = store.create()
            print(f"已创建新对话: {session_id}\n")
            return session_id, []

        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(sessions):
                info = sessions[index - 1]
                history = store.load_history(info.session_id)
                print(f"已恢复对话 {info.session_id}（{len(history)} 条消息）\n")
                return info.session_id, history

        print("无效选择，请输入 N 或列表中的编号。")
