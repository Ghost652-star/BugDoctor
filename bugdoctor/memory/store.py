"""Bug 模式记忆 — {data_root}/.bugdoctor/memory/"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.client import LLMClient
from bugdoctor.llm.events import StreamEnd, TextDelta

MEMORY_SUBDIR = Path(".bugdoctor") / "memory"
INDEX_NAME = "MEMORY.md"
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

EXTRACTION_SYSTEM_PROMPT = (
    "你是 BugDoctor 的记忆维护助手。"
    "根据诊断结论决定 create / update / delete / skip，只输出 JSON，不要 markdown 代码块。"
)

EXTRACTION_USER_PROMPT = """\
分析下面的诊断对话，维护 Bug 模式记忆库。

已有记忆索引（MEMORY.md）：
{existing_manifest}

用户报错：
{user_report}

诊断结论：
{diagnosis}

请返回 JSON（仅此 JSON，无其他文字）。字段说明：

- action: "create" | "update" | "delete" | "skip"
- target: 已有记忆的 name（不含 .md）。update/delete 时必填；create/skip 时填空字符串 ""
- reason: 一句话说明你的决定
- memory: create/update 时填写对象；delete/skip 时设为 null

action 规则：
1. create — 新的、可复用的 Bug 模式，且与已有记忆不重复
2. update — 与某条已有记忆是同一类 Bug，但本轮诊断更准确或更完整（target 填要更新的那条 name）
3. delete — 某条记忆明显错误或重复（target 填要删的那条 name，memory 为 null）
4. skip — 纯聊天、重复、或没有值得记录的内容（memory 为 null）

memory 对象字段（create/update 时 LLM 只填这些纯文本/数组，不要写 frontmatter，不要写 .md）：
- name: 英文 kebab-case 文件名，如 "isdigit-negative-amount"
- description: 一句话摘要（写入索引）
- symptoms: 关键词数组，如 ["TypeError", "multiply sequence"]
- key_symbols: 相关函数/类名数组
- root_cause: 一句话根因
- fix_approach: 一句话修复方向
- symptoms_text: "## 症状" 下的正文（纯文本，可多行）
- diagnosis_text: "## 诊断过程" 下的正文
- fix_text: "## 修复方向" 下的正文

完整示例（create）：
{{
  "action": "create",
  "target": "",
  "reason": "发现新的 isdigit 负数解析 bug 模式",
  "memory": {{
    "name": "isdigit-negative-amount",
    "description": "isdigit() 无法识别负数，amount 保持为字符串导致乘法 TypeError",
    "symptoms": ["TypeError", "multiply sequence", "float"],
    "key_symbols": ["_normalize_amount", "Transaction.amount"],
    "root_cause": "str.isdigit() 对 '-5' 返回 False，amount 未转为 int",
    "fix_approach": "用 int/float 解析替代 isdigit()",
    "symptoms_text": "运行 main.py 报 TypeError: can't multiply sequence by non-int of type 'float'",
    "diagnosis_text": "CSV 中 Amount=-5，_normalize_amount 因 isdigit 失败返回字符串，report_service 乘法时报错",
    "fix_text": "在 transaction_repository._normalize_amount 中用 try/except 转 int/float"
  }}
}}

完整示例（skip）：
{{
  "action": "skip",
  "target": "",
  "reason": "用户只是闲聊，没有新的 bug 模式",
  "memory": null
}}"""


@dataclass
class MemoryMaintainResult:
    action: str  # create | update | delete | skip | error
    target: str = ""
    reason: str = ""


class MemoryStore:
    """Bug 模式记忆 — {data_root}/.bugdoctor/memory/（不存在则自动创建）"""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.memory_dir = self.data_root / MEMORY_SUBDIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def list_manifest(self) -> str:
        """读取 MEMORY.md 索引文件，返回文本内容"""
        path = self.memory_dir / INDEX_NAME
        if not path.exists():
            return "(empty)"
        try:
            content = path.read_text(encoding="utf-8").strip()
            return content if content else "(empty)"
        except OSError:
            return "(empty)"

    def read_memory(self, name: str) -> str | None:
        """读取单条记忆的完整内容（name 含或不含 .md 后缀均可）"""
        if not name.endswith(".md"):
            name = f"{name}.md"
        path = self.memory_dir / name
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return None

    def write_memory(self, name: str, content: str) -> None:
        """写入或更新 {name}.md，并同步 MEMORY.md 索引行"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if not name.endswith(".md"):
            name = f"{name}.md"

        md_path = self.memory_dir / name
        md_path.write_text(content.strip() + "\n", encoding="utf-8")

        fm = self.parse_frontmatter(content)
        description = str(fm.get("description", ""))
        stem = name.replace(".md", "")
        self._upsert_index_entry(stem, name, description)

    def delete_memory(self, name: str) -> bool:
        """删除记忆文件及索引行"""
        if not name.endswith(".md"):
            name = f"{name}.md"
        path = self.memory_dir / name
        if not path.exists():
            return False
        path.unlink()

        stem = name.replace(".md", "")
        self._remove_index_entry(stem, name)
        return True

    def parse_frontmatter(self, content: str) -> dict[str, Any]:
        """从 YAML frontmatter 提取字段，并将 metadata 子字段摊平到顶层"""
        m = FRONTMATTER_RE.match(content)
        if not m:
            return {}
        try:
            parsed = yaml.safe_load(m.group(1))
            if not isinstance(parsed, dict):
                return {}
            flat: dict[str, Any] = {k: v for k, v in parsed.items() if v is not None}
            metadata = flat.pop("metadata", None)
            if isinstance(metadata, dict):
                for key, value in metadata.items():
                    if value is not None:
                        flat.setdefault(key, value)
            return flat
        except yaml.YAMLError:
            return {}

    async def extract_and_maintain(
        self,
        client: LLMClient,
        user_report: str,
        diagnosis: str,
    ) -> MemoryMaintainResult:
        """诊断结束后自动维护记忆库（create / update / delete / skip）。"""
        prompt = EXTRACTION_USER_PROMPT.format(
            existing_manifest=self.list_manifest(),
            user_report=user_report,
            diagnosis=diagnosis,
        )

        conv = ConversationManager()
        conv.add_user(prompt)

        collected = ""
        try:
            async for event in client.stream(conv, system=EXTRACTION_SYSTEM_PROMPT, tools=None):
                if isinstance(event, TextDelta):
                    collected += event.text
                elif isinstance(event, StreamEnd):
                    pass
        except Exception as exc:
            return MemoryMaintainResult(action="error", reason=str(exc))

        decision = _parse_maintenance_response(collected)
        if decision is None:
            return MemoryMaintainResult(action="error", reason="无法解析提取 LLM 响应")

        action = str(decision.get("action", "skip")).strip().lower()
        target = str(decision.get("target", "")).strip().replace(".md", "")
        reason = str(decision.get("reason", "")).strip()
        memory_data = decision.get("memory")

        if action == "skip":
            return MemoryMaintainResult(action="skip", reason=reason)

        if action == "delete":
            if not target:
                return MemoryMaintainResult(action="skip", reason="delete 缺少 target")
            deleted = self.delete_memory(target)
            if deleted:
                return MemoryMaintainResult(action="delete", target=target, reason=reason)
            return MemoryMaintainResult(action="skip", reason=f"未找到记忆: {target}")

        if action in {"create", "update"}:
            if not isinstance(memory_data, dict):
                return MemoryMaintainResult(action="skip", reason=f"{action} 缺少 memory 对象")
            built = _build_memory_markdown(memory_data)
            if built is None:
                return MemoryMaintainResult(action="error", reason="memory 缺少 name 或正文")
            name, content = built
            if action == "update":
                if not target:
                    return MemoryMaintainResult(action="skip", reason="update 缺少 target")
                if target != name:
                    self.delete_memory(target)
            self.write_memory(name, content)
            return MemoryMaintainResult(action=action, target=name, reason=reason)

        return MemoryMaintainResult(action="skip", reason=f"未知 action: {action}")

    def _upsert_index_entry(self, stem: str, filename: str, description: str) -> None:
        index_path = self.memory_dir / INDEX_NAME
        new_line = f"- [{stem}]({filename}) — {description}\n"
        if not index_path.exists():
            index_path.write_text(new_line, encoding="utf-8")
            return

        lines = index_path.read_text(encoding="utf-8").splitlines(keepends=True)
        replaced = False
        for i, line in enumerate(lines):
            if f"[{stem}]" in line or f"({filename})" in line:
                lines[i] = new_line
                replaced = True
                break
        if not replaced:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = lines[-1] + "\n"
            lines.append(new_line)
        index_path.write_text("".join(lines).rstrip("\n") + "\n", encoding="utf-8")

    def _remove_index_entry(self, stem: str, filename: str) -> None:
        index_path = self.memory_dir / INDEX_NAME
        if not index_path.exists():
            return
        lines = index_path.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [
            line for line in lines
            if f"[{stem}]" not in line and f"({filename})" not in line
        ]
        if kept:
            index_path.write_text("".join(kept).rstrip("\n") + "\n", encoding="utf-8")
        else:
            index_path.unlink(missing_ok=True)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60] or "bug-pattern"


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _section(title: str, body: str) -> str:
    text = body.strip()
    if not text:
        return ""
    return f"## {title}\n\n{text}\n"


def _build_memory_markdown(memory_data: dict[str, Any]) -> tuple[str, str] | None:
    """由 LLM 返回的结构化 memory 对象拼成完整 .md 文件。"""
    name = str(memory_data.get("name", "")).strip().replace(".md", "")
    description = str(memory_data.get("description", "")).strip()
    if not name:
        name = _slugify(description) if description else ""
    if not name:
        return None

    symptoms = _as_str_list(memory_data.get("symptoms"))
    key_symbols = _as_str_list(memory_data.get("key_symbols"))
    root_cause = str(memory_data.get("root_cause", "")).strip()
    fix_approach = str(memory_data.get("fix_approach", "")).strip()

    body_parts = [
        _section("症状", str(memory_data.get("symptoms_text", ""))),
        _section("诊断过程", str(memory_data.get("diagnosis_text", ""))),
        _section("修复方向", str(memory_data.get("fix_text", ""))),
    ]
    body = "\n".join(part for part in body_parts if part).strip()
    if not body and description:
        body = f"## 摘要\n\n{description}\n"
    if not body:
        return None

    frontmatter = {
        "name": name,
        "description": description or name,
        "metadata": {
            "type": "bug_pattern",
            "symptoms": symptoms,
            "key_symbols": key_symbols,
            "root_cause": root_cause,
            "fix_approach": fix_approach,
        },
    }
    header = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    content = f"---\n{header}\n---\n\n{body}\n"
    return name, content


def _parse_maintenance_response(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
