"""图推理 Agent 独享工具。"""
from langchain_core.tools import tool
from services.graph_service import GraphService


@tool
def get_neighbors(entity_id: str) -> list[dict]:
    """查询实体的一跳邻居及关系。"""
    return GraphService().get_neighbors(entity_id)


@tool
def find_path(source_id: str, target_id: str, max_hops: int = 4) -> dict:
    """使用广度优先搜索查找两个实体间的一条最短关系路径。"""
    return GraphService().find_path(source_id, target_id, max_hops)


@tool
def k_hop_expand(entity_id: str, k: int = 2) -> dict:
    """从实体出发扩展 K 跳，返回每一层新发现的实体。"""
    return GraphService().k_hop_expand(entity_id, k)


@tool
def calculate_path_strength(source_id: str, target_id: str) -> dict:
    """按路径边权乘积计算关系强度。"""
    return GraphService().calculate_path_strength(source_id, target_id)
