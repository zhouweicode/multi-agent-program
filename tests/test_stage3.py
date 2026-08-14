from copy import deepcopy
from langgraph.types import Command
from agents.verification_agent import VerificationAgent
from graph.builder import build_graph
from graph.routing import after_rule_validation, after_verification
from nodes.validator_node import validator_node
from tools.verification_tools import (verify_evidence, check_source, validate_relation,
                                      check_constraints, get_cooperation_timeline)


def _run_semantic_scenario(thread_id: str = "stage3-semantic") -> dict:
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    first = graph.invoke({"question": "判断张伟和李明是不是长期稳定的核心科研合作伙伴。",
                          "max_replans": 2, "replan_count": 0, "task_history": []}, config=config)
    return graph.invoke(Command(resume={"张伟": "person_zw_001", "李明": "person_lm_001"}), config=config)


def test_semantic_scenario_runs_rule_then_verification_and_passes():
    final = _run_semantic_scenario()
    assert final["validation_result"]["valid"] is True
    assert final["verification_result"]["status"] == "PASS"
    assert final["verification_result"]["needs_replan"] is False
    assert "是长期稳定的核心科研合作伙伴" in final["final_answer"]


def test_verification_agent_executes_five_step_tool_loop():
    result = VerificationAgent().run(
        question="判断是否为长期稳定核心科研合作伙伴",
        entity_ids=["person_zw_001", "person_lm_001"],
        evidence_ids=["ev_paper_001", "ev_paper_002", "ev_project_001"],
    )
    expected = ["verify_evidence", "check_source", "get_cooperation_timeline", "validate_relation", "check_constraints"]
    assert [call["name"] for call in result["tool_calls"]] == expected
    assert [item["tool"] for item in result["observations"]] == expected
    assert result["status"] == "PASS"


def test_verification_tools_check_evidence_relation_timeline_and_constraints():
    ids = ["person_zw_001", "person_lm_001"]
    evidence = verify_evidence.invoke({"evidence_ids": ["ev_paper_001", "missing"]})
    source = check_source.invoke({"evidence_ids": ["ev_paper_001", "ev_project_001"]})
    timeline = get_cooperation_timeline.invoke({"entity_ids": ids})
    relation = validate_relation.invoke({"entity_ids": ids, "relation": "CORE_RESEARCH_PARTNER"})
    constraints = check_constraints.invoke({"timeline": timeline, "min_year_span": 2, "min_achievements": 3})
    assert evidence == {"valid": False, "checked_count": 2, "missing": ["missing"]}
    assert source["trusted"] is True
    assert [row["year"] for row in timeline] == [2020, 2021, 2023]
    assert relation["supported"] is True
    assert constraints["satisfied"] is True


def test_rule_validator_rejects_invalid_project_time_and_count():
    state = _run_semantic_scenario("stage3-rule-corruption")
    corrupted = deepcopy(state)
    for fact in corrupted["achievement_result"]["facts"]:
        if fact["tool"] == "get_common_projects":
            fact["data"][0]["start_year"] = 2025
            fact["data"][0]["end_year"] = 2020
        if fact["tool"] == "aggregate_cooperation":
            fact["data"]["common_paper_count"] = 99
    result = validator_node(corrupted)["validation_result"]
    assert result["valid"] is False
    assert any("项目时间范围无效" in error for error in result["errors"])
    assert "共同论文 count 与数据条数不一致" in result["errors"]


def test_replan_routes_respect_max_replans():
    rule_failure = {"validation_result": {"valid": False, "needs_replan": True},
                    "replan_count": 0, "max_replans": 2, "requires_verification": True}
    assert after_rule_validation(rule_failure) == "supervisor"
    rule_failure["replan_count"] = 2
    assert after_rule_validation(rule_failure) == "answer"
    verification_failure = {"verification_result": {"status": "FAIL", "needs_replan": True},
                            "replan_count": 1, "max_replans": 2}
    assert after_verification(verification_failure) == "supervisor"
    verification_failure["replan_count"] = 2
    assert after_verification(verification_failure) == "answer"

