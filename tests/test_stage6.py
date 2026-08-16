"""第六阶段：统一 ID、Hybrid Embedding、Milvus RRF 与 State 契约测试。"""
from langgraph.types import Command

from graph.builder import build_graph
from repositories.entity_id_mapping_repository import EntityIdMappingRepository
from repositories.milvus_entity_repository import MilvusEntityRepository
from services.embedding_service import DeterministicHybridEmbedding, EmbeddingFactory
from models.settings import Settings
from fastapi.testclient import TestClient
from app.main import app


class FakeSchema:
    def __init__(self):
        self.fields = []

    def add_field(self, name, datatype, **kwargs):
        self.fields.append(name)


class FakeIndexes:
    def __init__(self):
        self.fields = []

    def add_index(self, field_name, **kwargs):
        self.fields.append(field_name)


class FakeMilvusClient:
    def __init__(self):
        self.created = False
        self.rows = []
        self.requests = None
        self.ranker = None

    def has_collection(self, name):
        return self.created

    def create_schema(self, **kwargs):
        self.schema = FakeSchema()
        return self.schema

    def prepare_index_params(self):
        self.indexes = FakeIndexes()
        return self.indexes

    def create_collection(self, name, schema, index_params):
        self.created = True

    def load_collection(self, name):
        self.loaded = name

    def query(self, name, filter, output_fields, **kwargs):
        if output_fields == ["count(*)"]:
            return [{"count(*)": len(self.rows)}]
        wanted = filter.split('"')[1]
        return [row for row in self.rows if row["canonical_id"] == wanted]

    def upsert(self, name, payload):
        self.rows = payload
        return {"upsert_count": len(payload)}

    def hybrid_search(self, name, requests, ranker, limit, output_fields):
        self.requests, self.ranker = requests, ranker
        return [[{"id": "person_zw_001", "distance": 0.032,
                  "entity": {"canonical_id": "person_zw_001", "name": "张伟",
                             "organization": "清华大学", "title": "教授"}}]]

    def close(self):
        pass


def test_entity_id_mapping_translates_mysql_and_neo4j():
    mapping = EntityIdMappingRepository()
    assert mapping.to_backend("person_zw_001", "mysql") == "450e887j"
    assert mapping.to_backend("person_zw_001", "neo4j") == "SCH001"
    assert mapping.to_canonical("SCH001", "neo4j") == "person_zw_001"


def test_unknown_mapping_is_backward_compatible():
    mapping = EntityIdMappingRepository()
    assert mapping.to_backend("new_person_001", "mysql") == "new_person_001"
    assert mapping.to_canonical("new_person_001", "mysql") == "new_person_001"


def test_deterministic_embedding_outputs_dense_and_sparse():
    provider = DeterministicHybridEmbedding(32)
    dense, sparse = provider.encode(["张伟 清华大学", "张伟 清华大学"])
    assert dense[0] == dense[1]
    assert sparse[0] == sparse[1]
    assert len(dense[0]) == 32 and sparse[0]


def test_milvus_repository_builds_two_indexes_and_uses_rrf():
    client = FakeMilvusClient()
    repository = MilvusEntityRepository(embedding=DeterministicHybridEmbedding(32), client=client)
    assert client.schema.fields[-2:] == ["dense_vector", "sparse_vector"]
    assert client.indexes.fields == ["dense_vector", "sparse_vector"]
    assert client.loaded == "scholar_entities"
    repository.upsert_entities([{"entity_id": "person_zw_001", "name": "张伟",
                                 "organization": "清华大学", "title": "教授"}])
    rows = repository.search_scholars("张伟")
    assert len(client.requests) == 2
    assert client.ranker.__class__.__name__ == "RRFRanker"
    assert rows[0]["retrieval_method"] == "dense+sparse+rrf"


def test_entity_resolution_writes_canonical_and_backend_ids_to_state():
    graph = build_graph()
    config = {"configurable": {"thread_id": "stage6-canonical-state"}}
    first = graph.invoke({"question": "张伟发表过哪些论文？", "max_replans": 2, "replan_count": 0}, config=config)
    assert first["__interrupt__"]
    final = graph.invoke(Command(resume={"张伟": "person_zw_001"}), config=config)
    assert final["resolved_entities"]["张伟"] == "person_zw_001"
    assert final["entity_backend_ids"]["张伟"]["neo4j"] == "SCH001"


def test_health_reports_stage7_backends():
    payload = TestClient(app).get("/health").json()
    assert payload["stage"] == 7
    assert {"entity_backend", "graph_backend", "embedding_provider"}.issubset(payload)


def test_embedding_factory_reuses_provider_in_one_process():
    EmbeddingFactory.clear_cache()
    settings = Settings(embedding_provider="mock", embedding_dimension=16)
    assert EmbeddingFactory.create(settings) is EmbeddingFactory.create(settings)
