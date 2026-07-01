from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Windows PowerShell / CMD 都需要 colorama 才能正确渲染 ANSI 颜色
import colorama
from colorama import Fore, Style as ColoramaStyle

colorama.init()

from bugdoctor.agent.loop import Agent, ErrorEvent, StreamText, ToolResultEvent, ToolUseEvent, TurnComplete
from bugdoctor.config import load_config
from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.client import LLMError, create_client
from bugdoctor.memory.replay import print_restored_history
from bugdoctor.memory.session import SessionStore, choose_session_interactive
from bugdoctor.prompts.system import build_system_prompt
from bugdoctor.tools.factory import create_registry


# ── 终端颜色（colorama 转换后跨平台可用） ────────────────

class Style:
    RESET = ColoramaStyle.RESET_ALL
    BOLD = ColoramaStyle.BRIGHT
    # DIM(\033[2m) 在 Windows PowerShell 里常无效；用浅灰前景色代替
    THINKING = Fore.LIGHTBLACK_EX
    CYAN = Fore.CYAN
    BLUE = Fore.BLUE
    YELLOW = Fore.YELLOW
    GREEN = Fore.GREEN
    RED = Fore.RED


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


def _print_final_answer(text: str) -> None:
    """正式回答：绿色分隔线 + 高亮正文（无工具 / 有工具最后一轮统一格式）。"""
    print(f"\n{Style.BOLD}{Style.GREEN}{'─' * 60}{Style.RESET}")
    print(f"{Style.BOLD}{text}{Style.RESET}")
    print(f"{Style.BOLD}{Style.GREEN}{'─' * 60}{Style.RESET}")


async def run_app(
    project: Path,
    config_path: Path | None = None,
    *,
    new_session: bool = False,
    session_id: str | None = None,
) -> None:
    config = load_config(project, config_path)

    try:
        client = create_client(config.llm)
    except LLMError as exc:
        print(f"配置错误: {exc}")
        print("请在 bugdoctor/config.yaml 中设置 llm.api_key，或设置环境变量 BUGDOCTOR_API_KEY")
        return

    registry, read_tracker = create_registry(config.project_root)
    session_store = SessionStore(config.project_root)

    if session_id:
        if not session_store.exists(session_id):
            print(f"未找到会话: {session_id}")
            return
        history = session_store.load_history(session_id)
        active_session_id = session_id
        print(f"已恢复对话 {session_id}（{len(history)} 条消息）\n")
    elif new_session:
        active_session_id = session_store.create()
        history = []
        print(f"已创建新对话: {active_session_id}\n")
    else:
        active_session_id, history = choose_session_interactive(session_store)

    conversation = ConversationManager(history=history)
    read_tracker.restore_from_history(history, config.project_root)

    if history:
        print_restored_history(history)

    system_prompt = build_system_prompt(str(config.project_root))
    agent = Agent(
        client=client,
        registry=registry,
        conversation=conversation,
        system_prompt=system_prompt,
        max_iterations=config.max_agent_iterations,
    )

    print(f"BugDoctor — model: {config.llm.model}")
    print(f"Workspace: {config.project_root}")
    print(f"Session: {active_session_id}")
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

        history_len = len(conversation.history)
        pending_stream: list[str] = []
        turn_ok = False

        async for event in agent.run(user_input):
            if isinstance(event, StreamText):
                # 先缓冲，等知道本轮要不要调工具再决定怎么展示（避免无工具时重复打印两遍）
                pending_stream.append(event.text)

            elif isinstance(event, ToolUseEvent):
                if pending_stream:
                    print(f"{Style.THINKING}{''.join(pending_stream)}{Style.RESET}", end="", flush=True)
                    pending_stream.clear()
                args_preview = _preview(str(event.arguments), 150)
                print(f"\n{Style.CYAN}  🔧 {event.tool_name}{Style.RESET} "
                      f"{Style.THINKING}{args_preview}{Style.RESET}")

            elif isinstance(event, ToolResultEvent):
                tag = "✗" if event.is_error else "✓"
                color = Style.RED if event.is_error else Style.BLUE
                preview = _preview(event.content).replace('\n', '\n    ')
                print(f"{color}  {tag} {preview}{Style.RESET}")

            elif isinstance(event, TurnComplete):
                turn_ok = True
                chunk = "".join(pending_stream)
                pending_stream.clear()
                if chunk.strip():
                    # TurnComplete = 本轮结束；无论是否调过工具，剩余文字都是正式回答
                    _print_final_answer(chunk)
                print()

            elif isinstance(event, ErrorEvent):
                print(f"\n{Style.BOLD}{Style.RED}[错误] {event.message}{Style.RESET}\n")

        if turn_ok:
            new_messages = conversation.history[history_len:]
            session_store.append_messages(active_session_id, new_messages)
