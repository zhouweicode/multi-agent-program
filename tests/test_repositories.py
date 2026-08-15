"""仓储切换测试不依赖本机数据库，CI 默认仍可直接运行。"""
from services.achievement_service import AchievementService
from services.entity_service import EntityService
from services.graph_service import GraphService
from repositories.mysql_repository import MySQLRepository


class FakeMySQLRepository:
    def search_scholars(self, mention):
        return [{"entity_id": "mysql_001", "name": mention, "organization": "测试机构", "title": "教授"}]

    def get_scholar(self, scholar_id):
        return {"entity_id": scholar_id, "name": "张伟", "organization": "测试机构", "title": "教授"} if scholar_id == "mysql_001" else None

    def get_author_papers(self, scholar_id):
        return [{"paper_id": "1", "title": "论文", "year": 2025, "authors": [scholar_id], "evidence_id": "mysql_paper_1"}]

    def get_common_papers(self, scholar_ids):
        return [{"paper_id": "2", "title": "共同论文", "year": 2024, "authors": scholar_ids, "evidence_id": "mysql_paper_2"}]


class FakeNeo4jRepository:
    def health(self):
        return {"backend": "neo4j", "ready": True}

    def get_neighbors(self, entity_id):
        return [{"entity_id": "SCH002", "source": entity_id, "target": "SCH002", "relation": "COOPERATES_WITH", "weight": 0.8}]

    def find_path(self, source_id, target_id, max_hops=4):
        return {"found": True, "nodes": [source_id, target_id], "edges": [{"source": source_id, "target": target_id, "weight": 0.8}], "hop_count": 1}

    def k_hop_expand(self, entity_id, k=2):
        return {"start_entity_id": entity_id, "k": k, "levels": [{"hop": 1, "entity_ids": ["SCH002"]}]}

    def calculate_path_strength(self, source_id, target_id):
        return {"source_id": source_id, "target_id": target_id, "strength": 0.8, "path": self.find_path(source_id, target_id)}


def test_entity_service_delegates_to_mysql_repository():
    service = EntityService(FakeMySQLRepository())
    assert service.search("张伟")[0]["entity_id"] == "mysql_001"
    assert service.exists("mysql_001") is True


def test_achievement_service_delegates_author_papers():
    rows = AchievementService(FakeMySQLRepository()).get_author_papers("mysql_001")
    assert rows[0]["evidence_id"] == "mysql_paper_1"


def test_achievement_service_delegates_common_papers():
    rows = AchievementService(FakeMySQLRepository()).get_common_papers(["mysql_001", "mysql_002"])
    assert rows[0]["authors"] == ["mysql_001", "mysql_002"]


def test_graph_service_delegates_four_operations():
    service = GraphService(FakeNeo4jRepository())
    assert service.get_neighbors("SCH001")[0]["entity_id"] == "SCH002"
    assert service.find_path("SCH001", "SCH002")["hop_count"] == 1
    assert service.k_hop_expand("SCH001", 2)["levels"][0]["entity_ids"] == ["SCH002"]
    assert service.calculate_path_strength("SCH001", "SCH002")["strength"] == 0.8


def test_default_services_keep_mock_backend(monkeypatch):
    for key in ("ENTITY_BACKEND", "ACHIEVEMENT_BACKEND", "GRAPH_BACKEND"):
        monkeypatch.setenv(key, "mock")
    assert EntityService().search("张伟")
    assert AchievementService().get_author_papers("person_zw_001")
    assert GraphService().health() == {"backend": "mock", "ready": True}


def test_mysql_repository_repairs_legacy_mojibake():
    assert MySQLRepository._repair_text("é\x9d¢å\x90‘ç§‘æŠ€") == "面向科技"
