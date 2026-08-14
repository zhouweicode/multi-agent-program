"""图推理 Agent 独享工具。"""
from collections import deque
from langchain_core.tools import tool
from data.mock_graph import GRAPH_EDGES


def _neighbors(entity_id: str) -> list[tuple[str, dict]]:
    rows = []
    for edge in GRAPH_EDGES:
        if edge["source"] == entity_id:
            rows.append((edge["target"], edge))
        elif edge["target"] == entity_id:
            rows.append((edge["source"], edge))
    return rows


@tool
def get_neighbors(entity_id: str) -> list[dict]:
    """查询实体的一跳邻居及关系。"""
    return [{"entity_id": other, **edge} for other, edge in _neighbors(entity_id)]


@tool
def find_path(source_id: str, target_id: str, max_hops: int = 4) -> dict:
    """使用广度优先搜索查找两个实体间的一条最短关系路径。"""
    queue = deque([(source_id, [source_id], [])])
    visited = {source_id}
    while queue:
        current, nodes, edges = queue.popleft()
        if current == target_id:
            return {"found": True, "nodes": nodes, "edges": edges, "hop_count": len(edges)}
        if len(edges) >= max_hops:
            continue
        for other, edge in _neighbors(current):
            if other not in visited:
                visited.add(other)
                queue.append((other, nodes + [other], edges + [edge.copy()]))
    return {"found": False, "nodes": [], "edges": [], "hop_count": 0}


@tool
def k_hop_expand(entity_id: str, k: int = 2) -> dict:
    """从实体出发扩展 K 跳，返回每一层新发现的实体。"""
    visited, frontier, levels = {entity_id}, {entity_id}, []
    for depth in range(1, k + 1):
        next_frontier = {other for node in frontier for other, _ in _neighbors(node) if other not in visited}
        visited.update(next_frontier)
        levels.append({"hop": depth, "entity_ids": sorted(next_frontier)})
        frontier = next_frontier
        if not frontier:
            break
    return {"start_entity_id": entity_id, "k": k, "levels": levels}


@tool
def calculate_path_strength(source_id: str, target_id: str) -> dict:
    """按路径边权乘积计算关系强度。"""
    path = find_path.invoke({"source_id": source_id, "target_id": target_id})
    strength = 0.0 if not path["found"] else round(__import__("math").prod(edge["weight"] for edge in path["edges"]), 4)
    return {"source_id": source_id, "target_id": target_id, "strength": strength, "path": path}
