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
from bugdoctor.memory.store import MemoryStore
from bugdoctor.memory.recall import recall_relevant
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
    memory_store = MemoryStore()

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
        has_tool_calls = False
        turn_ok = False

        # ── 检索相关 Bug 模式记忆，注入到消息上下文 ──
        memory_section = await recall_relevant(user_input, memory_store, client)
        if memory_section:
            conversation.add_system_reminder(memory_section)

        async for event in agent.run(user_input):
            if isinstance(event, StreamText):
                pending_stream.append(event.text)

            elif isinstance(event, ToolUseEvent):
                has_tool_calls = True
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
                    _print_final_answer(chunk)
                print()

            elif isinstance(event, ErrorEvent):
                print(f"\n{Style.BOLD}{Style.RED}[错误] {event.message}{Style.RESET}\n")

        if turn_ok:
            new_messages = conversation.history[history_len:]
            session_store.append_messages(active_session_id, new_messages)

            # ── 诊断结束后询问是否记住 ──
            if has_tool_calls:
                try:
                    choice = input(f"\n{Style.YELLOW}记住这次诊断？[y/N]: {Style.RESET}").strip()
                except (EOFError, KeyboardInterrupt):
                    choice = ""
                if choice.lower() == "y":
                    user_reports = [
                        msg.content
                        for msg in conversation.history[history_len:]
                        if msg.role == "user" and msg.content and not msg.tool_results
                    ]
                    diagnosis_texts = [
                        msg.content
                        for msg in conversation.history[history_len:]
                        if msg.role == "assistant" and msg.content and not msg.tool_uses
                    ]
                    user_report = user_reports[0] if user_reports else user_input
                    diagnosis_conclusion = diagnosis_texts[-1] if diagnosis_texts else ""

                    if diagnosis_conclusion:
                        print(f"{Style.THINKING}提取中...{Style.RESET}")
                        name = await memory_store.extract_and_save(client, user_report, diagnosis_conclusion)
                        if name:
                            print(f"{Style.GREEN}已保存到记忆: {name}{Style.RESET}")
                        else:
                            print(f"{Style.THINKING}没有新的模式需要记录。{Style.RESET}")
