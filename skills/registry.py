"""可信运行时 Skill 的发现、启停、版本记录与评测门禁。"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from skills.base import SkillSpec
from skills.loader import discover_skill_specs, run_skill_evaluation_gate


class SkillGateError(RuntimeError):
    """Skill 启用前的离线评测未达到基线。"""


class SkillRegistry:
    def __init__(
        self,
        specs: tuple[SkillSpec, ...] | None = None,
        *,
        root: str | Path | None = None,
        overrides_path: str | Path | None = None,
    ):
        skill_root = Path(root or Path(__file__).resolve().parent)
        items = specs or discover_skill_specs(skill_root)
        if len({item.skill_id for item in items}) != len(items):
            raise ValueError("Skill id 必须唯一")
        self._overrides_path = Path(
            overrides_path or os.getenv("SKILL_CONFIG_PATH", ".runtime/skills.json")
        )
        overrides = self._load_overrides()
        self._specs = {
            item.skill_id: replace(
                item, enabled=overrides.get(item.skill_id, item.enabled)
            )
            for item in items
        }

    def _load_overrides(self) -> dict[str, bool]:
        if not self._overrides_path.is_file():
            return {}
        value = json.loads(self._overrides_path.read_text(encoding="utf-8"))
        enabled = value.get("enabled", {}) if isinstance(value, dict) else {}
        if not isinstance(enabled, dict) or any(
            not isinstance(name, str) or not isinstance(flag, bool)
            for name, flag in enabled.items()
        ):
            raise ValueError("Skill 启停配置必须是 enabled: {skill_id: bool}")
        return enabled

    def get(self, skill_id: str, *, include_disabled: bool = False) -> SkillSpec:
        try:
            spec = self._specs[skill_id]
        except KeyError as exc:
            raise ValueError(f"未知 Skill: {skill_id}") from exc
        if not include_disabled and not spec.enabled:
            raise ValueError(f"Skill 已停用: {skill_id}")
        return spec

    def detect(self, question: str) -> SkillSpec | None:
        return next(
            (
                item
                for item in self._specs.values()
                if item.enabled and item.matches(question)
            ),
            None,
        )

    def set_enabled(self, skill_id: str, enabled: bool) -> SkillSpec:
        current = self.get(skill_id, include_disabled=True)
        if enabled and not current.enabled:
            evaluation = run_skill_evaluation_gate(current)
            gate = evaluation.get("gate", {})
            if not gate.get("passed"):
                failures = "; ".join(gate.get("failures", [])) or "未知原因"
                raise SkillGateError(f"Skill {skill_id} 评测门禁未通过: {failures}")
        updated = replace(current, enabled=bool(enabled))
        self._specs[skill_id] = updated
        values = {"enabled": {name: item.enabled for name, item in self._specs.items()}}
        self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._overrides_path.with_suffix(
            self._overrides_path.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self._overrides_path)
        return updated

    def evaluate(self, skill_id: str) -> dict[str, object]:
        return run_skill_evaluation_gate(self.get(skill_id))

    def list(self) -> list[dict[str, object]]:
        return [
            {
                "skill_id": item.skill_id,
                "version": item.version,
                "content_hash": item.content_hash,
                "name": item.name,
                "description": item.description,
                "enabled": item.enabled,
                "input_schema": item.input_schema_ref,
                "output_schema": item.output_schema_ref,
                "required_capabilities": list(item.required_capabilities),
                "optional_capabilities": list(item.optional_capabilities),
                "evaluation": (
                    {
                        "dataset": str(item.evaluation.dataset_path),
                        "baseline": str(item.evaluation.baseline_path),
                    }
                    if item.evaluation
                    else None
                ),
            }
            for item in self._specs.values()
        ]


skill_registry = SkillRegistry()
