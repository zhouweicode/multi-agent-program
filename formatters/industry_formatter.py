from formatters.utils import facts


def format_industry(result: dict | None, resolved: dict[str, str]) -> tuple[str | None, bool]:
    if not result:
        return None, False
    chains = facts(result, "get_chain_structure")
    companies = facts(result, "get_node_companies")
    events = facts(result, "rank_top_events") or facts(result, "get_node_events")
    parts = []
    if chains:
        parts.append("产业链：" + "；".join(row.get("name", row.get("chain_id", "未知产业链")) for row in chains))
    if companies:
        parts.append("相关企业：" + "、".join(row["name"] for row in companies))
    if events:
        parts.append("重点事件：" + "；".join(row["title"] for row in events))
    return ("产业链信息：" + "；".join(parts) if parts else "产业链信息：当前数据源未返回相关事实"), False
