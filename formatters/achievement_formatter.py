from formatters.utils import facts, unique


def format_achievement(result: dict | None, resolved: dict[str, str]) -> tuple[str | None, bool]:
    if not result:
        return None, False
    entity_ids = set(resolved.values())
    common = facts(result, "get_common_papers")
    authored = facts(result, "get_author_papers")
    papers = (unique(common + [row for row in authored if entity_ids.issubset(set(row.get("authors", [])))], "paper_id")
              if len(entity_ids) > 1 else unique(authored or common, "paper_id"))
    projects = unique(facts(result, "get_common_projects"), "project_id")
    parts = []
    if papers:
        label = "共同论文" if len(entity_ids) > 1 else "发表论文"
        parts.append(label + "：" + "；".join(f"《{row['title']}》（{row['year']}）" for row in papers))
    if projects:
        parts.append("共同项目：" + "；".join(f"{row['name']}（{row['start_year']}—{row['end_year']} 年）" for row in projects))
    if not parts:
        return "学术合作：当前数据源未返回共同论文或共同项目证据", False
    return (("学术合作：" + "；".join(parts), True) if len(entity_ids) > 1 else (parts[0], False))
