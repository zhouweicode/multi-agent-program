"""产业链 Agent 独享工具。"""
from langchain_core.tools import tool
from data.mock_industry import CHAINS, CHAIN_NODES, NODE_EVENTS
from data.mock_enterprises import COMPANIES


@tool
def get_chain_structure(chain_id: str) -> dict:
    """查询产业链及其上中下游节点结构。"""
    chain = CHAINS.get(chain_id)
    if not chain:
        return {"error": "CHAIN_NOT_FOUND", "chain_id": chain_id}
    return {**chain, "node_details": [CHAIN_NODES[x].copy() for x in chain["nodes"]]}


@tool
def get_node_companies(node_id: str) -> list[dict]:
    """查询产业节点关联企业。"""
    node = CHAIN_NODES.get(node_id, {})
    ids = set(node.get("company_ids", []))
    return [row.copy() for row in COMPANIES if row["company_id"] in ids]


@tool
def get_node_events(node_id: str) -> list[dict]:
    """查询产业节点事件。"""
    return [row.copy() for row in NODE_EVENTS if row["node_id"] == node_id]


@tool
def rank_top_events(node_id: str, top_n: int = 3) -> list[dict]:
    """按重要度返回产业节点 TOP-N 事件。"""
    rows = get_node_events.invoke({"node_id": node_id})
    return sorted(rows, key=lambda item: item["importance"], reverse=True)[:top_n]
