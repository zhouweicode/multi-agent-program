from agents.talent_agent import build_talent_agent
from nodes.answer_node import answer_node
from nodes.router_node import router_node


def test_router_recognizes_work_query_as_talent_domain():
    result = router_node({"question": "张伟在哪里工作？"})
    assert result["complexity"] == "simple"
    assert result["primary_domain"] == "talent"


def test_single_person_talent_query_uses_profile_and_employment_tools():
    result = build_talent_agent().run("张伟在哪里工作？", {"张伟": "person_zw_001"})
    assert [call["name"] for call in result["tool_calls"]] == [
        "get_person_profile",
        "get_employment_history",
    ]
    assert result["facts"][1]["data"][0]["organization"] == "清华大学"


def test_answer_renders_single_person_employment_instead_of_overlap():
    talent_result = build_talent_agent().run("张伟在哪里工作？", {"张伟": "person_zw_001"})
    result = answer_node({
        "resolved_entities": {"张伟": "person_zw_001"},
        "validation_result": {"valid": True},
        "talent_result": talent_result,
    })
    assert "清华大学" in result["final_answer"]
    assert "担任教授" in result["final_answer"]
    assert "未发现共同任职经历" not in result["final_answer"]
