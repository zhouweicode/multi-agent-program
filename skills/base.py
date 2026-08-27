"""运行时 Skill 的最小协议。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillSpec:
    """Skill 只声明业务方法和能力，不持有 Agent 或 Tool 权限。"""

    skill_id: str
    version: str
    name: str
    description: str
    trigger_phrases: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    optional_capabilities: tuple[str, ...] = ()
    output_schema: str = ""
    instruction_path: Path | None = None
    default_input: dict[str, Any] = field(default_factory=dict)

    def matches(self, question: str) -> bool:
        return any(phrase in question for phrase in self.trigger_phrases)

    def load_instructions(self) -> str:
        """按需加载完整 SOP，避免 Router 阶段把大段说明注入上下文。"""
        if self.instruction_path is None:
            return ""
        return self.instruction_path.read_text(encoding="utf-8")
