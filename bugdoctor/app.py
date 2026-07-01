from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Windows PowerShell / CMD 都需要 colorama 才能正确渲染 ANSI 颜色
import colorama
colorama.init()

from bugdoctor.agent.loop import Agent, ErrorEvent, StreamText, ToolResultEvent, ToolUseEvent, TurnComplete
from bugdoctor.config import load_config
from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.client import LLMError, create_client
from bugdoctor.prompts.system import build_system_prompt
from bugdoctor.tools.factory import create_registry


# ── 终端颜色（colorama 转换后跨平台可用） ────────────────

class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"


def _preview(text: str, max_len: int = 300) -> str:
    text = text.replace("\r\n", "\n")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _read_user_input() -> str:
    """Read one user message; supports multi-line paste (empty line to send)."""
    lines: list[str] = []
    while True:
        prefix = "you> " if not lines else "... "
        try:
            line = input(prefix)
        except EOFError:
            break
        if line.strip() == "" and lines:
            break
        if line.strip() == "" and not lines:
            continue
        lines.append(line.rstrip("\r\n"))
        if len(lines) == 1 and lines[0].strip().lower() in {"quit", "exit", "q"}:
            break
    return "\n".join(lines).strip()


async def run_app(project: Path, config_path: Path | None = None) -> None:
    config = load_config(project, config_path)

    try:
        client = create_client(config.llm)
    except LLMError as exc:
        print(f"配置错误: {exc}")
        print("请在 bugdoctor/config.yaml 中设置 llm.api_key，或设置环境变量 BUGDOCTOR_API_KEY")
        return

    registry = create_registry(config.project_root)
    conversation = ConversationManager()
    system_prompt = build_system_prompt(str(config.project_root))
    agent = Agent(
        client=client,
        registry=registry,
        conversation=conversation,
        system_prompt=system_prompt,
        max_iterations=config.max_agent_iterations,
    )

    print(f"BugDoctor — model: {config.llm.model}")
    print(f"Project: {config.project_root}")
    print(f"Tools: {', '.join(registry.list_names())}")
    print("粘贴错误信息或描述 bug，空行发送，输入 quit 退出。\n")

    while True:
        try:
            user_input = _read_user_input()
        except KeyboardInterrupt:
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("Bye.")
            break

        buffered_text: list[str] = []
        has_tool_calls = False

        async for event in agent.run(user_input):
            if isinstance(event, StreamText):
                buffered_text.append(event.text)
                # 思考过程：暗色，不抢眼
                print(f"{Style.DIM}{event.text}{Style.RESET}", end="", flush=True)

            elif isinstance(event, ToolUseEvent):
                has_tool_calls = True
                args_preview = _preview(str(event.arguments), 150)
                print(f"\n{Style.CYAN}  🔧 {event.tool_name}{Style.RESET} "
                      f"{Style.DIM}{args_preview}{Style.RESET}")

            elif isinstance(event, ToolResultEvent):
                tag = "✗" if event.is_error else "✓"
                color = Style.RED if event.is_error else Style.BLUE
                preview = _preview(event.content).replace('\n', '\n    ')
                print(f"{color}  {tag} {preview}{Style.RESET}")

            elif isinstance(event, TurnComplete):
                if not has_tool_calls:
                    # 最终回答：绿色加粗框出来
                    full_text = "".join(buffered_text)
                    if full_text.strip():
                        print(f"\n\n{Style.BOLD}{Style.GREEN}{'─' * 60}{Style.RESET}")
                        print(f"{Style.BOLD}{full_text}{Style.RESET}")
                        print(f"{Style.BOLD}{Style.GREEN}{'─' * 60}{Style.RESET}")
                print()

            elif isinstance(event, ErrorEvent):
                print(f"\n{Style.BOLD}{Style.RED}[错误] {event.message}{Style.RESET}\n")
