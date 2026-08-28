"""高级图查询 Tool 的受限 DSL、双后端契约与 Planner 路由测试。"""

import json

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from graph.builder import build_graph
from models.contracts import required_fact_types
from models.graph_queries import (
    AggregateGraphInput,
    FilteredNeighborsInput,
    FindPathsInput,
    GraphFilter,
    QuerySubgraphInput,
)
from models.llm import MockToolCallingModel
from models.settings import Settings
from repositories.neo4j_repository import Neo4jGraphRepository
from services.evidence_normalizer import normalize_tool_output
from services.graph_service import GraphService
from tools.provider import get_tools


@pytest.fixture
def graph_service(monkeypatch):
    monkeypatch.setenv("GRAPH_BACKEND", "mock")
    return GraphService()


def test_filtered_neighbors_supports_relation_direction_label_and_weight(graph_service):
    rows = graph_service.get_neighbors_filtered(
        FilteredNeighborsInput(
            entity_id="person_zw_001",
            relation_types=["COAUTHOR"],
            target_labels=["Scholar"],
            direction="out",
            min_weight=0.85,
        )
    )
    assert [row["entity_id"] for row in rows] == ["person_lm_001"]
    assert rows[0]["evidence_id"] == "ev_graph_001"


def test_top_k_paths_are_bounded_and_ranked(graph_service):
    result = graph_service.find_paths(
        FindPathsInput(
            source_id="person_zw_001",
            target_id="node_model",
            max_hops=4,
            top_k=3,
            ranking="shortest",
        )
    )
    assert result["path_count"] == 2
    assert [path["hop_count"] for path in result["paths"]] == [2, 3]
    assert result["paths"][0]["score"] == 0.56


def test_subgraph_enforces_node_and_edge_limits(graph_service):
    result = graph_service.query_subgraph(
        QuerySubgraphInput(
            seed_entity_ids=["person_zw_001"],
            max_hops=3,
            max_nodes=2,
            max_edges=1,
        )
    )
    assert result["node_count"] <= 2
    assert result["edge_count"] <= 1
    assert result["truncated"] is True
    assert "person_zw_001" in {node["entity_id"] for node in result["nodes"]}


def test_graph_aggregation_supports_count_group_order_and_distinct(graph_service):
    total = graph_service.aggregate_graph(
        AggregateGraphInput(
            source_label="Scholar",
            metrics=[{"operation": "count", "alias": "scholar_count"}],
        )
    )
    assert total["rows"] == [{"scholar_count": 2}]

    grouped = graph_service.aggregate_graph(
        AggregateGraphInput(
            source_label="Scholar",
            relation_type="COAUTHOR",
            target_label="Scholar",
            direction="out",
            group_by=[
                {"scope": "source", "field": "scholar_id", "alias": "scholar_id"}
            ],
            metrics=[
                {
                    "operation": "count_distinct",
                    "field": {"scope": "target", "field": "scholar_id"},
                    "alias": "partner_count",
                }
            ],
            order_by=[{"field": "partner_count", "direction": "desc"}],
        )
    )
    assert grouped["rows"] == [{"scholar_id": "person_zw_001", "partner_count": 1}]


def test_governed_schema_is_stable_read_only_and_rejects_unknown_fields(graph_service):
    first = graph_service.get_graph_schema()
    second = graph_service.get_graph_schema()
    assert first["read_only"] is True
    assert first["content_hash"] == second["content_hash"]
    assert "Scholar" in first["node_types"]
    assert "AUTHOR_OF" in first["relation_types"]
    assert first["limits"]["max_nodes"] == 200

    with pytest.raises(ValueError, match="未授权关系"):
        graph_service.find_paths(
            FindPathsInput(
                source_id="person_zw_001",
                target_id="node_model",
                relation_types=["DELETE_EVERYTHING"],
            )
        )
    with pytest.raises(ValueError, match="属性未授权"):
        graph_service.aggregate_graph(
            AggregateGraphInput(
                source_label="Scholar",
                metrics=[
                    {
                        "operation": "sum",
                        "field": {"scope": "source", "field": "password"},
                        "alias": "secret",
                    }
                ],
            )
        )
    with pytest.raises(ValidationError, match="value 必须是数组"):
        GraphFilter(field="year", operator="in", value=2025)


def test_neo4j_filter_values_are_parameters_not_cypher(monkeypatch):
    repository = object.__new__(Neo4jGraphRepository)
    repository.settings = Settings(neo4j_managed_only=False)
    captured = {}

    def fake_read(query, **parameters):
        captured.update({"query": query, "parameters": parameters})
        return []

    monkeypatch.setattr(repository, "_read", fake_read)
    injection = "x') MATCH (n) DETACH DELETE n //"
    repository.get_neighbors_filtered(
        FilteredNeighborsInput(
            entity_id="person_zw_001",
            relation_types=["COAUTHOR"],
            target_labels=["Scholar"],
            filters=[{"scope": "target", "field": "name", "value": injection}],
        )
    )
    assert injection not in captured["query"]
    assert captured["parameters"]["filter_0"] == injection
    assert "$filter_0" in captured["query"]


@pytest.mark.parametrize(
    ("goal", "expected_tool", "expected_fact"),
    [
        ("读取图Schema和可查询关系", "get_graph_schema", "graph_schema"),
        ("执行图聚合统计", "aggregate_graph", "graph_aggregation"),
        ("查询张伟局部子图", "query_subgraph", "bounded_subgraph"),
        ("查询两实体Top-K路径", "find_paths", "ranked_paths"),
        ("按合作关系过滤邻居", "get_neighbors_filtered", "filtered_neighbors"),
    ],
)
def test_mock_planner_selects_advanced_graph_tool(goal, expected_tool, expected_fact):
    model = MockToolCallingModel("graph").bind_tools(get_tools("graph"))
    message = HumanMessage(
        content=json.dumps(
            {
                "goal": goal,
                "resolved_entities": {
                    "张伟": "person_zw_001",
                    "李明": "person_lm_001",
                },
            },
            ensure_ascii=False,
        )
    )
    response = model.invoke([message])
    assert response.tool_calls[0]["name"] == expected_tool
    assert required_fact_types("graph_reasoning_agent", goal) == [expected_fact]


def test_top_k_and_subgraph_edges_are_normalized_as_evidence(graph_service):
    paths = graph_service.find_paths(
        FindPathsInput(source_id="person_zw_001", target_id="node_model", top_k=1)
    )
    path_evidence = normalize_tool_output(
        "find_paths", paths, ["person_zw_001", "node_model"]
    )
    assert {item["evidence_id"] for item in path_evidence} == {
        "ev_graph_002",
        "ev_graph_004",
    }
    assert all(item["fact_type"] == "graph_path" for item in path_evidence)

    subgraph = graph_service.query_subgraph(
        QuerySubgraphInput(seed_entity_ids=["person_zw_001"], max_hops=1)
    )
    subgraph_evidence = normalize_tool_output(
        "query_subgraph", subgraph, ["person_zw_001"]
    )
    assert subgraph_evidence
    assert all(item["fact_type"] == "graph_subgraph" for item in subgraph_evidence)


@pytest.mark.parametrize(
    ("question", "expected_tool"),
    [
        ("读取图Schema和可查询关系", "get_graph_schema"),
        ("执行图聚合统计", "aggregate_graph"),
    ],
)
def test_simple_graph_workflow_routes_executes_and_validates(question, expected_tool):
    result = build_graph().invoke(
        {"question": question, "max_replans": 2, "replan_count": 0},
        config={"configurable": {"thread_id": f"advanced-{expected_tool}"}},
    )
    assert [item["name"] for item in result["graph_result"]["tool_calls"]] == [
        expected_tool
    ]
    assert result["validation_result"]["valid"] is True
