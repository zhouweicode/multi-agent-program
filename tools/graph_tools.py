"""图推理 Agent 独享工具。"""
from typing import Any

from langchain_core.tools import tool

from models.graph_queries import (
    AggregateGraphInput,
    FilteredNeighborsInput,
    FindPathsInput,
    QuerySubgraphInput,
)
from services.resources import get_graph_service


@tool
def get_neighbors(entity_id: str) -> list[dict]:
    """查询实体的一跳邻居及关系。"""
    return get_graph_service().get_neighbors(entity_id)


@tool
def find_path(source_id: str, target_id: str, max_hops: int = 4) -> dict:
    """使用广度优先搜索查找两个实体间的一条最短关系路径。"""
    return get_graph_service().find_path(source_id, target_id, max_hops)


@tool
def k_hop_expand(entity_id: str, k: int = 2) -> dict:
    """从实体出发扩展 K 跳，返回每一层新发现的实体。"""
    return get_graph_service().k_hop_expand(entity_id, k)


@tool
def calculate_path_strength(source_id: str, target_id: str) -> dict:
    """按路径边权乘积计算关系强度。"""
    return get_graph_service().calculate_path_strength(source_id, target_id)


@tool(args_schema=FilteredNeighborsInput)
def get_neighbors_filtered(
    entity_id: str,
    relation_types: list[str] | None = None,
    target_labels: list[str] | None = None,
    direction: str = "both",
    start_year: int | None = None,
    end_year: int | None = None,
    min_weight: float | None = None,
    filters: list[dict[str, Any]] | None = None,
    limit: int = 50,
) -> list[dict]:
    """按关系、方向、目标标签、时间、权重和属性过滤一跳邻居。"""
    query = FilteredNeighborsInput.model_validate(
        {
            "entity_id": entity_id,
            "relation_types": relation_types or [],
            "target_labels": target_labels or [],
            "direction": direction,
            "start_year": start_year,
            "end_year": end_year,
            "min_weight": min_weight,
            "filters": filters or [],
            "limit": limit,
        }
    )
    return get_graph_service().get_neighbors_filtered(query)


@tool(args_schema=FindPathsInput)
def find_paths(
    source_id: str,
    target_id: str,
    max_hops: int = 4,
    top_k: int = 3,
    relation_types: list[str] | None = None,
    direction: str = "both",
    ranking: str = "shortest",
    min_weight: float | None = None,
) -> dict:
    """返回受跳数和关系白名单约束的 Top-K 最短或高权重路径。"""
    query = FindPathsInput.model_validate(
        {
            "source_id": source_id,
            "target_id": target_id,
            "max_hops": max_hops,
            "top_k": top_k,
            "relation_types": relation_types or [],
            "direction": direction,
            "ranking": ranking,
            "min_weight": min_weight,
        }
    )
    return get_graph_service().find_paths(query)


@tool(args_schema=QuerySubgraphInput)
def query_subgraph(
    seed_entity_ids: list[str],
    max_hops: int = 2,
    node_labels: list[str] | None = None,
    relation_types: list[str] | None = None,
    direction: str = "both",
    max_nodes: int = 100,
    max_edges: int = 200,
) -> dict:
    """围绕种子实体返回带节点、关系和截断标记的受限局部子图。"""
    query = QuerySubgraphInput.model_validate(
        {
            "seed_entity_ids": seed_entity_ids,
            "max_hops": max_hops,
            "node_labels": node_labels or [],
            "relation_types": relation_types or [],
            "direction": direction,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
        }
    )
    return get_graph_service().query_subgraph(query)


@tool(args_schema=AggregateGraphInput)
def aggregate_graph(
    source_label: str,
    metrics: list[dict[str, Any]],
    relation_type: str | None = None,
    target_label: str | None = None,
    direction: str = "both",
    filters: list[dict[str, Any]] | None = None,
    group_by: list[dict[str, Any]] | None = None,
    order_by: list[dict[str, Any]] | None = None,
    limit: int = 20,
) -> dict:
    """在治理 Schema 内执行计数、去重、分组、排序及数值聚合。"""
    query = AggregateGraphInput.model_validate(
        {
            "source_label": source_label,
            "relation_type": relation_type,
            "target_label": target_label,
            "direction": direction,
            "filters": filters or [],
            "group_by": group_by or [],
            "metrics": metrics,
            "order_by": order_by or [],
            "limit": limit,
        }
    )
    return get_graph_service().aggregate_graph(query)


@tool
def get_graph_schema() -> dict:
    """返回 Planner 可使用的只读 Label、关系、属性白名单和查询上限。"""
    return get_graph_service().get_graph_schema()
