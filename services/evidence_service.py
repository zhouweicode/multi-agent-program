"""统一证据服务；Verification 不直接依赖 Mock 常量或具体数据库。"""
from repositories.evidence_repository import EvidenceRepository
from services.achievement_service import AchievementService


class EvidenceService:
    def __init__(self, achievement_service: AchievementService | None = None):
        if achievement_service is None:
            from services.resources import get_achievement_service
            achievement_service = get_achievement_service()
        self.achievements = achievement_service
        self.repository = EvidenceRepository(self.achievements)

    def cooperation_rows(self, entity_ids: list[str]) -> list[dict]:
        """从 AchievementService 当前后端获取同一批科研合作证据。"""
        return [record["payload"] | {"evidence_type": record["evidence_type"], "source": record["source"]}
                for record in self.repository.list_for_entities(entity_ids)
                if record["evidence_type"] in {"paper", "project"}]

    def _available(self, entity_ids: list[str] | None = None) -> dict[str, dict]:
        rows = self.cooperation_rows(entity_ids) if entity_ids else []
        return {row["evidence_id"]: row for row in rows if row.get("evidence_id")}

    def verify(self, evidence_ids: list[str], entity_ids: list[str] | None = None,
               evidence_records: list[dict] | None = None) -> dict:
        supplied = {str(item.get("evidence_id")): item for item in evidence_records or []
                    if item.get("evidence_id")}
        available = self._available(entity_ids)
        non_research_prefixes = ("ev_employment_", "ev_role_", "ev_company_", "ev_event_", "ev_graph_")
        missing = [evidence_id for evidence_id in evidence_ids
                   if evidence_id not in available and evidence_id not in supplied
                   and not evidence_id.startswith(non_research_prefixes)]
        return {"valid": bool(evidence_ids) and not missing, "checked_count": len(evidence_ids), "missing": missing}

    def check_sources(self, evidence_ids: list[str], entity_ids: list[str] | None = None,
                      evidence_records: list[dict] | None = None) -> dict:
        supplied = {str(item.get("evidence_id")): item for item in evidence_records or []
                    if item.get("evidence_id")}
        available = self._available(entity_ids)
        trusted_sources = ("mock:", "mysql:")
        resolved = {
            evidence_id: available.get(evidence_id) or supplied.get(evidence_id)
            for evidence_id in evidence_ids
        }
        def trusted(record: dict | None) -> bool:
            if not record:
                return False
            source_type = str(record.get("source_type") or "")
            source = str(record.get("source") or record.get("source_name") or "")
            return source_type in {"mysql", "neo4j", "milvus", "mock", "derived", "web"} or source.startswith(trusted_sources)
        untrusted = [evidence_id for evidence_id, record in resolved.items() if not trusted(record)]
        sources = sorted({str(record.get("source") or record.get("source_name"))
                          for record in resolved.values() if record})
        return {"trusted": bool(evidence_ids) and not untrusted, "untrusted": untrusted, "sources": sources}

    def relation(self, entity_ids: list[str], relation: str) -> dict:
        rows = self.cooperation_rows(entity_ids)
        papers = [row for row in rows if row["evidence_type"] == "paper"]
        projects = [row for row in rows if row["evidence_type"] == "project"]
        return {"relation": relation, "supported": bool(papers and projects),
                "common_paper_count": len(papers), "common_project_count": len(projects),
                "evidence_ids": [row["evidence_id"] for row in rows]}

    def timeline(self, entity_ids: list[str]) -> list[dict]:
        rows = []
        for item in self.cooperation_rows(entity_ids):
            year = item.get("year") if item["evidence_type"] == "paper" else item.get("start_year")
            identity = item.get("paper_id") or item.get("project_id")
            if year is not None:
                rows.append({"year": year, "type": item["evidence_type"], "id": identity,
                             "evidence_id": item["evidence_id"], "source": item["source"]})
        return sorted(rows, key=lambda row: row["year"])

    def exists(self, evidence_id: str, entity_ids: list[str] | None = None) -> bool:
        return self.repository.exists(evidence_id, entity_ids)

    def get(self, evidence_id: str, entity_ids: list[str] | None = None) -> dict | None:
        return self.repository.get(evidence_id, entity_ids)

    def get_batch(self, evidence_ids: list[str], entity_ids: list[str] | None = None) -> list[dict]:
        return self.repository.get_batch(evidence_ids, entity_ids)
