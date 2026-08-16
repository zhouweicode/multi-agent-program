from formatters.utils import facts


def format_enterprise(result: dict | None, resolved: dict[str, str]) -> tuple[str | None, bool]:
    if not result:
        return None, False
    entity_ids = set(resolved.values())
    roles = facts(result, "get_person_company_roles")
    projects = [row for row in facts(result, "get_company_projects") if not entity_ids or entity_ids.issubset(set(row.get("participant_ids", [])))]
    patents = [row for row in facts(result, "get_company_patents") if not entity_ids or entity_ids.issubset(set(row.get("inventor_ids", [])))]
    names = {entity_id: name for name, entity_id in resolved.items()}
    parts = []
    if roles:
        parts.append("企业角色：" + "；".join(
            f"{names.get(row['entity_id'], row['entity_id'])}在{row.get('company_name', row['company_id'])}担任{row['role']}（自 {row['start_year']} 年）"
            for row in roles))
    if projects:
        label = "共同企业项目" if len(entity_ids) > 1 else "相关企业项目"
        parts.append(label + "：" + "；".join(f"{row['name']}（{row.get('company_name', row['company_id'])}）" for row in projects))
    if patents:
        label = "共同企业专利" if len(entity_ids) > 1 else "相关企业专利"
        parts.append(label + "：" + "；".join(f"《{row['title']}》（{row.get('company_name', row['company_id'])}）" for row in patents))
    if not parts:
        return "企业与产业合作：当前数据源未发现共同企业项目或共同企业专利", False
    title = "企业与产业合作" if len(entity_ids) > 1 else "企业关系"
    return title + "：" + "；".join(parts), len(entity_ids) > 1 and bool(projects or patents)
