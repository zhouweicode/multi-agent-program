"""Build the complete gkx_synthetic graph in Neo4j.

The command only reads MySQL and is a dry-run unless ``--apply`` is supplied. Neo4j writes
are idempotent because every node and relationship is matched by stable source IDs.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from typing import Any

from models.settings import Settings


@dataclass(frozen=True)
class LoadStep:
    name: str
    select_sql: str
    cypher: str


STEPS = (
    LoadStep("organizations", "SELECT * FROM organizations WHERE status=1 ORDER BY org_id", """
        UNWIND $rows AS row MERGE (n:Organization {org_id: row.org_id})
        SET n.name_zh=row.name_zh, n.name_en=row.name_en, n.org_type=row.org_type,
            n.city=row.city, n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("departments", "SELECT * FROM departments WHERE status=1 ORDER BY dept_id", """
        UNWIND $rows AS row MERGE (n:Department {dept_id: row.dept_id})
        SET n.name_zh=row.name_zh, n.name_en=row.name_en, n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("enterprises", "SELECT * FROM enterprises WHERE status=1 ORDER BY enterprise_id", """
        UNWIND $rows AS row MERGE (n:Enterprise {enterprise_id: row.enterprise_id})
        SET n.name_zh=row.name_zh, n.name_en=row.name_en, n.credit_code=row.credit_code,
            n.city=row.city, n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("industry_segments", "SELECT * FROM industry_segments WHERE status=1 ORDER BY segment_id", """
        UNWIND $rows AS row MERGE (n:IndustrySegment {segment_id: row.segment_id})
        SET n.name_zh=row.name_zh, n.level=row.level, n.parent_segment_id=row.parent_segment_id,
            n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("scholars", "SELECT * FROM dwd_scholar WHERE status=1 ORDER BY scholar_id", """
        UNWIND $rows AS row MERGE (n:Scholar {scholar_id: row.scholar_id})
        SET n.name_zh=row.name_zh, n.name_en=row.name_en, n.organization=row.scholar_org_name_zh,
            n.title=row.work_experience_position_zh, n.orcid=row.orcid,
            n.research_field=row.research_field, n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("papers", "SELECT * FROM dwd_scholar_papers WHERE status=1 ORDER BY id", """
        UNWIND $rows AS row MERGE (n:Paper {paper_id: row.id})
        SET n.title=row.zh_name, n.title_en=row.en_name, n.doi=row.doi, n.venue=row.venue,
            n.publish_date=toString(row.cover_date_start), n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("projects", "SELECT * FROM dwd_zh_project WHERE status=1 ORDER BY id", """
        UNWIND $rows AS row MERGE (n:Project {project_id: row.id})
        SET n.title=row.title, n.start_year=row.approval_year, n.research_period=row.research_period,
            n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("patents", """SELECT p.*, t.title_zh, t.title_localized FROM dwd_patent p
        LEFT JOIN dwd_patent_title t ON t.patent_id=p.patent_id WHERE p.status=1 ORDER BY p.patent_id""", """
        UNWIND $rows AS row MERGE (n:Patent {patent_id: row.patent_id})
        SET n.publication_number=row.publication_number, n.title=row.title_zh,
            n.title_en=row.title_localized, n.application_date=toString(row.application_date),
            n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("industry_events", "SELECT * FROM industry_events WHERE status=1 ORDER BY event_id", """
        UNWIND $rows AS row MERGE (n:IndustryEvent {event_id: row.event_id})
        SET n.segment_id=row.segment_id, n.title=row.title, n.event_date=toString(row.event_date),
            n.importance=toFloat(row.importance), n.evidence_id=row.evidence_id,
            n.release_id=$release_id, n.synthetic=true
    """),
    LoadStep("department_org", "SELECT dept_id,org_id FROM departments WHERE status=1 ORDER BY dept_id", """
        UNWIND $rows AS row MATCH (d:Department {dept_id:row.dept_id}),(o:Organization {org_id:row.org_id})
        MERGE (d)-[r:BELONGS_TO]->(o) SET r.release_id=$release_id, r.synthetic=true
    """),
    LoadStep("scholar_org", "SELECT scholar_id,org_id FROM dwd_scholar WHERE status=1 ORDER BY scholar_id", """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id}),(o:Organization {org_id:row.org_id})
        MERGE (s)-[r:WORKS_AT]->(o) SET r.release_id=$release_id, r.synthetic=true,
            r.evidence_id='syn_employment_'+row.scholar_id
    """),
    LoadStep("scholar_department", "SELECT scholar_id,dept_id FROM dwd_scholar WHERE status=1 ORDER BY scholar_id", """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id}),(d:Department {dept_id:row.dept_id})
        MERGE (s)-[r:MEMBER_OF]->(d) SET r.release_id=$release_id, r.synthetic=true,
            r.evidence_id='syn_department_'+row.scholar_id
    """),
    LoadStep("authorships", "SELECT * FROM dwd_scholar_paper_relation WHERE status=1 ORDER BY id", """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id}),(p:Paper {paper_id:row.related_paper_id})
        MERGE (s)-[r:AUTHOR_OF]->(p) SET r.author_order=row.author_order, r.year=row.year,
            r.evidence_id=row.evidence_id, r.release_id=$release_id, r.synthetic=true
    """),
    LoadStep("project_participation", "SELECT * FROM scholar_project_relation WHERE status=1 ORDER BY id", """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id}),(p:Project {project_id:row.project_id})
        MERGE (s)-[r:PARTICIPATES_IN]->(p) SET r.role=row.role, r.evidence_id=row.evidence_id,
            r.release_id=$release_id, r.synthetic=true
    """),
    LoadStep("inventorships", "SELECT * FROM scholar_patent_relation WHERE status=1 ORDER BY id", """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id}),(p:Patent {patent_id:row.patent_id})
        MERGE (s)-[r:INVENTED]->(p) SET r.inventor_order=row.inventor_order,
            r.evidence_id=row.evidence_id, r.release_id=$release_id, r.synthetic=true
    """),
    LoadStep("patent_assignees", "SELECT patent_id,assignee_enterprise_id FROM dwd_patent WHERE status=1 ORDER BY patent_id", """
        UNWIND $rows AS row MATCH (p:Patent {patent_id:row.patent_id}),(e:Enterprise {enterprise_id:row.assignee_enterprise_id})
        MERGE (p)-[r:ASSIGNED_TO]->(e) SET r.release_id=$release_id, r.synthetic=true
    """),
    LoadStep("scholar_enterprise", "SELECT * FROM scholar_enterprise_relation WHERE status=1 ORDER BY id", """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id}),(e:Enterprise {enterprise_id:row.enterprise_id})
        MERGE (s)-[r:HAS_ENTERPRISE_ROLE]->(e) SET r.role=row.role, r.start_year=row.start_year,
            r.evidence_id=row.evidence_id, r.release_id=$release_id, r.synthetic=true
    """),
    LoadStep("enterprise_industry", "SELECT * FROM enterprise_industry_relation WHERE status=1 ORDER BY id", """
        UNWIND $rows AS row MATCH (e:Enterprise {enterprise_id:row.enterprise_id}),(i:IndustrySegment {segment_id:row.segment_id})
        MERGE (e)-[r:BELONGS_TO]->(i) SET r.release_id=$release_id, r.synthetic=true
    """),
    LoadStep("industry_hierarchy", "SELECT segment_id,parent_segment_id FROM industry_segments WHERE status=1 AND parent_segment_id IS NOT NULL ORDER BY segment_id", """
        UNWIND $rows AS row MATCH (c:IndustrySegment {segment_id:row.segment_id}),(p:IndustrySegment {segment_id:row.parent_segment_id})
        MERGE (c)-[r:SUBSEGMENT_OF]->(p) SET r.release_id=$release_id, r.synthetic=true
    """),
    LoadStep("industry_event_links", "SELECT event_id,segment_id,evidence_id FROM industry_events WHERE status=1 ORDER BY event_id", """
        UNWIND $rows AS row MATCH (i:IndustrySegment {segment_id:row.segment_id}),(e:IndustryEvent {event_id:row.event_id})
        MERGE (i)-[r:HAS_EVENT]->(e) SET r.evidence_id=row.evidence_id,
            r.release_id=$release_id, r.synthetic=true
    """),
)


CONSTRAINTS = (
    "CREATE CONSTRAINT synthetic_org_id IF NOT EXISTS FOR (n:Organization) REQUIRE n.org_id IS UNIQUE",
    "CREATE CONSTRAINT synthetic_dept_id IF NOT EXISTS FOR (n:Department) REQUIRE n.dept_id IS UNIQUE",
    "CREATE CONSTRAINT synthetic_enterprise_id IF NOT EXISTS FOR (n:Enterprise) REQUIRE n.enterprise_id IS UNIQUE",
    "CREATE CONSTRAINT synthetic_segment_id IF NOT EXISTS FOR (n:IndustrySegment) REQUIRE n.segment_id IS UNIQUE",
    "CREATE CONSTRAINT synthetic_scholar_id IF NOT EXISTS FOR (n:Scholar) REQUIRE n.scholar_id IS UNIQUE",
    "CREATE CONSTRAINT synthetic_paper_id IF NOT EXISTS FOR (n:Paper) REQUIRE n.paper_id IS UNIQUE",
    "CREATE CONSTRAINT synthetic_project_id IF NOT EXISTS FOR (n:Project) REQUIRE n.project_id IS UNIQUE",
    "CREATE CONSTRAINT synthetic_patent_id IF NOT EXISTS FOR (n:Patent) REQUIRE n.patent_id IS UNIQUE",
    "CREATE CONSTRAINT synthetic_event_id IF NOT EXISTS FOR (n:IndustryEvent) REQUIRE n.event_id IS UNIQUE",
)


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    return {key: (str(value) if value is not None and value.__class__.__module__.startswith(("datetime", "decimal")) else value)
            for key, value in row.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="gkx_synthetic -> 完整 Neo4j 图谱；默认只预览")
    parser.add_argument("--release-id", default="gkx-synthetic-v1")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.mysql_database != "gkx_synthetic" and not settings.mysql_database.startswith("gkx_synthetic_"):
        raise SystemExit("拒绝运行：MYSQL_DATABASE 必须为 gkx_synthetic 或 gkx_synthetic_*")
    import pymysql
    mysql = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
        password=settings.mysql_password, database=settings.mysql_database, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )
    neo_driver = None
    counts: dict[str, int] = {}
    try:
        with mysql.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            if args.apply:
                if not settings.neo4j_password:
                    raise ValueError("NEO4J_PASSWORD 未配置")
                from neo4j import GraphDatabase
                neo_driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
                neo_driver.verify_connectivity()
                with neo_driver.session(database=settings.neo4j_database) as session:
                    for constraint in CONSTRAINTS:
                        session.run(constraint).consume()
            for step in STEPS:
                cursor.execute(step.select_sql)
                count = 0
                while rows := cursor.fetchmany(max(1, args.batch_size)):
                    payload = [_serialize(row) for row in rows]
                    if neo_driver:
                        with neo_driver.session(database=settings.neo4j_database) as session:
                            session.execute_write(lambda tx, q=step.cypher, p=payload:
                                                  tx.run(q, rows=p, release_id=args.release_id).consume())
                    count += len(payload)
                counts[step.name] = count
        mysql.rollback()
    finally:
        mysql.close()
        if neo_driver:
            neo_driver.close()
    print(json.dumps({"dry_run": not args.apply, "release_id": args.release_id,
                      "mysql_database": settings.mysql_database, "steps": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
