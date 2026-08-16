"""产业链 Agent 独享工具。"""
from langchain_core.tools import tool
from services.resources import get_industry_service


@tool
def get_chain_structure(chain_id: str) -> dict:
    """查询产业链及其上中下游节点结构。"""
    return get_industry_service().get_chain_structure(chain_id)


@tool
def get_node_companies(node_id: str) -> list[dict]:
    """查询产业节点关联企业。"""
    return get_industry_service().get_node_companies(node_id)


@tool
def get_node_events(node_id: str) -> list[dict]:
    """查询产业节点事件。"""
    return get_industry_service().get_node_events(node_id)


@tool
def rank_top_events(node_id: str, top_n: int = 3) -> list[dict]:
    """按重要度返回产业节点 TOP-N 事件。"""
    rows = get_node_events.invoke({"node_id": node_id})
    return sorted(rows, key=lambda item: item["importance"], reverse=True)[:top_n]
