"""可信仓库内运行时 Skill 的结构化协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class SkillEvaluationPolicy:
    dataset_path: Path
    baseline_path: Path
    runner_ref: str
    gate_ref: str


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
    enabled: bool = True
    input_schema: type[BaseModel] | None = None
    output_schema: type[BaseModel] | None = None
    input_schema_ref: str = ""
    output_schema_ref: str = ""
    instruction_path: Path | None = None
    instruction_body: str = ""
    content_hash: str = ""
    evaluation: SkillEvaluationPolicy | None = None
    default_input: dict[str, Any] = field(default_factory=dict)

    def matches(self, question: str) -> bool:
        return any(phrase in question for phrase in self.trigger_phrases)

    def load_instructions(self) -> str:
        """按需加载完整 SOP，避免 Router 阶段把大段说明注入上下文。"""
        if self.instruction_body:
            return self.instruction_body
        if self.instruction_path is None:
            return ""
        return self.instruction_path.read_text(encoding="utf-8")

    def validate_input(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.input_schema is None:
            return dict(value)
        return self.input_schema.model_validate(value).model_dump()

    def validate_output(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.output_schema is None:
            return dict(value)
        return self.output_schema.model_validate(value).model_dump()
