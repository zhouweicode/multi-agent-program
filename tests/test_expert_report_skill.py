from langgraph.types import Command

from app.main import app
from fastapi.testclient import TestClient
from graph.builder import build_graph
from nodes.router_node import router_node
from nodes.validator_node import validator_node
from skills.expert_report.spec import normalize_expert_report_input
from skills.registry import skill_registry


def _run_report(question: str, thread_id: str, **initial):
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    first = graph.invoke({
        "question": question,
        "max_replans": 2,
        "replan_count": 0,
        "web_search_enabled": False,
        "task_history": [],
        **initial,
    }, config=config)
    assert first["__interrupt__"][0].value["status"] == "NEED_USER_SELECTION"
    return graph.invoke(Command(resume={"张伟": "person_zw_001"}), config=config)


def test_registry_detects_and_progressively_loads_expert_report_skill():
    spec = skill_registry.detect("请生成张伟的专家报告")
    assert spec is not None
    assert spec.skill_id == "expert_report"
    assert "expert_profile_history" in spec.required_capabilities
    instructions = spec.load_instructions()
    assert "Skill 不直接调用 Agent 或 Tool" in instructions
    assert "ReportClaim" in instructions


def test_router_selects_skill_and_normalizes_report_options():
    result = router_node({
        "question": "给张伟生成面向企业的简版专家报告，展示前3项",
        "web_search_enabled": False,
    })
    assert result["requested_skill"] == "expert_report"
    assert result["complexity"] == "complex"
    assert result["primary_domain"] == "talent"
    assert result["skill_input"] == {
        "report_type": "brief",
        "audience": "enterprise",
        "include_enterprise": False,
        "include_cooperation_network": False,
        "include_web": False,
        "top_n": 3,
    }


def test_comprehensive_report_runs_existing_agents_and_binds_every_claim_to_evidence():
    final = _run_report("请生成张伟的完整专家报告", "expert-report-comprehensive")
    assert final["requested_skill"] == "expert_report"
    assert [task["agent"] for task in final["tasks"]] == [
        "talent_agent", "achievement_agent", "enterprise_agent", "graph_reasoning_agent",
    ]
    assert final["validation_result"]["valid"] is True
    report = final["report_draft"]
    assert [section["section_id"] for section in report["sections"]] == [
        "profile_history", "research_achievements", "enterprise_relations", "cooperation_network",
    ]
    catalog_ids = {item["evidence_id"] for item in report["evidence_catalog"]}
    claims = [claim for section in report["sections"] for claim in section["claims"]]
    assert claims
    assert all(claim["evidence_ids"] and set(claim["evidence_ids"]) <= catalog_ids for claim in claims)
    assert report["evidence_coverage"] == 1.0
    assert "# 张伟专家报告" in final["final_answer"]
    assert "## 证据目录" in final["final_answer"]
    assert "get_person_profile" not in final["final_answer"]


def test_brief_report_only_requests_core_capabilities():
    final = _run_report("请生成张伟的简版专家报告", "expert-report-brief")
    assert [task["agent"] for task in final["tasks"]] == ["talent_agent", "achievement_agent"]
    assert [section["section_id"] for section in final["report_draft"]["sections"]] == [
        "profile_history", "research_achievements",
    ]


def test_optional_skill_domain_error_is_warning_and_does_not_block_report():
    result = validator_node({
        "requested_skill": "expert_report",
        "skill_required_domains": ["talent", "achievement"],
        "complexity": "complex",
        "resolved_entities": {"张伟": "person_zw_001"},
        "tasks": [{
            "task_id": "skill_expert_report_graph",
            "agent": "graph_reasoning_agent",
            "required_fact_types": ["neighbors"],
            "required_entity_ids": ["person_zw_001"],
        }],
        "graph_result": {
            "agent": "graph_reasoning_agent", "facts": [], "evidence": [],
            "errors": ["[TOOL_TIMEOUT] graph backend timed out"],
        },
        "evidence": [],
    })["validation_result"]
    assert result["valid"] is True
    assert result["needs_replan"] is False
    assert result["errors"] == []
    assert any("可选领域 graph" in warning for warning in result["warnings"])


def test_explicit_api_skill_and_skill_catalog_contract():
    client = TestClient(app)
    catalog = client.get("/skills")
    assert catalog.status_code == 200
    assert catalog.json()["skills"][0]["skill_id"] == "expert_report"
    assert normalize_expert_report_input("普通查询", {"top_n": 200})["top_n"] == 50
