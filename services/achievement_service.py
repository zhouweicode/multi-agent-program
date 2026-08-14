"""科研成果服务：保持 Tool 层稳定，按配置选择 Mock 或 MySQL。"""
from data.mock_achievements import PAPERS, PROJECTS
from models.settings import Settings
from repositories.mysql_repository import MySQLRepository
from repositories.entity_id_mapping_repository import EntityIdMappingRepository


class AchievementService:
    def __init__(self, repository=None):
        settings = Settings.from_env()
        self.backend = "mock"
        self.mapping = EntityIdMappingRepository()
        self.repository = repository
        if self.repository is None and settings.achievement_backend == "mysql":
            self.repository = MySQLRepository(settings)
            self.backend = "mysql"
        elif self.repository is not None:
            self.backend = getattr(repository, "backend", "mock")

    def _mysql_ids(self, entity_ids: list[str]) -> list[str]:
        return [self.mapping.to_backend(entity_id, "mysql") for entity_id in entity_ids]

    def _canonical_papers(self, rows: list[dict]) -> list[dict]:
        for row in rows:
            row["authors"] = [self.mapping.to_canonical(entity_id, "mysql") for entity_id in row.get("authors", [])]
        return rows

    def get_author_papers(self, entity_id: str) -> list[dict]:
        if self.repository:
            backend_id = self.mapping.to_backend(entity_id, "mysql") if self.backend == "mysql" else entity_id
            return self._canonical_papers(self.repository.get_author_papers(backend_id)) if self.backend == "mysql" else self.repository.get_author_papers(backend_id)
        return [paper.copy() for paper in PAPERS if entity_id in paper["authors"]]

    def get_common_papers(self, entity_ids: list[str]) -> list[dict]:
        if self.repository:
            backend_ids = self._mysql_ids(entity_ids) if self.backend == "mysql" else entity_ids
            return self._canonical_papers(self.repository.get_common_papers(backend_ids)) if self.backend == "mysql" else self.repository.get_common_papers(backend_ids)
        wanted = set(entity_ids)
        return [paper.copy() for paper in PAPERS if wanted.issubset(set(paper["authors"]))]

    def get_common_projects(self, entity_ids: list[str]) -> list[dict]:
        # 项目查询将在后续阶段接入 MySQL，当前继续使用 Mock。
        wanted = set(entity_ids)
        return [row.copy() for row in PROJECTS if wanted.issubset(set(row["participant_ids"]))]
