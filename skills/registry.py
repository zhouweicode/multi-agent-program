"""Skill 注册、发现与按需加载。"""
from __future__ import annotations

from skills.base import SkillSpec
from skills.expert_report.spec import EXPERT_REPORT_SPEC
from skills.industry_landscape.spec import INDUSTRY_LANDSCAPE_SPEC


class SkillRegistry:
    def __init__(self, specs: tuple[SkillSpec, ...] | None = None):
        items = specs or (EXPERT_REPORT_SPEC, INDUSTRY_LANDSCAPE_SPEC)
        self._specs = {item.skill_id: item for item in items}

    def get(self, skill_id: str) -> SkillSpec:
        try:
            return self._specs[skill_id]
        except KeyError as exc:
            raise ValueError(f"未知 Skill: {skill_id}") from exc

    def detect(self, question: str) -> SkillSpec | None:
        return next((item for item in self._specs.values() if item.matches(question)), None)

    def list(self) -> list[dict[str, object]]:
        return [
            {
                "skill_id": item.skill_id,
                "version": item.version,
                "name": item.name,
                "description": item.description,
                "required_capabilities": list(item.required_capabilities),
                "optional_capabilities": list(item.optional_capabilities),
            }
            for item in self._specs.values()
        ]


skill_registry = SkillRegistry()
