"""人才机构 Agent 独享工具。"""
from langchain_core.tools import tool
from services.resources import get_entity_service


@tool
def get_person_profile(entity_id: str) -> dict:
    """按唯一实体 ID 查询专家画像。"""
    return get_entity_service().get(entity_id) or {"error": "PERSON_NOT_FOUND", "entity_id": entity_id}


@tool
def get_employment_history(entity_id: str) -> list[dict]:
    """查询专家任职经历。"""
    return get_entity_service().get_employment_history(entity_id)


@tool
def get_education_history(entity_id: str) -> list[dict]:
    """查询专家教育经历。"""
    return get_entity_service().get_education_history(entity_id)


@tool
def match_employment_overlap(entity_ids: list[str]) -> list[dict]:
    """计算两位专家任职机构与时间是否重叠。"""
    if len(entity_ids) != 2:
        return []
    histories = [get_employment_history.invoke({"entity_id": x}) for x in entity_ids]
    overlaps = []
    for left in histories[0]:
        for right in histories[1]:
            if left["organization"] == right["organization"]:
                years = [year for year in (left.get("start_year"), right.get("start_year")) if isinstance(year, int)]
                overlaps.append({"entity_ids": entity_ids, "organization": left["organization"], "from_year": max(years) if years else None,
                                 "evidence_ids": [left["evidence_id"], right["evidence_id"]]})
    return overlaps
