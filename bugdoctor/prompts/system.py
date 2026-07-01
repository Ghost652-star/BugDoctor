"""System prompt 公开接口"""

from __future__ import annotations

from bugdoctor.prompts.builder import PromptBuilder, PromptSection
from bugdoctor.prompts.sections import DIAGNOSIS_RULES, IDENTITY, OUTPUT_STYLE, environment_section


def build_system_prompt(
    project_root: str,
    skill_section: str = "",
    memory_section: str = "",
) -> str:
    """构建 system prompt。
    """
    b = PromptBuilder()
    b.add(IDENTITY)
    b.add(DIAGNOSIS_RULES)
    b.add(OUTPUT_STYLE)
    b.add(environment_section(project_root))

    if skill_section:
        b.add(PromptSection(name="Skills", priority=90, content=skill_section))

    if memory_section:
        b.add(PromptSection(name="Memory", priority=95, content=memory_section))

    return b.build()
