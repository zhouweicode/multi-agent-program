"""第九阶段：统一证据、真实领域 Repository 适配与健康探针。"""
from fastapi.testclient import TestClient

from app.main import app
from services.enterprise_service import EnterpriseService
from services.evidence_normalizer import normalize_tool_output
from services.industry_service import IndustryService


class FakeDomainGraphRepository:
    def health(self):
        return {"backend": "neo4j", "ready": True}

    def get_person_company_roles(self, entity_ids):
        return [{"entity_id": entity_ids[0], "company_id": "c1", "company_name": "测试企业",
                 "role": "顾问", "start_year": 2020, "evidence_id": "neo4j_role_1",
                 "source": "neo4j:Scholar-Enterprise"}]

    def get_company_projects(self, company_id):
        return [{"project_id": "p1", "company_id": company_id, "name": "项目",
                 "participant_ids": ["SCH001"], "start_year": 2020, "end_year": 2022,
                 "evidence_id": "neo4j_project_1", "source": "neo4j:Enterprise-Project"}]

    def get_company_patents(self, company_id):
        return [{"patent_id": "x1", "company_id": company_id, "title": "专利",
                 "inventor_ids": ["SCH001"], "evidence_id": "neo4j_patent_1",
                 "source": "neo4j:Enterprise-Patent"}]

    def get_chain_structure(self, chain_id):
        return {"chain_id": chain_id, "name": "测试产业链", "nodes": ["n1"], "node_details": []}

    def get_node_companies(self, node_id):
        return [{"company_id": "c1", "name": "测试企业"}]

    def get_node_events(self, node_id):
        return [{"event_id": "e1", "node_id": node_id, "title": "事件", "importance": 90,
                 "evidence_id": "neo4j_event_1", "source": "neo4j:IndustrySegment-IndustryEvent"}]


def test_tool_observation_becomes_complete_evidence_record():
    records = normalize_tool_output("get_common_papers", [{"paper_id": "paper_1", "title": "论文",
        "year": 2025, "authors": ["p1", "p2"], "evidence_id": "mysql_paper_1",
        "source": "mysql:gkx.dwd_scholar_papers"}], ["p1", "p2"])
    assert records[0]["fact_type"] == "common_paper"
    assert records[0]["source_type"] == "mysql"
    assert records[0]["source_record_id"] == "paper_1"
    assert records[0]["entity_ids"] == ["p1", "p2"]
    assert records[0]["content"]["title"] == "论文"


def test_graph_path_edges_become_neo4j_evidence():
    output = {"found": True, "edges": [{"source": "p1", "target": "p2", "relation": "COAUTHOR",
        "evidence_id": "neo4j_relation_1", "source_backend": "neo4j:relationship"}]}
    records = normalize_tool_output("find_path", output, ["p1", "p2"])
    assert records[0]["source_type"] == "neo4j"
    assert records[0]["fact_type"] == "graph_path"


def test_enterprise_service_delegates_to_neo4j_and_normalizes_ids():
    service = EnterpriseService(FakeDomainGraphRepository())
    roles = service.get_person_company_roles(["person_zw_001"])
    assert roles[0]["entity_id"] == "person_zw_001"
    assert service.get_company_projects("c1")[0]["participant_ids"] == ["person_zw_001"]
    assert service.get_company_patents("c1")[0]["inventor_ids"] == ["person_zw_001"]


def test_industry_service_delegates_to_neo4j():
    service = IndustryService(FakeDomainGraphRepository())
    assert service.get_chain_structure("chain_1")["name"] == "测试产业链"
    assert service.get_node_companies("n1")[0]["company_id"] == "c1"
    assert service.get_node_events("n1")[0]["evidence_id"] == "neo4j_event_1"


def test_health_exposes_stage9_domain_backends():
    payload = TestClient(app).get("/health").json()
    assert payload["stage"] == 9
    assert "enterprise_backend" in payload
    assert "industry_backend" in payload
