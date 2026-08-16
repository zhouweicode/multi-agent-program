from formatters.graph_formatter import format_graph
from services.evidence_service import EvidenceService
from services.observability import serializable_snapshot
from services.resources import get_graph_service


class FakeAchievementService:
    def get_common_papers(self, entity_ids):
        return [{"paper_id": "p1", "title": "P", "year": 2024, "authors": entity_ids,
                 "evidence_id": "mysql_paper_p1", "source": "mysql:gkx.papers"}]

    def get_common_projects(self, entity_ids):
        return [{"project_id": "j1", "name": "J", "start_year": 2022, "end_year": 2024,
                 "participant_ids": entity_ids, "evidence_id": "mysql_project_j1",
                 "source": "mysql:gkx.projects"}]


def test_verification_evidence_service_uses_active_achievement_service():
    ids = ["person_1", "person_2"]
    service = EvidenceService(FakeAchievementService())
    evidence_ids = ["mysql_paper_p1", "mysql_project_j1"]
    assert service.verify(evidence_ids, ids)["valid"] is True
    assert service.check_sources(evidence_ids, ids)["trusted"] is True
    assert service.relation(ids, "CORE_RESEARCH_PARTNER")["supported"] is True
    assert [row["year"] for row in service.timeline(ids)] == [2022, 2024]


def test_observability_snapshot_redacts_nested_secrets():
    snapshot = serializable_snapshot({"model_api_key": "secret", "nested": {"password": "secret"}, "safe": "ok"})
    assert snapshot["model_api_key"] == "***REDACTED***"
    assert snapshot["nested"]["password"] == "***REDACTED***"
    assert snapshot["safe"] == "ok"


def test_graph_formatter_includes_path_strength():
    result = {"facts": [{"tool": "calculate_path_strength", "data": {
        "strength": 0.56, "path": {"found": True, "nodes": ["a", "b"], "edges": [{}]}
    }}]}
    text, _ = format_graph(result, {"A": "a", "B": "b"})
    assert "0.56" in text


def test_graph_service_is_reused_within_process():
    assert get_graph_service() is get_graph_service()
