"""Supervisor 任务验收契约；兼容导出由 ToolRegistry 统一生成。"""

from tools.registry import tool_registry

AGENT_DOMAINS = tool_registry.agent_domains
FACT_TYPE_TO_TOOL = tool_registry.fact_type_to_tool
DEFAULT_REQUIRED_FACT_TYPES = tool_registry.default_required_fact_types


def required_fact_types(
    agent: str, question: str, entity_count: int | None = None
) -> list[str]:
    """根据明确任务语义细化静态领域契约。"""
    if entity_count == 1 and agent == "talent_agent":
        if any(word in question for word in ("教育", "学历", "毕业")):
            return ["education"]
        if any(word in question for word in ("任职", "工作", "单位", "履历")):
            return ["employment"]
        return ["person_profile"]
    if (
        agent == "achievement_agent"
        and "专利" in question
        and not any(word in question for word in ("论文", "学术", "科研项目"))
    ):
        return ["patents" if entity_count == 1 else "common_patents"]
    if entity_count == 1 and agent == "achievement_agent":
        return ["papers"]
    if agent == "graph_reasoning_agent":
        graph_contracts = (
            (("图 Schema", "图Schema", "图模式", "图谱结构", "可查询标签", "可查询关系", "可查询属性"), "graph_schema"),
            (("图统计", "图聚合", "分组统计", "去重统计", "数量排名"), "graph_aggregation"),
            (("局部子图", "查询子图", "返回子图"), "bounded_subgraph"),
            (("Top-K", "top-k", "多条路径", "加权路径", "最短路径", "路径排名"), "ranked_paths"),
            (("过滤邻居", "筛选邻居", "关系过滤", "方向过滤", "一跳筛选"), "filtered_neighbors"),
        )
        for keywords, fact_type in graph_contracts:
            if any(keyword in question for keyword in keywords):
                return [fact_type]
        if entity_count == 1:
            # A single-entity graph exploration cannot produce a source-target
            # path contract; require local neighbours plus bounded expansion.
            return ["neighbors", "k_hop_subgraph"]
    return list(DEFAULT_REQUIRED_FACT_TYPES[agent])
