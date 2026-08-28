"""只发现仓库内可信 Skill；不安装归档，也不执行未声明代码。"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from skills.base import SkillEvaluationPolicy, SkillSpec
from tools.registry import ToolRegistry, tool_registry

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md 必须以 YAML Frontmatter 开头")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("SKILL.md Frontmatter 缺少结束分隔符")
    metadata = yaml.safe_load(text[4:end])
    if not isinstance(metadata, dict):
        raise TypeError("SKILL.md Frontmatter 必须是对象")
    return metadata, text[end + 5 :].lstrip("\n")


def _import_ref(reference: str, expected_type: type | None = None) -> Any:
    if ":" not in reference:
        raise ValueError(f"引用必须使用 module:attribute 格式: {reference}")
    module_name, attribute = reference.split(":", 1)
    if not module_name.startswith(("skills.", "evaluation.")):
        raise ValueError(f"Skill 只能引用可信 skills/evaluation 模块: {reference}")
    value = getattr(importlib.import_module(module_name), attribute)
    if expected_type is not None and (
        not isinstance(value, type) or not issubclass(value, expected_type)
    ):
        raise TypeError(f"引用不是 {expected_type.__name__} 子类: {reference}")
    return value


def _strings(
    metadata: dict[str, Any], name: str, *, required: bool = False
) -> tuple[str, ...]:
    value = metadata.get(name, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"Skill 字段 {name} 必须是非空字符串数组")
    if required and not value:
        raise ValueError(f"Skill 字段 {name} 不能为空")
    return tuple(value)


def _evaluation_policy(metadata: dict[str, Any]) -> SkillEvaluationPolicy:
    value = metadata.get("evaluation")
    if not isinstance(value, dict):
        raise TypeError("Skill 必须声明 evaluation 评测门禁")
    required = ("dataset", "baseline", "runner", "gate")
    if any(
        not isinstance(value.get(name), str) or not value[name] for name in required
    ):
        raise ValueError(f"Skill evaluation 必须声明 {', '.join(required)}")
    dataset = (PROJECT_ROOT / value["dataset"]).resolve()
    baseline = (PROJECT_ROOT / value["baseline"]).resolve()
    for path, label in ((dataset, "dataset"), (baseline, "baseline")):
        if PROJECT_ROOT not in path.parents or not path.is_file():
            raise ValueError(f"Skill evaluation {label} 不存在或越界: {path}")
    return SkillEvaluationPolicy(dataset, baseline, value["runner"], value["gate"])


def load_skill_spec(
    path: str | Path,
    *,
    registry: ToolRegistry = tool_registry,
) -> SkillSpec:
    skill_path = Path(path).resolve()
    text = skill_path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(text)
    skill_id = metadata.get("id")
    if not isinstance(skill_id, str) or not skill_id:
        raise ValueError("Skill id 不能为空")
    if skill_path.parent.name != skill_id:
        raise ValueError(
            f"Skill id {skill_id} 必须与目录名 {skill_path.parent.name} 一致"
        )
    for name in ("version", "name", "description", "input_schema", "output_schema"):
        if not isinstance(metadata.get(name), str) or not metadata[name]:
            raise ValueError(f"Skill 字段 {name} 不能为空")
    required_capabilities = _strings(metadata, "required_capabilities", required=True)
    optional_capabilities = _strings(metadata, "optional_capabilities")
    for capability in required_capabilities + optional_capabilities:
        registry.get_capability(capability)
    input_schema_ref = metadata["input_schema"]
    output_schema_ref = metadata["output_schema"]
    default_input = metadata.get("default_input", {})
    if not isinstance(default_input, dict):
        raise TypeError("Skill default_input 必须是对象")
    input_schema = _import_ref(input_schema_ref, BaseModel)
    output_schema = _import_ref(output_schema_ref, BaseModel)
    validated_defaults = input_schema.model_validate(default_input).model_dump()
    return SkillSpec(
        skill_id=skill_id,
        version=metadata["version"],
        name=metadata["name"],
        description=metadata["description"],
        trigger_phrases=_strings(metadata, "trigger_phrases", required=True),
        required_capabilities=required_capabilities,
        optional_capabilities=optional_capabilities,
        enabled=bool(metadata.get("enabled", True)),
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_ref=input_schema_ref,
        output_schema_ref=output_schema_ref,
        instruction_path=skill_path,
        instruction_body=body,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        evaluation=_evaluation_policy(metadata),
        default_input=validated_defaults,
    )


def discover_skill_specs(root: str | Path) -> tuple[SkillSpec, ...]:
    root_path = Path(root)
    paths = sorted(root_path.glob("*/SKILL.md"))
    if not paths:
        raise ValueError(f"没有发现可信 Skill: {root_path}")
    return tuple(load_skill_spec(path) for path in paths)


def run_skill_evaluation_gate(spec: SkillSpec) -> dict[str, Any]:
    if spec.evaluation is None:
        raise ValueError(f"Skill {spec.skill_id} 未配置评测门禁")
    runner: Callable[[str | Path], dict[str, Any]] = _import_ref(
        spec.evaluation.runner_ref
    )
    gate: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] = _import_ref(
        spec.evaluation.gate_ref
    )
    report = runner(spec.evaluation.dataset_path)
    baseline = json.loads(spec.evaluation.baseline_path.read_text(encoding="utf-8"))
    result = gate(report, baseline)
    return {"skill_id": spec.skill_id, "report": report, "gate": result}
