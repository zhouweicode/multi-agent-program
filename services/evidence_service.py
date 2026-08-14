"""证据服务；当前只校验 Mock evidence_id。"""
from data.mock_achievements import PAPERS, PROJECTS


class EvidenceService:
    def exists(self, evidence_id: str) -> bool:
        prefixes = ("ev_employment_", "ev_role_", "ev_company_", "ev_event_", "ev_graph_", "mysql_paper_")
        return any(row["evidence_id"] == evidence_id for row in PAPERS + PROJECTS) or evidence_id.startswith(prefixes)

    def get(self, evidence_id: str) -> dict | None:
        row = next((item for item in PAPERS + PROJECTS if item["evidence_id"] == evidence_id), None)
        return row.copy() if row else None
