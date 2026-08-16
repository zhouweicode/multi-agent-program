from formatters.utils import facts


def format_talent(result: dict | None, resolved: dict[str, str]) -> tuple[str | None, bool]:
    if not result:
        return None, False
    entity_ids = set(resolved.values())
    overlaps = facts(result, "match_employment_overlap")
    employments = facts(result, "get_employment_history")
    profiles = facts(result, "get_person_profile")
    educations = facts(result, "get_education_history")
    if overlaps:
        detail = "；".join(f"{row['organization']}（{'自 ' + str(row['from_year']) + ' 年起重叠' if row.get('from_year') else '重叠时间未记录'}）" for row in overlaps)
        return f"职业合作：两人存在共同任职经历，{detail}，因此具备同机构同事关系", True
    if len(entity_ids) > 1 and employments:
        shared = []
        for organization in dict.fromkeys(row["organization"] for row in employments):
            rows = [row for row in employments if row["organization"] == organization]
            if entity_ids.issubset({row.get("entity_id") for row in rows}):
                years = [row.get("start_year") for row in rows if isinstance(row.get("start_year"), int)]
                shared.append(f"{organization}（{'自 ' + str(max(years)) + ' 年起重叠' if years else '重叠时间未记录'}）")
        if shared:
            return "职业合作：两人存在共同任职经历，" + "；".join(shared) + "，因此具备同机构同事关系", True
        return "职业合作：当前返回的任职证据中未发现两人在同一机构的时间重叠", False
    if employments:
        rows = []
        for item in employments:
            period = ("时间未记录" if item.get("start_year") is None else
                      (f"{item['start_year']} 年至今" if item.get("end_year") is None else f"{item['start_year']}—{item['end_year']} 年"))
            rows.append(f"{item['organization']}，担任{item['role']}（{period}）")
        return "任职经历：" + "；".join(rows), False
    if educations:
        rows = [f"{item.get('institution', '学校未知')}，{item.get('degree', '学位未知')}" for item in educations]
        return "教育经历：" + "；".join(rows), False
    if profiles:
        profile = profiles[0]
        return f"专家画像：{profile.get('organization', '机构未知')}，{profile.get('title', '职务未知')}", False
    return "职业合作：当前数据源未返回可用于判断共同任职的证据", False
