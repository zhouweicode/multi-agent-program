"""将 Mock 或 MySQL 学者同步到 Milvus；这是数据准备脚本，不属于 Agent。"""
import argparse

from data.mock_entities import MOCK_ENTITIES
from repositories.entity_id_mapping_repository import EntityIdMappingRepository
from repositories.milvus_entity_repository import MilvusEntityRepository
from repositories.mysql_repository import MySQLRepository


def load_mysql(limit: int) -> list[dict]:
    mapping = EntityIdMappingRepository()
    rows, offset = [], 0
    repository = MySQLRepository()
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
    args = parser.parse_args()
    rows = MOCK_ENTITIES if args.source == "mock" else load_mysql(args.limit)
    repository = MilvusEntityRepository()
    count = repository.upsert_entities(rows)
    print({"source": args.source, "upsert_count": count, "collection_count": repository.count()})
    repository.close()


if __name__ == "__main__":
    main()
