"""将 Mock 或 MySQL 学者同步到 Milvus；这是数据准备脚本，不属于 Agent。"""
import argparse
from dataclasses import replace

from data.mock_entities import MOCK_ENTITIES
from repositories.entity_id_mapping_repository import EntityIdMappingRepository
from repositories.milvus_entity_repository import MilvusEntityRepository
from repositories.mysql_repository import MySQLRepository
from models.settings import Settings


def load_mysql(limit: int, settings: Settings | None = None) -> list[dict]:
    mapping = EntityIdMappingRepository()
    rows, offset = [], 0
    repository = MySQLRepository(settings)
    while len(rows) < limit:
        batch = repository.list_scholars(min(1000, limit - len(rows)), offset)
        if not batch:
            break
        rows.extend(mapping.normalize_candidate(row, "mysql") for row in batch)
        offset += len(batch)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="同步实体到 Milvus scholar_entities Collection")
    parser.add_argument("--source", choices=("mock", "mysql"), default="mock")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--collection", help="覆盖 MILVUS_COLLECTION，建议 synthetic 使用独立 collection")
    parser.add_argument("--embedding-provider", choices=("mock", "bge_m3"), help="覆盖 EMBEDDING_PROVIDER")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.collection:
        settings = replace(settings, milvus_collection=args.collection)
    if args.embedding_provider:
        settings = replace(settings, embedding_provider=args.embedding_provider)
    rows = MOCK_ENTITIES if args.source == "mock" else load_mysql(args.limit, settings)
    repository = MilvusEntityRepository(settings)
    count = repository.upsert_entities(rows)
    print({"source": args.source, "upsert_count": count, "collection_count": repository.count()})
    repository.close()


if __name__ == "__main__":
    main()
