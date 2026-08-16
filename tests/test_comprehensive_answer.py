from langgraph.types import Command

from graph.builder import build_graph
from nodes.validator_node import validator_node


def test_comprehensive_cooperation_query_returns_business_conclusion():
    graph = build_graph()
    config = {"configurable": {"thread_id": "comprehensive-business-answer"}}
    first = graph.invoke({
        "question": "综合分析张伟和李明的学术、职业和产业合作关系。",
        "max_replans": 2,
        "replan_count": 0,
        "task_history": [],
    }, config=config)
    assert first["__interrupt__"]
    final = graph.invoke(Command(resume={"张伟": "person_zw_001", "李明": "person_lm_001"}), config=config)

    assert [task["agent"] for task in final["tasks"]] == [
        "talent_agent", "achievement_agent", "enterprise_agent"
    ]
    assert all(task["required_fact_types"] for task in final["tasks"])
    assert all(task["required_entity_ids"] == ["person_zw_001", "person_lm_001"] for task in final["tasks"])
    assert final.get("graph_result") is None
    assert final.get("industry_result") is None
    answer = final["final_answer"]
    assert "自 2019 年起重叠" in answer
    assert "共同论文" in answer
    assert "共同项目" in answer
    assert "智图科技" in answer
    assert "产业知识图谱平台" in answer
    assert "一种混合检索方法" in answer
    assert "完成 3 次工具调用" not in answer
    assert "综合结论" in answer


def test_incomplete_enterprise_cooperation_requires_enterprise_replan():
    validation = validator_node({
        "question": "分析两人的产业合作关系",
        "complexity": "complex",
        "resolved_entities": {"张伟": "person_zw_001", "李明": "person_lm_001"},
        "tasks": [{"agent": "enterprise_agent"}],
        "enterprise_result": {
            "agent": "enterprise_agent", "errors": [],
            "facts": [{"tool": "get_person_company_roles", "data": []}],
        },
        "evidence": [],
    })["validation_result"]
    assert validation["valid"] is False
    assert validation["needs_replan"] is True
    assert validation["missing_domains"] == ["enterprise"]
