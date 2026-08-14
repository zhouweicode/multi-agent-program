from langgraph.types import Command
from graph.builder import build_graph


def test_ambiguous_entities_pause_and_resume():
    graph = build_graph()
    config = {"configurable": {"thread_id": "test-resume"}}
    first = graph.invoke({"question": "综合分析张伟和李明的学术和职业合作关系。", "max_replans": 2, "replan_count": 0}, config=config)
    assert first["__interrupt__"][0].value["status"] == "NEED_USER_SELECTION"
    final = graph.invoke(Command(resume={"张伟": "person_zw_001", "李明": "person_lm_001"}), config=config)
    assert final["validation_result"]["valid"] is True
    assert len(final["achievement_result"]["facts"][0]["data"]) == 2
    assert "清华大学" in final["final_answer"]


def test_simple_paper_query_skips_supervisor():
    graph = build_graph()
    config = {"configurable": {"thread_id": "test-simple"}}
    first = graph.invoke({"question": "张伟发表过哪些论文？", "max_replans": 2, "replan_count": 0}, config=config)
    final = graph.invoke(Command(resume={"张伟": "person_zw_001"}), config=config)
    assert "plan" not in final
    assert final["achievement_result"]["agent"] == "achievement_agent"
    assert final["validation_result"]["valid"] is True
