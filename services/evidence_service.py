"""统一证据服务；Verification 不直接依赖 Mock 常量或具体数据库。"""
from services.achievement_service import AchievementService
from services.resources import get_achievement_service


class EvidenceService:
    def __init__(self, achievement_service: AchievementService | None = None):
        self.achievements = achievement_service or get_achievement_service()

    def cooperation_rows(self, entity_ids: list[str]) -> list[dict]:
        """从 AchievementService 当前后端获取同一批科研合作证据。"""
        papers = [{**row, "evidence_type": "paper", "source": row.get("source", "mock:papers")}
                  for row in self.achievements.get_common_papers(entity_ids)]
        projects = [{**row, "evidence_type": "project", "source": row.get("source", "mock:projects")}
                    for row in self.achievements.get_common_projects(entity_ids)]
        return papers + projects

    def _available(self, entity_ids: list[str] | None = None) -> dict[str, dict]:
        rows = self.cooperation_rows(entity_ids) if entity_ids else []
        return {row["evidence_id"]: row for row in rows if row.get("evidence_id")}

    def verify(self, evidence_ids: list[str], entity_ids: list[str] | None = None) -> dict:
        available = self._available(entity_ids)
        non_research_prefixes = ("ev_employment_", "ev_role_", "ev_company_", "ev_event_", "ev_graph_")
        missing = [evidence_id for evidence_id in evidence_ids
                   if evidence_id not in available and not evidence_id.startswith(non_research_prefixes)]
        return {"valid": bool(evidence_ids) and not missing, "checked_count": len(evidence_ids), "missing": missing}

    def check_sources(self, evidence_ids: list[str], entity_ids: list[str] | None = None) -> dict:
        available = self._available(entity_ids)
        trusted_sources = ("mock:", "mysql:")
        untrusted = [evidence_id for evidence_id in evidence_ids
                     if evidence_id not in available or not str(available[evidence_id].get("source", "")).startswith(trusted_sources)]
        sources = sorted({available[item]["source"] for item in evidence_ids if item in available})
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
        non_research_prefixes = ("ev_employment_", "ev_role_", "ev_company_", "ev_event_", "ev_graph_")
        return evidence_id in self._available(entity_ids) or evidence_id.startswith(non_research_prefixes)

    def get(self, evidence_id: str, entity_ids: list[str] | None = None) -> dict | None:
        return self._available(entity_ids).get(evidence_id)
