"""Terminal replay of restored session history."""

from __future__ import annotations

from colorama import Fore, Style as ColoramaStyle

from bugdoctor.conversation.models import Message

RESET = ColoramaStyle.RESET_ALL
BOLD = ColoramaStyle.BRIGHT
DIM = Fore.LIGHTBLACK_EX
CYAN = Fore.CYAN
BLUE = Fore.BLUE
RED = Fore.RED


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\r\n", "\n").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def print_restored_history(
    history: list[Message],
    *,
    content_max_len: int = 2000,
) -> None:
    """Replay saved messages: full user/assistant text; tool calls as names only."""
    if not history:
        return

    print(f"{DIM}{'─' * 60}{RESET}")
    print(f"{DIM}  历史对话回放{RESET}")
    print(f"{DIM}{'─' * 60}{RESET}\n")

    pending_tool_names: list[str] = []

    for msg in history:
        if msg.role == "user" and msg.tool_results:
            parts: list[str] = []
            for i, result in enumerate(msg.tool_results):
                name = pending_tool_names[i] if i < len(pending_tool_names) else "tool"
                tag = "✗" if result.is_error else "✓"
                color = RED if result.is_error else BLUE
                parts.append(f"{color}{tag} {name}{RESET}")
            if parts:
                print(f"  {'  '.join(parts)}")
            pending_tool_names = []
            continue

        if msg.role == "user":
            if not msg.content or msg.content.startswith("[system]"):
                continue
            print(f"{BOLD}you>{RESET} {_truncate(msg.content, content_max_len)}")
            continue

        if msg.role == "assistant":
            pending_tool_names = [tu.tool_name for tu in msg.tool_uses]
            if msg.content.strip():
                print(_truncate(msg.content, content_max_len))
            if msg.tool_uses:
                names = ", ".join(tu.tool_name for tu in msg.tool_uses)
                print(f"  {CYAN}🔧 {names}{RESET}")
            print()

    print(f"{DIM}{'─' * 60}{RESET}\n")
