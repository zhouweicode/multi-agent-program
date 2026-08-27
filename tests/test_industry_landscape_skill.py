from graph.builder import build_graph
from nodes.router_node import router_node
from nodes.validator_node import validator_node
from skills.industry_landscape.spec import normalize_industry_landscape_input
from skills.registry import skill_registry


def _run_landscape(question: str, thread_id: str, **initial):
    return build_graph().invoke({
        "question": question,
        "max_replans": 2,
        "replan_count": 0,
        "web_search_enabled": False,
        "task_history": [],
        **initial,
    }, config={"configurable": {"thread_id": thread_id}})


def test_registry_detects_industry_landscape_and_loads_sop():
    spec = skill_registry.detect("请生成人工智能产业全景报告")
    assert spec is not None
    assert spec.skill_id == "industry_landscape"
    assert spec.required_capabilities == ("industry_landscape_core",)
    assert "Skill 不直接调用 Agent 或 Tool" in spec.load_instructions()


def test_router_selects_industry_skill_and_normalizes_options():
    result = router_node({
        "question": "请生成人工智能产业面向投资人的简版产业全景报告，展示TOP3",
        "web_search_enabled": False,
    })
    assert result["requested_skill"] == "industry_landscape"
    assert result["primary_domain"] == "industry"
    assert result["complexity"] == "complex"
    assert result["entity_mentions"] == []
    assert result["skill_input"] == {
        "report_type": "brief",
        "audience": "investment",
        "industry_query": "人工智能",
        "include_web": False,
        "top_n_companies": 3,
        "top_n_events": 3,
    }


def test_industry_landscape_runs_industry_agent_and_binds_all_claims():
    final = _run_landscape("请生成人工智能产业全景报告", "industry-landscape-comprehensive")
    assert final.get("__interrupt__") is None
    assert final["requested_skill"] == "industry_landscape"
    assert [task["agent"] for task in final["tasks"]] == ["industry_agent"]
    assert [fact["tool"] for fact in final["industry_result"]["facts"]] == [
        "search_industry_segments", "get_chain_structure", "get_node_companies",
        "get_node_events", "rank_top_events",
    ]
    assert final["validation_result"]["valid"] is True
    report = final["report_draft"]
    assert report["skill_id"] == "industry_landscape"
    assert report["industry_id"] == "chain_ai"
    assert [section["section_id"] for section in report["sections"]] == [
        "industry_scope", "chain_structure", "company_landscape", "key_events",
    ]
    catalog_ids = {item["evidence_id"] for item in report["evidence_catalog"]}
    claims = [claim for section in report["sections"] for claim in section["claims"]]
    assert claims
    assert all(claim["evidence_ids"] and set(claim["evidence_ids"]) <= catalog_ids for claim in claims)
    assert report["evidence_coverage"] == 1.0
    assert final["final_answer"].startswith("# 人工智能产业链全景报告")
    assert "## 证据目录" in final["final_answer"]


def test_explicit_industry_skill_works_without_trigger_phrase():
    result = router_node({
        "question": "分析人工智能产业",
        "requested_skill": "industry_landscape",
        "skill_input": {"report_type": "brief", "industry_query": "人工智能"},
        "web_search_enabled": False,
    })
    assert result["requested_skill"] == "industry_landscape"
    assert result["skill_input"]["industry_query"] == "人工智能"
    assert result["skill_input"]["report_type"] == "brief"


def test_optional_web_failure_degrades_without_blocking_industry_report():
    result = validator_node({
        "requested_skill": "industry_landscape",
        "skill_required_domains": ["industry"],
        "complexity": "complex",
        "resolved_entities": {},
        "tasks": [{
            "task_id": "skill_industry_landscape_web", "agent": "web_research_agent",
            "required_fact_types": ["web_sources"], "required_entity_ids": [],
        }],
        "industry_result": {"agent": "industry_agent", "errors": [], "facts": [
            {"tool": "search_industry_segments", "data": [{"segment_id": "node_model"}]},
            {"tool": "get_chain_structure", "data": {"chain_id": "chain_ai", "node_details": []}},
        ]},
        "web_result": {"agent": "web_research_agent", "facts": [], "evidence": [],
                       "errors": ["[TOOL_TIMEOUT] injected web timeout"]},
        "evidence": [],
    })["validation_result"]
    assert result["valid"] is True
    assert result["needs_replan"] is False
    assert result["errors"] == []
    assert any("可选领域 web" in warning for warning in result["warnings"])


def test_industry_input_limits_are_bounded():
    result = normalize_industry_landscape_input("人工智能产业全景报告", {
        "top_n_companies": 200, "top_n_events": 0,
    })
    assert result["industry_query"] == "人工智能"
    assert result["top_n_companies"] == 50
    assert result["top_n_events"] == 1
