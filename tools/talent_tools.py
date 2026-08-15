"""人才机构 Agent 独享工具。"""
from langchain_core.tools import tool
from services.entity_service import EntityService

_entities = EntityService()


@tool
def get_person_profile(entity_id: str) -> dict:
    """按唯一实体 ID 查询专家画像。"""
    return _entities.get(entity_id) or {"error": "PERSON_NOT_FOUND", "entity_id": entity_id}


@tool
def get_employment_history(entity_id: str) -> list[dict]:
    """查询专家任职经历。"""
    rows = {
        "person_zw_001": [{"organization": "清华大学", "role": "教授", "start_year": 2017, "end_year": None, "evidence_id": "ev_employment_zw_001"}],
        "person_lm_001": [{"organization": "清华大学", "role": "副教授", "start_year": 2019, "end_year": None, "evidence_id": "ev_employment_lm_001"}],
        "person_zw_002": [{"organization": "北京理工大学", "role": "研究员", "start_year": 2018, "end_year": None, "evidence_id": "ev_employment_zw_002"}],
        "person_lm_002": [{"organization": "中科院自动化所", "role": "研究员", "start_year": 2016, "end_year": None, "evidence_id": "ev_employment_lm_002"}],
    }
    return [{"entity_id": entity_id, **row} for row in rows.get(entity_id, [])]


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
                overlaps.append({"entity_ids": entity_ids, "organization": left["organization"], "from_year": max(left["start_year"], right["start_year"]),
                                 "evidence_ids": [left["evidence_id"], right["evidence_id"]]})
    return overlaps
