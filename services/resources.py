"""进程级 Service/Repository 生命周期容器，避免每次 Tool Call 重建数据库客户端。"""
from dataclasses import replace
from functools import lru_cache

from models.settings import Settings
from services.achievement_service import AchievementService
from services.entity_service import EntityService
from services.graph_service import GraphService
from services.evidence_service import EvidenceService
from services.enterprise_service import EnterpriseService
from services.industry_service import IndustryService
from repositories.neo4j_repository import Neo4jGraphRepository
from kg_workflow.registry import KGWorkflowRegistry

_managed_services: list[object] = []


def active_release_settings(settings: Settings | None = None) -> tuple[Settings, dict | None]:
    """Resolve the serving collection from the atomically activated KG release pointer."""
    settings = settings or Settings.from_env()
    registry = KGWorkflowRegistry(settings.kg_workflow_registry_path)
    try:
        active = registry.active_release()
    finally:
        registry.close()
    if active and active.get("milvus_collection"):
        settings = replace(settings, milvus_collection=active["milvus_collection"])
    return settings, active


@lru_cache(maxsize=4)
def _entity_service(backend: str, milvus_uri: str, collection: str,
                    embedding_provider: str, embedding_dimension: int) -> EntityService:
    settings = replace(Settings.from_env(), entity_backend=backend, milvus_uri=milvus_uri,
                       milvus_collection=collection, embedding_provider=embedding_provider,
                       embedding_dimension=embedding_dimension)
    service = EntityService(settings=settings)
    _managed_services.append(service)
    return service


def get_entity_service() -> EntityService:
    settings, _ = active_release_settings()
    return _entity_service(settings.entity_backend, settings.milvus_uri, settings.milvus_collection,
                           settings.embedding_provider, settings.embedding_dimension)


@lru_cache(maxsize=4)
def _achievement_service(backend: str, database: str) -> AchievementService:
    service = AchievementService()
    _managed_services.append(service)
    return service


def get_achievement_service() -> AchievementService:
    settings = Settings.from_env()
    return _achievement_service(settings.achievement_backend, settings.mysql_database)


@lru_cache(maxsize=4)
def _graph_service(backend: str, uri: str, database: str) -> GraphService:
    service = GraphService(_neo4j_repository(uri, database) if backend == "neo4j" else None)
    _managed_services.append(service)
    return service


@lru_cache(maxsize=2)
def _neo4j_repository(uri: str, database: str) -> Neo4jGraphRepository:
    repository = Neo4jGraphRepository(Settings.from_env())
    _managed_services.append(repository)
    return repository


def get_graph_service() -> GraphService:
    settings = Settings.from_env()
    return _graph_service(settings.graph_backend, settings.neo4j_uri, settings.neo4j_database)


@lru_cache(maxsize=2)
def _enterprise_service(backend: str) -> EnterpriseService:
    settings = Settings.from_env()
    repository = _neo4j_repository(settings.neo4j_uri, settings.neo4j_database) if backend == "neo4j" else None
    service = EnterpriseService(repository)
    _managed_services.append(service)
    return service


def get_enterprise_service() -> EnterpriseService:
    return _enterprise_service(Settings.from_env().enterprise_backend)


@lru_cache(maxsize=2)
def _industry_service(backend: str) -> IndustryService:
    settings = Settings.from_env()
    repository = _neo4j_repository(settings.neo4j_uri, settings.neo4j_database) if backend == "neo4j" else None
    service = IndustryService(repository)
    _managed_services.append(service)
    return service


def get_industry_service() -> IndustryService:
    return _industry_service(Settings.from_env().industry_backend)


@lru_cache(maxsize=4)
def _evidence_service(backend: str, database: str) -> EvidenceService:
    service = EvidenceService(get_achievement_service())
    _managed_services.append(service)
    return service


def get_evidence_service() -> EvidenceService:
    settings = Settings.from_env()
    return _evidence_service(settings.achievement_backend, settings.mysql_database)


def close_resources() -> None:
    """由 FastAPI shutdown 调用，关闭 Milvus/Neo4j 等持久客户端。"""
    for service in _managed_services:
        close = getattr(service, "close", None)
        if close:
            close()
    _managed_services.clear()
    for cached in (_entity_service, _achievement_service, _graph_service, _neo4j_repository, _enterprise_service,
                   _industry_service, _evidence_service):
        cached.cache_clear()
