from langgraph.types import Command
from graph.builder import build_graph
from tools.enterprise_tools import get_person_company_roles, get_company_projects, get_company_patents
from tools.industry_tools import get_chain_structure, get_node_companies, rank_top_events
from tools.graph_tools import get_neighbors, find_path, k_hop_expand, calculate_path_strength


def test_enterprise_tools_return_roles_projects_and_patents():
    roles = get_person_company_roles.invoke({"entity_ids": ["person_zw_001", "person_lm_001"]})
    projects = get_company_projects.invoke({"company_id": "company_001"})
    patents = get_company_patents.invoke({"company_id": "company_001"})
    assert {row["role"] for row in roles} == {"技术顾问", "联合实验室负责人"}
    assert projects[0]["participant_ids"] == ["person_zw_001", "person_lm_001"]
    assert patents[0]["patent_id"] == "company_patent_001"


def test_industry_tools_structure_companies_and_top_events():
    structure = get_chain_structure.invoke({"chain_id": "chain_ai"})
    companies = get_node_companies.invoke({"node_id": "node_model"})
    events = rank_top_events.invoke({"node_id": "node_model", "top_n": 2})
    assert [row["level"] for row in structure["node_details"]] == ["上游", "中游", "下游"]
    assert companies[0]["company_id"] == "company_001"
    assert [row["importance"] for row in events] == [92, 85]


def test_graph_tools_neighbors_path_expand_and_strength():
    neighbors = get_neighbors.invoke({"entity_id": "person_zw_001"})
    path = find_path.invoke({"source_id": "person_zw_001", "target_id": "node_model"})
    expanded = k_hop_expand.invoke({"entity_id": "person_zw_001", "k": 2})
    strength = calculate_path_strength.invoke({"source_id": "person_zw_001", "target_id": "node_model"})
    assert {row["entity_id"] for row in neighbors} >= {"person_lm_001", "company_001"}
    assert path["found"] is True and path["hop_count"] == 2
    assert "node_model" in expanded["levels"][1]["entity_ids"]
    assert strength["strength"] == 0.56


def test_simple_enterprise_query_routes_without_supervisor():
    graph = build_graph()
    config = {"configurable": {"thread_id": "stage2-enterprise"}}
    first = graph.invoke({"question": "张伟在企业担任什么角色？", "max_replans": 2, "replan_count": 0}, config=config)
    final = graph.invoke(Command(resume={"张伟": "person_zw_001"}), config=config)
    assert "plan" not in final
    assert final["enterprise_result"]["agent"] == "enterprise_agent"
    assert final["validation_result"]["valid"] is True
    assert "企业关系" in final["final_answer"]


def test_complex_query_supervisor_fans_out_to_five_agents():
    graph = build_graph()
    config = {"configurable": {"thread_id": "stage2-five-agents"}}
    question = "综合分析张伟和李明的学术、职业、企业、产业链和间接关系路径。"
    first = graph.invoke({"question": question, "max_replans": 2, "replan_count": 0, "task_history": []}, config=config)
    final = graph.invoke(Command(resume={"张伟": "person_zw_001", "李明": "person_lm_001"}), config=config)
    planned = {task["agent"] for task in final["tasks"]}
    assert planned == {"talent_agent", "achievement_agent", "enterprise_agent", "industry_agent", "graph_reasoning_agent"}
    assert all(final.get(field) for field in ("talent_result", "achievement_result", "enterprise_result", "industry_result", "graph_result"))
    assert len(final["task_history"]) == 5
    assert final["validation_result"]["valid"] is True


def test_simple_industry_query_needs_no_person_entity():
    graph = build_graph()
    config = {"configurable": {"thread_id": "stage2-industry"}}
    final = graph.invoke({"question": "查询人工智能产业链TOP事件。", "max_replans": 2, "replan_count": 0}, config=config)
    assert final["industry_result"]["agent"] == "industry_agent"
    top_fact = next(item for item in final["industry_result"]["facts"] if item["tool"] == "rank_top_events")
    assert len(top_fact["data"]) == 2
    assert final["validation_result"]["valid"] is True

