"""企业关系服务：按配置使用共享 Neo4j Repository 或 Mock 数据。"""
from data.mock_enterprises import COMPANIES, PERSON_COMPANY_ROLES, COMPANY_PROJECTS, COMPANY_PATENTS
from repositories.entity_id_mapping_repository import EntityIdMappingRepository


class EnterpriseService:
    def __init__(self, repository=None):
        self.repository = repository
        self.backend = "neo4j" if repository else "mock"
        self.mapping = EntityIdMappingRepository()

    def health(self) -> dict:
        health = getattr(self.repository, "health", None)
        return health() if health else {"backend": self.backend, "ready": True}

    @staticmethod
    def _company_name(company_id: str) -> str:
        return next((row["name"] for row in COMPANIES if row["company_id"] == company_id), company_id)

    def get_person_company_roles(self, entity_ids: list[str]) -> list[dict]:
        if self.repository:
            backend_ids = [self.mapping.to_backend(item, "neo4j") for item in entity_ids]
            rows = self.repository.get_person_company_roles(backend_ids)
            for row in rows:
                row["entity_id"] = self.mapping.to_canonical(row["entity_id"], "neo4j")
            return rows
        wanted = set(entity_ids)
        return [{**row, "company_name": self._company_name(row["company_id"]), "source": "mock:enterprise_roles"}
                for row in PERSON_COMPANY_ROLES if row["entity_id"] in wanted]

    def get_company_projects(self, company_id: str) -> list[dict]:
        if self.repository:
            rows = self.repository.get_company_projects(company_id)
            for row in rows:
                row["participant_ids"] = [self.mapping.to_canonical(item, "neo4j") for item in row.get("participant_ids", [])]
            return rows
        return [{**row, "company_name": self._company_name(row["company_id"]), "source": "mock:company_projects"}
                for row in COMPANY_PROJECTS if row["company_id"] == company_id]

    def get_company_patents(self, company_id: str) -> list[dict]:
        if self.repository:
            rows = self.repository.get_company_patents(company_id)
            for row in rows:
                row["inventor_ids"] = [self.mapping.to_canonical(item, "neo4j") for item in row.get("inventor_ids", [])]
            return rows
        return [{**row, "company_name": self._company_name(row["company_id"]), "source": "mock:company_patents"}
                for row in COMPANY_PATENTS if row["company_id"] == company_id]
