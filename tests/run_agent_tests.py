"""Test harness: run Agent against a sample project with given input."""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bugdoctor.agent.loop import Agent, StreamText, ToolUseEvent, ToolResultEvent, TurnComplete, ErrorEvent
from bugdoctor.config import load_config
from bugdoctor.conversation.manager import ConversationManager
from bugdoctor.llm.client import create_client
from bugdoctor.prompts.system import build_system_prompt
from bugdoctor.tools.factory import create_registry


async def run_test(project_path: str, bug_description: str) -> None:
    project = Path(project_path).resolve()
    config = load_config(project)
    client = create_client(config.llm)
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

    print(f"{'='*60}")
    print(f"Project: {project.name}")
    print(f"Tools: {', '.join(registry.list_names())}")
    print(f"{'='*60}")

    async for event in agent.run(bug_description):
        if isinstance(event, StreamText):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolUseEvent):
            print(f"\n  [TOOL] {event.tool_name}({event.arguments})")
        elif isinstance(event, ToolResultEvent):
            tag = "ERR" if event.is_error else "OK"
            preview = event.content[:300].replace('\n', '\n  ')
            print(f"  [{tag}] {preview}")
        elif isinstance(event, TurnComplete):
            print("\n--- turn complete ---")
        elif isinstance(event, ErrorEvent):
            print(f"\n  [AGENT ERROR] {event.message}")

    print(f"\n{'='*60}\n")


def main():
    samples_dir = Path(__file__).resolve().parent.parent / "samples"
    tests = [
        ("demo_data_pipeline", (
            "Traceback (most recent call last):\n"
            '  File "main.py", line 43, in <module>\n'
            "    run_pipeline(csv_file)\n"
            '  File "main.py", line 30, in run_pipeline\n'
            "    transformed = transform_records(raw_records, multiplier=2.0)\n"
            '  File "pipeline/transformer.py", line 303, in transform_records\n'
            "    classified = [_apply_scale_factor(rec, multiplier) for rec in classified]\n"
            '  File "pipeline/transformer.py", line 221, in _apply_scale_factor\n'
            "    scaled = raw_value * factor * weight\n"
            "TypeError: can't multiply sequence by non-int of type 'float'"
        )),
    ]

    for name, bug_text in tests:
        project = samples_dir / name
        if not project.is_dir():
            print(f"SKIP: {project} not found")
            continue
        asyncio.run(run_test(str(project), bug_text))


if __name__ == "__main__":
    main()
