"""统一实体 ID 仓储：隔离 canonical ID 与各数据库内部 ID。"""
from data.mock_entity_mappings import ENTITY_ID_MAPPINGS


class EntityIdMappingRepository:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or ENTITY_ID_MAPPINGS
        self.by_canonical = {row["canonical_id"]: row for row in self.rows}
        self.by_backend = {
            (backend, backend_id): row["canonical_id"]
            for row in self.rows
            for backend, backend_id in row.items()
            if backend != "canonical_id" and backend_id
        }

    def to_backend(self, canonical_id: str, backend: str) -> str:
        """无显式映射时保持原 ID，便于渐进迁移和新增实体。"""
        return self.by_canonical.get(canonical_id, {}).get(backend, canonical_id)

    def to_canonical(self, backend_id: str, backend: str) -> str:
        return self.by_backend.get((backend, backend_id), backend_id)

    def backend_ids(self, canonical_id: str) -> dict[str, str]:
        row = self.by_canonical.get(canonical_id, {})
        return {key: value for key, value in row.items() if key != "canonical_id" and value}

    def normalize_candidate(self, candidate: dict, backend: str) -> dict:
        row = candidate.copy()
        backend_id = row["entity_id"]
        canonical_id = self.to_canonical(backend_id, backend)
        row["entity_id"] = canonical_id
        row["canonical_id"] = canonical_id
        row["backend_ids"] = {**self.backend_ids(canonical_id), backend: backend_id}
        return row
