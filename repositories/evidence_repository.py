"""统一 Evidence Repository：把不同业务后端归一为可回查的证据记录。"""
from __future__ import annotations

from services.achievement_service import AchievementService
from time import monotonic


class EvidenceRepository:
    """当前先统一科研证据；其他领域保留规范引用，后续替换为各自真实 Repository。"""
    EXTERNAL_PREFIXES = {
        "ev_employment_": "employment", "ev_education_": "education", "ev_role_": "enterprise_role",
        "ev_company_": "enterprise", "ev_event_": "industry_event", "ev_graph_": "graph_relation",
        "mysql_employment_": "employment", "mysql_education_": "education",
    }

    def __init__(self, achievement_service: AchievementService):
        self.achievements = achievement_service
        self._entity_cache: dict[tuple[str, ...], tuple[float, list[dict]]] = {}

    def list_for_entities(self, entity_ids: list[str]) -> list[dict]:
        cache_key = tuple(sorted(entity_ids))
        cached = self._entity_cache.get(cache_key)
        if cached and monotonic() - cached[0] < 30:
            return cached[1]
        papers = [{**row, "evidence_type": "paper", "source": row.get("source", "mock:papers")}
                  for row in self.achievements.get_common_papers(entity_ids)]
        projects = [{**row, "evidence_type": "project", "source": row.get("source", "mock:projects")}
                    for row in self.achievements.get_common_projects(entity_ids)]
        patent_rows = self.achievements.get_common_patents(entity_ids) if hasattr(self.achievements, "get_common_patents") else []
        patents = [{**row, "evidence_type": "patent", "source": row.get("source", "mock:patents")}
                   for row in patent_rows]
        records = []
        for row in papers + projects + patents:
            records.append({"evidence_id": row["evidence_id"], "evidence_type": row["evidence_type"],
                            "source": row["source"], "source_record_id": row.get("paper_id") or row.get("project_id") or row.get("patent_id"),
                            "entity_ids": row.get("authors") or row.get("participant_ids") or row.get("inventor_ids", []),
                            "event_time": row.get("year") or row.get("start_year"), "payload": row})
        self._entity_cache[cache_key] = (monotonic(), records)
        return records

    def get(self, evidence_id: str, entity_ids: list[str] | None = None) -> dict | None:
        if entity_ids:
            record = next((row for row in self.list_for_entities(entity_ids)
                           if row["evidence_id"] == evidence_id), None)
            if record:
                return record
        for prefix, evidence_type in self.EXTERNAL_PREFIXES.items():
            if evidence_id.startswith(prefix):
                return {"evidence_id": evidence_id, "evidence_type": evidence_type,
                        "source": "domain_repository_reference", "source_record_id": evidence_id,
                        "entity_ids": entity_ids or [], "event_time": None, "payload": {}}
        return None

    def get_batch(self, evidence_ids: list[str], entity_ids: list[str] | None = None) -> list[dict]:
        return [record for evidence_id in evidence_ids if (record := self.get(evidence_id, entity_ids))]

    def exists(self, evidence_id: str, entity_ids: list[str] | None = None) -> bool:
        return self.get(evidence_id, entity_ids) is not None
