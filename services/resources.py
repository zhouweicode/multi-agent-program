"""进程级 Service/Repository 生命周期容器，避免每次 Tool Call 重建数据库客户端。"""
from functools import lru_cache

from models.settings import Settings
from services.achievement_service import AchievementService
from services.entity_service import EntityService
from services.graph_service import GraphService

_managed_services: list[object] = []


@lru_cache(maxsize=4)
def _entity_service(backend: str, milvus_uri: str, collection: str) -> EntityService:
    service = EntityService()
    _managed_services.append(service)
    return service


def get_entity_service() -> EntityService:
    settings = Settings.from_env()
    return _entity_service(settings.entity_backend, settings.milvus_uri, settings.milvus_collection)


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
    service = GraphService()
    _managed_services.append(service)
    return service


def get_graph_service() -> GraphService:
    settings = Settings.from_env()
    return _graph_service(settings.graph_backend, settings.neo4j_uri, settings.neo4j_database)


def close_resources() -> None:
    """由 FastAPI shutdown 调用，关闭 Milvus/Neo4j 等持久客户端。"""
    for service in _managed_services:
        close = getattr(service, "close", None)
        if close:
            close()
    _managed_services.clear()
    for cached in (_entity_service, _achievement_service, _graph_service):
        cached.cache_clear()
