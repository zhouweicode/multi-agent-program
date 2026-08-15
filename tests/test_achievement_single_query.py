from agents.achievement_agent import build_achievement_agent
from nodes.answer_node import answer_node
from nodes.validator_node import validator_node


def _single_author_result():
    return build_achievement_agent().run(
        "张伟发表过哪些论文？",
        {"张伟": "person_zw_001"},
    )


def test_single_author_query_only_calls_author_papers_tool():
    result = _single_author_result()
    assert [call["name"] for call in result["tool_calls"]] == ["get_author_papers"]
    assert len(result["facts"][0]["data"]) == 2


def test_validator_accepts_complete_single_author_papers_result():
    validation = validator_node({
        "question": "张伟发表过哪些论文？",
        "complexity": "simple",
        "primary_domain": "achievement",
        "resolved_entities": {"张伟": "person_zw_001"},
        "achievement_result": _single_author_result(),
        "evidence": [],
    })["validation_result"]
    assert validation["valid"] is True
    assert validation["needs_replan"] is False


def test_answer_lists_author_papers_without_calling_them_common_papers():
    output = answer_node({
        "resolved_entities": {"张伟": "person_zw_001"},
        "validation_result": {"valid": True},
        "achievement_result": _single_author_result(),
    })["final_answer"]
    assert "发表论文" in output
    assert "Knowledge Graph Reasoning with Multi-Agent Collaboration" in output
    assert "Hybrid Retrieval for Scientific Knowledge Graphs" in output
    assert "共同论文" not in output
