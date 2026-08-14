"""实体检索服务。未来在此替换为 BGE-M3 + Milvus + RRF。"""
from data.mock_entities import MOCK_ENTITIES


class EntityService:
    def search(self, mention: str) -> list[dict]:
        return [item.copy() for item in MOCK_ENTITIES if item["name"] == mention]

    def exists(self, entity_id: str) -> bool:
        return any(item["entity_id"] == entity_id for item in MOCK_ENTITIES)

    def get(self, entity_id: str) -> dict | None:
        return next((x.copy() for x in MOCK_ENTITIES if x["entity_id"] == entity_id), None)

