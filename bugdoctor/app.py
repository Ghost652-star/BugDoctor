from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bugdoctor.agent.loop import Agent, ErrorEvent, StreamText, ToolResultEvent, ToolUseEvent, TurnComplete
from bugdoctor.config import load_config
from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.client import LLMError, create_client
from bugdoctor.prompts.system import build_system_prompt
from bugdoctor.tools.factory import create_registry


def _preview(text: str, max_len: int = 400) -> str:
    text = text.replace("\r\n", "\n")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


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
    print("Paste an error or describe a bug. Type quit to exit.\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("Bye.")
            break

        print("assistant> ", end="", flush=True)
        async for event in agent.run(user_input):
            if isinstance(event, StreamText):
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolUseEvent):
                print(f"\n[tool] {event.tool_name}({event.arguments})")
            elif isinstance(event, ToolResultEvent):
                tag = "ERROR" if event.is_error else "result"
                print(f"[{tag}] {_preview(event.content)}")
            elif isinstance(event, TurnComplete):
                print("\n")
            elif isinstance(event, ErrorEvent):
                print(f"\n[agent error] {event.message}\n")
