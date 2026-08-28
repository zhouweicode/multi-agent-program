import json
from pathlib import Path

import pytest

from skills.loader import load_skill_spec, parse_frontmatter
from skills.registry import SkillGateError, SkillRegistry, skill_registry


def test_trusted_skill_discovery_validates_frontmatter_and_schemas():
    items = {item["skill_id"]: item for item in skill_registry.list()}
    assert set(items) == {"expert_report", "industry_landscape"}
    expert = skill_registry.get("expert_report")
    assert expert.version == "1.1.0"
    assert len(expert.content_hash) == 64
    assert expert.input_schema_ref.endswith(":ExpertReportInput")
    assert expert.output_schema_ref.endswith(":ExpertReport")
    assert expert.validate_input(expert.default_input)["top_n"] == 10
    assert expert.evaluation is not None


def test_skill_body_excludes_frontmatter_from_model_instructions():
    expert = skill_registry.get("expert_report")
    instructions = expert.load_instructions()
    assert instructions.startswith("# 专家报告 Skill")
    assert "input_schema:" not in instructions
    assert "Skill 不直接调用 Agent 或 Tool" in instructions


def test_skill_loader_rejects_unknown_capability(tmp_path: Path):
    source = Path("skills/expert_report/SKILL.md").read_text(encoding="utf-8")
    source = source.replace("expert_profile_history", "unknown_capability", 1)
    folder = tmp_path / "expert_report"
    folder.mkdir()
    path = folder / "SKILL.md"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="未知 Capability"):
        load_skill_spec(path)


def test_skill_enable_override_is_persisted_and_runtime_enforced(tmp_path: Path):
    specs = tuple(
        load_skill_spec(path) for path in sorted(Path("skills").glob("*/SKILL.md"))
    )
    overrides = tmp_path / "skills.json"
    registry = SkillRegistry(specs, overrides_path=overrides)
    updated = registry.set_enabled("expert_report", False)
    assert updated.enabled is False
    assert registry.detect("请生成专家报告") is None
    with pytest.raises(ValueError, match="已停用"):
        registry.get("expert_report")
    assert (
        json.loads(overrides.read_text(encoding="utf-8"))["enabled"]["expert_report"]
        is False
    )
    reloaded = SkillRegistry(specs, overrides_path=overrides)
    assert reloaded.get("expert_report", include_disabled=True).enabled is False


def test_skill_evaluation_gate_is_declared_and_passes():
    result = skill_registry.evaluate("expert_report")
    assert result["gate"] == {"passed": True, "failures": []}


def test_skill_cannot_be_enabled_when_evaluation_gate_fails(
    tmp_path: Path, monkeypatch
):
    specs = tuple(
        load_skill_spec(path) for path in sorted(Path("skills").glob("*/SKILL.md"))
    )
    registry = SkillRegistry(specs, overrides_path=tmp_path / "skills.json")
    registry.set_enabled("expert_report", False)
    monkeypatch.setattr(
        "skills.registry.run_skill_evaluation_gate",
        lambda _spec: {"gate": {"passed": False, "failures": ["regression"]}},
    )
    with pytest.raises(SkillGateError, match="regression"):
        registry.set_enabled("expert_report", True)


def test_frontmatter_is_required():
    with pytest.raises(ValueError, match="Frontmatter"):
        parse_frontmatter("# plain markdown")
