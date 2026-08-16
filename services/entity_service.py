"""实体检索服务。未来在此替换为 BGE-M3 + Milvus + RRF。"""
from data.mock_entities import MOCK_ENTITIES
from models.settings import Settings
from repositories.mysql_repository import MySQLRepository
from repositories.entity_id_mapping_repository import EntityIdMappingRepository
from repositories.milvus_entity_repository import MilvusEntityRepository


class EntityService:
    def __init__(self, repository=None):
        settings = Settings.from_env()
        self.backend = "mock"
        self.mapping = EntityIdMappingRepository()
        self.repository = repository
        if self.repository is None and settings.entity_backend == "mysql":
            self.repository = MySQLRepository(settings)
            self.backend = "mysql"
        elif self.repository is None and settings.entity_backend == "milvus":
            self.repository = MilvusEntityRepository(settings)
            self.backend = "milvus"
        elif self.repository is not None:
            self.backend = getattr(repository, "backend", "mock")

    def search(self, mention: str) -> list[dict]:
        if self.repository:
            rows = self.repository.search_scholars(mention)
            return ([self.mapping.normalize_candidate(row, "mysql") for row in rows]
                    if self.backend == "mysql" else rows)
        return [item.copy() for item in MOCK_ENTITIES if item["name"] == mention]

    def exists(self, entity_id: str) -> bool:
        if self.repository:
            backend_id = self.mapping.to_backend(entity_id, self.backend)
            return self.repository.get_scholar(backend_id) is not None
        return any(item["entity_id"] == entity_id for item in MOCK_ENTITIES)

    def get(self, entity_id: str) -> dict | None:
        if self.repository:
            backend_id = self.mapping.to_backend(entity_id, self.backend)
            row = self.repository.get_scholar(backend_id)
            return self.mapping.normalize_candidate(row, self.backend) if row and self.backend == "mysql" else row
        return next((x.copy() for x in MOCK_ENTITIES if x["entity_id"] == entity_id), None)

    def close(self) -> None:
        close = getattr(self.repository, "close", None)
        if close:
            close()
