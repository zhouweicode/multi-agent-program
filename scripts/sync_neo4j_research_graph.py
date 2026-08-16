"""把 MySQL 学者与科研成果幂等同步到 Neo4j。

默认只预览；显式传入 --apply 才执行 MERGE 写入。
"""
from __future__ import annotations

import argparse

from models.settings import Settings
from repositories.mysql_repository import MySQLRepository
from repositories.neo4j_repository import Neo4jGraphRepository


def collect(mysql: MySQLRepository, limit: int) -> tuple[list[dict], list[dict]]:
    scholars = mysql.list_scholars(limit=limit)
    authorships = []
    for scholar in scholars:
        for paper in mysql.get_author_papers(scholar["entity_id"], limit=500):
            authorships.append({"scholar_id": scholar["entity_id"], **paper})
    return scholars, authorships


def sync(neo4j: Neo4jGraphRepository, scholars: list[dict], authorships: list[dict], batch_id: str) -> dict:
    scholar_query = """
        UNWIND $rows AS row
        MERGE (s:Scholar {scholar_id: row.entity_id})
        SET s.name = row.name, s.organization = row.organization, s.title = row.title,
            s.import_batch = $batch_id
    """
    paper_query = """
        UNWIND $rows AS row
        MATCH (s:Scholar {scholar_id: row.scholar_id})
        MERGE (p:Paper {paper_id: row.paper_id})
        SET p.title = row.title, p.year = row.year, p.doi = row.doi,
            p.import_batch = $batch_id
        MERGE (s)-[r:AUTHOR_OF]->(p)
        SET r.evidence_id = row.evidence_id, r.source = row.source,
            r.import_batch = $batch_id
    """
    with neo4j.driver.session(database=neo4j.settings.neo4j_database) as session:
        session.execute_write(lambda tx: tx.run(scholar_query, rows=scholars, batch_id=batch_id).consume())
        session.execute_write(lambda tx: tx.run(paper_query, rows=authorships, batch_id=batch_id).consume())
    return {"scholar_count": len(scholars), "authorship_count": len(authorships), "batch_id": batch_id}


def main() -> None:
    parser = argparse.ArgumentParser(description="MySQL -> Neo4j 科研图谱增量同步")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--batch-id", default="stage9-manual")
    parser.add_argument("--apply", action="store_true", help="实际写入；缺省仅预览统计")
    args = parser.parse_args()
    settings = Settings.from_env()
    mysql = MySQLRepository(settings)
    neo4j = Neo4jGraphRepository(settings) if args.apply else None
    scholars, authorships = collect(mysql, max(1, args.limit))
    try:
        result = (sync(neo4j, scholars, authorships, args.batch_id) if neo4j else
                  {"dry_run": True, "scholar_count": len(scholars),
                   "authorship_count": len(authorships), "batch_id": args.batch_id})
        print(result)
    finally:
        if neo4j:
            neo4j.close()


if __name__ == "__main__":
    main()
