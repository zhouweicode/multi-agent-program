"""Migrate the complete MySQL ``gkx`` database into a dedicated Neo4j database.

Two complementary graph layers are written:

1. A lossless provenance layer with one ``SourceRecord`` per MySQL row and one
   ``SourceTable`` per MySQL table.  The original row is retained as canonical JSON.
2. A serving layer using the labels and relationships consumed by
   ``Neo4jGraphRepository`` (Scholar, Enterprise, Paper, Project, Patent,
   IndustrySegment, IndustryEvent, and their relationships).

The default mode is a read-only preview.  Applying is idempotent and requires an exact
target-database confirmation.  It never deletes nodes, relationships, databases, or the
legacy ``neo4j`` database.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.settings import Settings
from scripts.seed_gkx_excel51 import _connect


SOURCE_DATABASE = "gkx"
DEFAULT_TARGET_DATABASE = "gkx"
DEFAULT_RELEASE_ID = "gkx-v1"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class GraphStep:
    name: str
    select_sql: str
    cypher: str


CONSTRAINTS = (
    "CREATE CONSTRAINT gkx_source_table_unique IF NOT EXISTS FOR (n:SourceTable) REQUIRE n.table_name IS UNIQUE",
    "CREATE CONSTRAINT gkx_source_record_unique IF NOT EXISTS FOR (n:SourceRecord) REQUIRE n.record_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_scholar_unique IF NOT EXISTS FOR (n:Scholar) REQUIRE n.scholar_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_org_unique IF NOT EXISTS FOR (n:Organization) REQUIRE n.org_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_enterprise_unique IF NOT EXISTS FOR (n:Enterprise) REQUIRE n.enterprise_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_enterprise_uscc_unique IF NOT EXISTS FOR (n:Enterprise) REQUIRE n.uscc IS UNIQUE",
    "CREATE CONSTRAINT gkx_paper_unique IF NOT EXISTS FOR (n:Paper) REQUIRE n.paper_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_project_unique IF NOT EXISTS FOR (n:Project) REQUIRE n.project_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_patent_unique IF NOT EXISTS FOR (n:Patent) REQUIRE n.patent_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_segment_unique IF NOT EXISTS FOR (n:IndustrySegment) REQUIRE n.segment_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_event_unique IF NOT EXISTS FOR (n:IndustryEvent) REQUIRE n.event_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_technology_unique IF NOT EXISTS FOR (n:Technology) REQUIRE n.tech_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_employment_unique IF NOT EXISTS FOR (n:Employment) REQUIRE n.employment_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_education_unique IF NOT EXISTS FOR (n:Education) REQUIRE n.education_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_school_unique IF NOT EXISTS FOR (n:School) REQUIRE n.school_id IS UNIQUE",
    "CREATE CONSTRAINT gkx_report_unique IF NOT EXISTS FOR (n:TrendReport) REQUIRE n.report_id IS UNIQUE",
)


NODE_STEPS = (
    GraphStep("enterprises", """
        SELECT org_id, name_cn, external_id, province, city, area, address, org_type,
               reg_status, industry, industry_l1_name, industry_l2_name,
               incorporation_year, registered_capital_value, data_source
          FROM dwd_org_base_info ORDER BY org_id
    """, """
        UNWIND $rows AS row
        MERGE (n:Organization:Enterprise {org_id: row.org_id})
        SET n.enterprise_id=row.org_id, n.name_zh=row.name_cn, n.name=row.name_cn,
            n.uscc=row.external_id, n.credit_code=row.external_id,
            n.province=row.province, n.city=row.city, n.area=row.area, n.address=row.address,
            n.org_type=row.org_type, n.status=row.reg_status, n.industry=row.industry,
            n.industry_l1=row.industry_l1_name, n.industry_l2=row.industry_l2_name,
            n.incorporation_year=toInteger(row.incorporation_year),
            n.registered_capital=toFloat(row.registered_capital_value),
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("work_organizations", """
        SELECT DISTINCT CONCAT('WORKORG:', SHA2(org_name_zh, 256)) AS org_id,
               org_name_zh AS name_zh, org_name_en AS name_en
          FROM dwd_scholar_work_experience
         WHERE org_name_zh IS NOT NULL AND org_name_zh <> '' ORDER BY org_id
    """, """
        UNWIND $rows AS row
        MERGE (n:Organization {org_id: row.org_id})
        SET n.name_zh=row.name_zh, n.name_en=row.name_en, n.organization_kind='work_history',
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("schools", """
        SELECT DISTINCT CONCAT('SCHOOL:', SHA2(org_name_zh, 256)) AS school_id,
               org_name_zh AS name_zh, org_name_en AS name_en
          FROM dwd_scholar_education_background
         WHERE org_name_zh IS NOT NULL AND org_name_zh <> '' ORDER BY school_id
    """, """
        UNWIND $rows AS row
        MERGE (n:School {school_id: row.school_id})
        SET n.name_zh=row.name_zh, n.name_en=row.name_en,
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("scholars", """
        SELECT scholar_id, name_en, name_zh, scholar_org_name_en, scholar_org_name_zh,
               bio_zh, work_experience_position_zh, education_background_degree_zh,
               paper_nums, citation_nums, h_index
          FROM dwd_scholar WHERE status=1 ORDER BY scholar_id
    """, """
        UNWIND $rows AS row
        MERGE (n:Scholar {scholar_id: row.scholar_id})
        SET n.name_zh=row.name_zh, n.name_en=row.name_en,
            n.organization=row.scholar_org_name_zh, n.organization_en=row.scholar_org_name_en,
            n.bio=row.bio_zh, n.title=row.work_experience_position_zh,
            n.degree=row.education_background_degree_zh,
            n.paper_count=toInteger(row.paper_nums), n.citation_count=toInteger(row.citation_nums),
            n.h_index=toInteger(row.h_index), n.source_database=$source_database,
            n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("papers", """
        SELECT id, zh_name, en_name, authors, paper_url, cover_date_start, zh_abstract,
               en_abstract, doi, publication_en_name
          FROM dwd_scholar_papers WHERE status=1 ORDER BY id
    """, """
        UNWIND $rows AS row
        MERGE (n:Paper {paper_id: toString(row.id)})
        SET n.title=row.zh_name, n.title_en=row.en_name, n.authors=row.authors,
            n.url=row.paper_url, n.publish_date=toString(row.cover_date_start),
            n.abstract=row.zh_abstract, n.abstract_en=row.en_abstract, n.doi=row.doi,
            n.venue=row.publication_en_name, n.source_database=$source_database,
            n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("projects", """
        SELECT id, project_number, title, project_source, funded_institution, project_level,
               funded_amount, discipline, fund_category, approval_year, approval_time,
               research_period, project_host, keywords, abstract, project_page_url
          FROM dwd_zh_project ORDER BY id
    """, """
        UNWIND $rows AS row
        MERGE (n:Project {project_id: row.id})
        SET n.project_no=row.project_number, n.title=row.title, n.source=row.project_source,
            n.funded_institution=row.funded_institution, n.level=row.project_level,
            n.funded_amount=toFloat(row.funded_amount), n.discipline=row.discipline,
            n.fund_category=row.fund_category, n.start_year=toInteger(row.approval_year),
            n.approval_time=toString(row.approval_time), n.research_period=row.research_period,
            n.project_host=row.project_host, n.keywords=row.keywords, n.abstract=row.abstract,
            n.url=row.project_page_url, n.source_database=$source_database,
            n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("patents", """
        SELECT patent_id, publication_number, application_kind, country_code, country,
               first_applicant_name, first_current_assignee_name, first_inventor_name,
               main_classification_ipcr, main_classification_cpc, keywords, claims,
               description, language, value, db_source
          FROM dwd_patent ORDER BY patent_id
    """, """
        UNWIND $rows AS row
        MERGE (n:Patent {patent_id: row.patent_id})
        SET n.patent_no=row.publication_number, n.publication_number=row.publication_number,
            n.application_no=row.publication_number, n.application_kind=row.application_kind,
            n.country_code=row.country_code, n.country=row.country,
            n.applicant=row.first_applicant_name, n.assignee=row.first_current_assignee_name,
            n.first_inventor=row.first_inventor_name, n.ipcr=row.main_classification_ipcr,
            n.cpc=row.main_classification_cpc, n.keywords=row.keywords, n.claims=row.claims,
            n.description=row.description, n.language=row.language, n.value=toFloat(row.value),
            n.title=coalesce(row.publication_number,row.patent_id),
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("industry_segments", """
        SELECT chain_code, chain_name, node_id, node_name, node_type, level, node_seq,
               parent_id, parent_name, node_imp_level, downstream_link_code, node_stage,
               node_path, data_source
          FROM dwd_industry_chain_info ORDER BY node_seq, chain_code
    """, """
        UNWIND $rows AS row
        MERGE (n:IndustrySegment {segment_id: row.node_id})
        SET n.name_zh=row.node_name, n.name=row.node_name, n.chain_id=row.chain_code,
            n.chain_name=row.chain_name, n.node_type=toInteger(row.node_type),
            n.level=toInteger(row.level), n.node_seq=toInteger(row.node_seq),
            n.parent_segment_id=row.parent_id, n.parent_name=row.parent_name,
            n.importance=toFloat(row.node_imp_level), n.downstream_segment_id=row.downstream_link_code,
            n.stage=toInteger(row.node_stage), n.path=row.node_path,
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("industry_events", """
        SELECT q.news_id, q.title, q.relaese_date, q.summary, q.source,
               q.chain_code, q.chain_name, n.node_id AS segment_id,
               MOD(CAST(RIGHT(q.news_id, 8) AS UNSIGNED), 100) + 1 AS importance
          FROM (SELECT x.*, ROW_NUMBER() OVER (ORDER BY news_id) AS rn
                  FROM dwd_industry_chain_news_info x) q
          JOIN (SELECT x.node_id, ROW_NUMBER() OVER (ORDER BY node_seq, chain_code) AS rn
                  FROM dwd_industry_chain_info x) n ON n.rn=q.rn
         ORDER BY q.news_id
    """, """
        UNWIND $rows AS row
        MERGE (n:IndustryEvent {event_id: row.news_id})
        SET n.title=row.title, n.event_date=toString(row.relaese_date), n.date=toString(row.relaese_date),
            n.summary=row.summary, n.source=row.source, n.segment_id=row.segment_id,
            n.chain_id=row.chain_code, n.chain_name=row.chain_name,
            n.importance=toFloat(row.importance), n.source_database=$source_database,
            n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("technologies", """
        SELECT CONCAT('TECH:', p.chain_code, ':', LPAD(CAST(p.tech_product_seq AS UNSIGNED), 6, '0')) AS tech_id,
               p.tech_product AS name_zh, p.company_name, p.credit_code,
               p.chain_code, p.chain_name, n.node_id AS segment_id
          FROM dwd_org_industry_chain_prod_dtl p
          JOIN (SELECT x.node_id, ROW_NUMBER() OVER (ORDER BY node_seq, chain_code) AS rn
                  FROM dwd_industry_chain_info x) n
            ON n.rn=CAST(p.tech_product_seq AS UNSIGNED)
         ORDER BY p.tech_product_seq
    """, """
        UNWIND $rows AS row
        MERGE (n:Technology {tech_id: row.tech_id})
        SET n.name_zh=row.name_zh, n.name=row.name_zh, n.company_name=row.company_name,
            n.credit_code=row.credit_code, n.chain_id=row.chain_code,
            n.chain_name=row.chain_name, n.segment_id=row.segment_id,
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("trend_reports", """
        SELECT report_id, title_cn, report_category, abstract_cn, keywords_cn, report_type,
               preparation_time, approval_year, source_url, visibility_scope
          FROM dwd_zh_report ORDER BY report_id
    """, """
        UNWIND $rows AS row
        MERGE (n:TrendReport {report_id: row.report_id})
        SET n.title=row.title_cn, n.category=row.report_category, n.abstract=row.abstract_cn,
            n.keywords=row.keywords_cn, n.report_type=row.report_type,
            n.report_date=row.preparation_time, n.year=toInteger(row.approval_year),
            n.url=row.source_url, n.visibility=row.visibility_scope,
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("employments", """
        SELECT CONCAT('EMP:', id) AS employment_id, scholar_id, seq_no, start_time, end_time,
               is_current, CONCAT('WORKORG:', SHA2(org_name_zh, 256)) AS org_id,
               org_name_zh, department_name_zh, position_zh
          FROM dwd_scholar_work_experience WHERE status=1 ORDER BY id
    """, """
        UNWIND $rows AS row
        MERGE (n:Employment {employment_id: row.employment_id})
        SET n.scholar_id=row.scholar_id, n.seq_no=toInteger(row.seq_no),
            n.start_time=row.start_time, n.end_time=row.end_time,
            n.is_current=toInteger(row.is_current), n.org_id=row.org_id,
            n.org_name=row.org_name_zh, n.department=row.department_name_zh,
            n.position=row.position_zh, n.source_database=$source_database,
            n.release_id=$release_id, n.synthetic=true
    """),
    GraphStep("educations", """
        SELECT CONCAT('EDU:', id) AS education_id, scholar_id, seq_no, start_time, end_time,
               is_current, CONCAT('SCHOOL:', SHA2(org_name_zh, 256)) AS school_id,
               org_name_zh, department_name_zh, degree_zh, major_zh
          FROM dwd_scholar_education_background WHERE status=1 ORDER BY id
    """, """
        UNWIND $rows AS row
        MERGE (n:Education {education_id: row.education_id})
        SET n.scholar_id=row.scholar_id, n.seq_no=toInteger(row.seq_no),
            n.start_time=row.start_time, n.end_time=row.end_time,
            n.is_current=toInteger(row.is_current), n.school_id=row.school_id,
            n.school_name=row.org_name_zh, n.department=row.department_name_zh,
            n.degree=row.degree_zh, n.major=row.major_zh,
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
    """),
)


RELATIONSHIP_STEPS = (
    GraphStep("scholar_enterprise", """
        SELECT s.scholar_id, o.org_id, s.work_experience_position_zh AS role
          FROM dwd_scholar s JOIN dwd_org_base_info o ON o.name_cn=s.scholar_org_name_zh
         WHERE s.status=1 ORDER BY s.scholar_id
    """, """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id})
        MATCH (o:Enterprise {enterprise_id:row.org_id})
        MERGE (s)-[r:HAS_ENTERPRISE_ROLE]->(o)
        SET r.role=row.role, r.evidence_id='gkx_enterprise_role_'+row.scholar_id,
            r.weight=1.0, r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("scholar_work_org", """
        SELECT CONCAT('EMP:', id) AS employment_id, scholar_id,
               CONCAT('WORKORG:', SHA2(org_name_zh, 256)) AS org_id,
               start_time, end_time, position_zh
          FROM dwd_scholar_work_experience WHERE status=1 ORDER BY id
    """, """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id})
        MATCH (e:Employment {employment_id:row.employment_id})
        MATCH (o:Organization {org_id:row.org_id})
        MERGE (s)-[a:HAS_EMPLOYMENT]->(e)
        SET a.evidence_id='gkx_employment_'+row.employment_id, a.source_database=$source_database,
            a.release_id=$release_id, a.synthetic=true
        MERGE (e)-[b:EMPLOYED_BY]->(o)
        SET b.start_time=row.start_time, b.end_time=row.end_time, b.position=row.position_zh,
            b.source_database=$source_database, b.release_id=$release_id, b.synthetic=true
    """),
    GraphStep("scholar_school", """
        SELECT CONCAT('EDU:', id) AS education_id, scholar_id,
               CONCAT('SCHOOL:', SHA2(org_name_zh, 256)) AS school_id,
               start_time, end_time, degree_zh, major_zh
          FROM dwd_scholar_education_background WHERE status=1 ORDER BY id
    """, """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id})
        MATCH (e:Education {education_id:row.education_id})
        MATCH (o:School {school_id:row.school_id})
        MERGE (s)-[a:HAS_EDUCATION]->(e)
        SET a.evidence_id='gkx_education_'+row.education_id, a.source_database=$source_database,
            a.release_id=$release_id, a.synthetic=true
        MERGE (e)-[b:STUDIED_AT]->(o)
        SET b.start_time=row.start_time, b.end_time=row.end_time, b.degree=row.degree_zh,
            b.major=row.major_zh, b.source_database=$source_database,
            b.release_id=$release_id, b.synthetic=true
    """),
    GraphStep("authorships", """
        SELECT scholar_id, related_paper_id, year, citations, publish_time
          FROM dwd_scholar_paper_relation WHERE status=1
         ORDER BY related_paper_id, scholar_id
    """, """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id})
        MATCH (p:Paper {paper_id:toString(row.related_paper_id)})
        MERGE (s)-[r:AUTHOR_OF]->(p)
        SET r.year=toInteger(row.year), r.citations=toInteger(row.citations),
            r.publish_time=toString(row.publish_time),
            r.evidence_id='gkx_authorship_'+row.scholar_id+'_'+toString(row.related_paper_id),
            r.weight=1.0, r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("paper_cooperation", """
        SELECT scholar_id, co_scholar_id, co_paper_count
          FROM dwd_scholar_coauthor WHERE status=1 ORDER BY scholar_id, co_scholar_id
    """, """
        UNWIND $rows AS row MATCH (a:Scholar {scholar_id:row.scholar_id})
        MATCH (b:Scholar {scholar_id:row.co_scholar_id})
        WITH row, CASE WHEN a.scholar_id < b.scholar_id THEN a ELSE b END AS x,
                  CASE WHEN a.scholar_id < b.scholar_id THEN b ELSE a END AS y
        MERGE (x)-[r:PAPER_COOP_REL]->(y)
        SET r.co_paper_count=toInteger(row.co_paper_count),
            r.weight=CASE WHEN toFloat(row.co_paper_count)/5.0 > 1.0 THEN 1.0 ELSE toFloat(row.co_paper_count)/5.0 END,
            r.evidence_id='gkx_paper_coop_'+x.scholar_id+'_'+y.scholar_id,
            r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("report_scholar", """
        SELECT scholar_id, JSON_UNQUOTE(JSON_EXTRACT(report_id, '$[0]')) AS report_id
          FROM dwd_zh_report_scholar ORDER BY scholar_id
    """, """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id})
        MATCH (r:TrendReport {report_id:row.report_id})
        MERGE (s)-[x:HAS_TREND_REPORT]->(r)
        SET x.evidence_id='gkx_report_'+row.scholar_id+'_'+row.report_id,
            x.source_database=$source_database, x.release_id=$release_id, x.synthetic=true
    """),
    GraphStep("project_participation", """
        SELECT scholar_id, JSON_UNQUOTE(JSON_EXTRACT(scholar_project, '$[0]')) AS project_id
          FROM dwd_zh_report_scholar ORDER BY scholar_id
    """, """
        UNWIND $rows AS row MATCH (s:Scholar {scholar_id:row.scholar_id})
        MATCH (p:Project {project_id:row.project_id})
        MERGE (s)-[r:PARTICIPATES_IN]->(p)
        SET r.role='项目成员', r.evidence_id='gkx_project_'+row.scholar_id+'_'+row.project_id,
            r.weight=1.0, r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("industry_hierarchy", """
        SELECT node_id, parent_id FROM dwd_industry_chain_info
         WHERE parent_id IS NOT NULL ORDER BY node_id
    """, """
        UNWIND $rows AS row MATCH (c:IndustrySegment {segment_id:row.node_id})
        MATCH (p:IndustrySegment {segment_id:row.parent_id})
        MERGE (c)-[r:SUBSEGMENT_OF]->(p)
        SET r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("industry_downstream", """
        SELECT node_id, downstream_link_code FROM dwd_industry_chain_info
         WHERE downstream_link_code IS NOT NULL ORDER BY node_id
    """, """
        UNWIND $rows AS row MATCH (a:IndustrySegment {segment_id:row.node_id})
        MATCH (b:IndustrySegment {segment_id:row.downstream_link_code})
        MERGE (a)-[r:UPSTREAM_OF]->(b)
        SET r.weight=1.0, r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("enterprise_industry", """
        SELECT o.org_id, d.node_id, d.chain_score
          FROM dwd_org_industry_chain_dtl d
          JOIN dwd_org_base_info o ON o.external_id=d.credit_code
         ORDER BY o.org_id, d.node_id
    """, """
        UNWIND $rows AS row MATCH (e:Enterprise {enterprise_id:row.org_id})
        MATCH (n:IndustrySegment {segment_id:row.node_id})
        MERGE (e)-[r:BELONGS_TO]->(n)
        SET r.score=toFloat(row.chain_score), r.weight=toFloat(row.chain_score)/100.0,
            r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("patent_industry", """
        SELECT apno AS patent_id, node_id FROM dwd_org_industry_chain_pat_dtl ORDER BY apno
    """, """
        UNWIND $rows AS row MATCH (p:Patent {patent_id:row.patent_id})
        MATCH (n:IndustrySegment {segment_id:row.node_id})
        MERGE (p)-[r:PART_OF_SEGMENT]->(n)
        SET r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("patent_assignees", """
        SELECT d.apno AS patent_id, o.org_id
          FROM dwd_org_industry_chain_pat_dtl d
          JOIN dwd_org_base_info o
            ON o.name_cn=JSON_UNQUOTE(JSON_EXTRACT(d.current_assign, '$[0]'))
         ORDER BY d.apno
    """, """
        UNWIND $rows AS row MATCH (p:Patent {patent_id:row.patent_id})
        MATCH (e:Enterprise {enterprise_id:row.org_id})
        MERGE (p)-[r:ASSIGNED_TO]->(e)
        SET r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("technology_enterprise", """
        SELECT CONCAT('TECH:', p.chain_code, ':', LPAD(CAST(p.tech_product_seq AS UNSIGNED), 6, '0')) AS tech_id,
               o.org_id
          FROM dwd_org_industry_chain_prod_dtl p
          JOIN dwd_org_base_info o ON o.external_id=p.credit_code
         ORDER BY p.tech_product_seq
    """, """
        UNWIND $rows AS row MATCH (t:Technology {tech_id:row.tech_id})
        MATCH (e:Enterprise {enterprise_id:row.org_id})
        MERGE (e)-[r:OWNS_TECH]->(t)
        SET r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("technology_segment", """
        SELECT CONCAT('TECH:', p.chain_code, ':', LPAD(CAST(p.tech_product_seq AS UNSIGNED), 6, '0')) AS tech_id,
               n.node_id
          FROM dwd_org_industry_chain_prod_dtl p
          JOIN (SELECT x.node_id, ROW_NUMBER() OVER (ORDER BY node_seq, chain_code) AS rn
                  FROM dwd_industry_chain_info x) n
            ON n.rn=CAST(p.tech_product_seq AS UNSIGNED)
         ORDER BY p.tech_product_seq
    """, """
        UNWIND $rows AS row MATCH (t:Technology {tech_id:row.tech_id})
        MATCH (n:IndustrySegment {segment_id:row.node_id})
        MERGE (t)-[r:PART_OF_SEGMENT]->(n)
        SET r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("event_segment", """
        SELECT q.news_id, n.node_id
          FROM (SELECT x.news_id, ROW_NUMBER() OVER (ORDER BY news_id) AS rn
                  FROM dwd_industry_chain_news_info x) q
          JOIN (SELECT x.node_id, ROW_NUMBER() OVER (ORDER BY node_seq, chain_code) AS rn
                  FROM dwd_industry_chain_info x) n ON n.rn=q.rn
         ORDER BY q.news_id
    """, """
        UNWIND $rows AS row MATCH (e:IndustryEvent {event_id:row.news_id})
        MATCH (n:IndustrySegment {segment_id:row.node_id})
        MERGE (n)-[r:HAS_EVENT]->(e)
        SET r.evidence_id='gkx_event_'+row.news_id, r.source_database=$source_database,
            r.release_id=$release_id, r.synthetic=true
    """),
    GraphStep("event_expert", """
        SELECT n.news_id, s.scholar_id
          FROM dwd_industry_chain_news_info n
          JOIN dwd_scholar s
            ON CAST(RIGHT(n.news_id, 8) AS UNSIGNED)=CAST(RIGHT(s.scholar_id, 6) AS UNSIGNED)
         ORDER BY n.news_id
    """, """
        UNWIND $rows AS row MATCH (e:IndustryEvent {event_id:row.news_id})
        MATCH (s:Scholar {scholar_id:row.scholar_id})
        MERGE (e)-[r:EVENT_EXPERT_REL]->(s)
        SET r.evidence_id='gkx_event_expert_'+row.news_id+'_'+row.scholar_id,
            r.weight=1.0, r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
)


FINALIZERS = (
    ("current_work", """
        MATCH (s:Scholar), (e:Enterprise)
        WHERE s.organization=e.name_zh AND s.source_database=$source_database AND e.source_database=$source_database
        MERGE (s)-[r:WORKS_AT]->(e)
        SET r.role=s.title, r.evidence_id='gkx_current_work_'+s.scholar_id,
            r.weight=1.0, r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    ("colleagues", """
        MATCH (a:Scholar)-[:HAS_EMPLOYMENT]->(:Employment)-[:EMPLOYED_BY]->(o:Organization)
              <-[:EMPLOYED_BY]-(:Employment)<-[:HAS_EMPLOYMENT]-(b:Scholar)
        WHERE a.scholar_id < b.scholar_id AND a.source_database=$source_database AND b.source_database=$source_database
        MERGE (a)-[r:COLLEAGUE_REL]->(b)
        SET r.organization=o.name_zh, r.weight=0.8,
            r.evidence_id='gkx_colleague_'+a.scholar_id+'_'+b.scholar_id,
            r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    ("alumni", """
        MATCH (a:Scholar)-[:HAS_EDUCATION]->(:Education)-[:STUDIED_AT]->(o:School)
              <-[:STUDIED_AT]-(:Education)<-[:HAS_EDUCATION]-(b:Scholar)
        WHERE a.scholar_id < b.scholar_id AND a.source_database=$source_database AND b.source_database=$source_database
        MERGE (a)-[r:ALUMNI_REL]->(b)
        SET r.school=o.name_zh, r.weight=0.6,
            r.evidence_id='gkx_alumni_'+a.scholar_id+'_'+b.scholar_id,
            r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    ("enterprise_projects", """
        MATCH (s:Scholar)-[:PARTICIPATES_IN]->(p:Project), (s)-[:HAS_ENTERPRISE_ROLE]->(e:Enterprise)
        WHERE s.source_database=$source_database
        MERGE (e)-[r:COOPERATES_ON]->(p)
        SET r.evidence_id='gkx_enterprise_project_'+e.enterprise_id+'_'+p.project_id,
            r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """),
    ("derived_inventors", """
        MATCH (p:Patent)-[:ASSIGNED_TO]->(e:Enterprise)<-[:HAS_ENTERPRISE_ROLE]-(s:Scholar)
        WHERE p.source_database=$source_database
        MERGE (s)-[r:INVENTED]->(p)
        SET r.evidence_id='gkx_derived_inventor_'+s.scholar_id+'_'+p.patent_id,
            r.derived=true, r.weight=0.5, r.source_database=$source_database,
            r.release_id=$release_id, r.synthetic=true
    """),
)


def safe_identifier(name: str) -> str:
    if not IDENTIFIER.fullmatch(name):
        raise ValueError(f"Unsafe identifier: {name!r}")
    return name


def mysql_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: mysql_value(value) for key, value in row.items()}


def json_default(value: Any) -> Any:
    return mysql_value(value)


def table_inventory(mysql: Any) -> list[dict[str, Any]]:
    with mysql.cursor() as cursor:
        cursor.execute("""
            SELECT t.table_name AS table_name, t.table_rows AS estimated_rows
              FROM information_schema.tables t
             WHERE t.table_schema=DATABASE() AND t.table_type='BASE TABLE'
             ORDER BY t.table_name
        """)
        tables = list(cursor.fetchall())
        for row in tables:
            table = safe_identifier(row["table_name"])
            cursor.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
            row["row_count"] = int(cursor.fetchone()["n"])
        return tables


def step_counts(mysql: Any, steps: Iterable[GraphStep]) -> dict[str, int]:
    result: dict[str, int] = {}
    with mysql.cursor() as cursor:
        for step in steps:
            cursor.execute(f"SELECT COUNT(*) AS n FROM ({step.select_sql}) AS q")
            result[step.name] = int(cursor.fetchone()["n"])
    return result


def database_state(driver: Any, database: str) -> dict[str, Any]:
    with driver.session(database="system") as session:
        rows = session.run("SHOW DATABASES YIELD name,currentStatus RETURN name,currentStatus").data()
    status = next((row["currentStatus"] for row in rows if row["name"] == database), None)
    if status is None:
        return {"exists": False, "status": None, "nodes": 0, "relationships": 0}
    with driver.session(database=database) as session:
        nodes = int(session.run("MATCH (n) RETURN count(n) AS n").single()["n"])
        relationships = int(session.run("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"])
        foreign = int(session.run(
            "MATCH (n) WHERE coalesce(n.source_database,'') <> $source RETURN count(n) AS n",
            source=SOURCE_DATABASE,
        ).single()["n"])
    return {"exists": True, "status": status, "nodes": nodes,
            "relationships": relationships, "foreign_nodes": foreign}


def create_database(driver: Any, database: str) -> None:
    safe_identifier(database)
    with driver.session(database="system") as session:
        session.run(f"CREATE DATABASE `{database}` IF NOT EXISTS WAIT 30 SECONDS").consume()


def execute_batch(driver: Any, database: str, query: str, rows: list[dict[str, Any]],
                  release_id: str) -> None:
    with driver.session(database=database) as session:
        session.execute_write(
            lambda tx: tx.run(query, rows=rows, release_id=release_id,
                              source_database=SOURCE_DATABASE).consume()
        )


def run_step(mysql: Any, driver: Any, database: str, step: GraphStep,
             release_id: str, batch_size: int) -> int:
    total = 0
    with mysql.cursor() as cursor:
        cursor.execute(step.select_sql)
        while rows := cursor.fetchmany(batch_size):
            payload = [serialize_row(row) for row in rows]
            execute_batch(driver, database, step.cypher, payload, release_id)
            total += len(payload)
    print(json.dumps({"phase": "serving_graph", "step": step.name, "rows": total}, ensure_ascii=False), flush=True)
    return total


def run_finalizer(driver: Any, database: str, name: str, query: str, release_id: str) -> None:
    with driver.session(database=database) as session:
        summary = session.execute_write(
            lambda tx: tx.run(query, release_id=release_id, source_database=SOURCE_DATABASE).consume()
        )
    print(json.dumps({"phase": "derived_relationships", "step": name,
                      "relationships_created": summary.counters.relationships_created}, ensure_ascii=False), flush=True)


def source_record_payload(table: str, row: dict[str, Any], duplicate: int, release_id: str) -> dict[str, Any]:
    serialized = serialize_row(row)
    data_json = json.dumps(serialized, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=json_default)
    content_hash = hashlib.sha256(data_json.encode()).hexdigest()
    return {
        "record_id": f"{table}:{content_hash}:{duplicate}",
        "table_name": table,
        "content_hash": content_hash,
        "duplicate_ordinal": duplicate,
        "data_json": data_json,
        "release_id": release_id,
    }


def sync_source_records(mysql: Any, driver: Any, database: str, inventory: list[dict[str, Any]],
                        release_id: str, batch_size: int) -> int:
    table_query = """
        UNWIND $rows AS row MERGE (t:SourceTable {table_name:row.table_name})
        SET t.row_count=toInteger(row.row_count), t.source_database=$source_database,
            t.release_id=$release_id, t.synthetic=true
    """
    execute_batch(driver, database, table_query, [
        {"table_name": row["table_name"], "row_count": row["row_count"]} for row in inventory
    ], release_id)
    record_query = """
        UNWIND $rows AS row MERGE (n:SourceRecord {record_id:row.record_id})
        SET n.table_name=row.table_name, n.content_hash=row.content_hash,
            n.duplicate_ordinal=toInteger(row.duplicate_ordinal), n.data_json=row.data_json,
            n.source_database=$source_database, n.release_id=$release_id, n.synthetic=true
        WITH n,row MATCH (t:SourceTable {table_name:row.table_name})
        MERGE (n)-[r:FROM_TABLE]->(t)
        SET r.source_database=$source_database, r.release_id=$release_id, r.synthetic=true
    """
    total = 0
    with mysql.cursor() as cursor:
        for number, meta in enumerate(inventory, 1):
            table = safe_identifier(meta["table_name"])
            cursor.execute(f"SELECT * FROM `{table}`")
            occurrences: Counter[str] = Counter()
            batch: list[dict[str, Any]] = []
            table_total = 0
            for row in cursor:
                serialized = serialize_row(row)
                content = json.dumps(serialized, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=json_default)
                digest = hashlib.sha256(content.encode()).hexdigest()
                occurrences[digest] += 1
                batch.append(source_record_payload(table, row, occurrences[digest], release_id))
                if len(batch) >= batch_size:
                    execute_batch(driver, database, record_query, batch, release_id)
                    table_total += len(batch)
                    batch = []
            if batch:
                execute_batch(driver, database, record_query, batch, release_id)
                table_total += len(batch)
            if table_total != int(meta["row_count"]):
                raise RuntimeError(f"Source row mismatch for {table}: {table_total} != {meta['row_count']}")
            total += table_total
            if number % 10 == 0 or number == len(inventory):
                print(json.dumps({"phase": "source_records", "tables_done": number,
                                  "tables_total": len(inventory), "rows_done": total}, ensure_ascii=False), flush=True)
    return total


def audit_graph(driver: Any, database: str, expected_source_records: int,
                expected_tables: int) -> dict[str, Any]:
    queries = {
        "nodes": "MATCH (n) RETURN count(n) AS n",
        "relationships": "MATCH ()-[r]->() RETURN count(r) AS n",
        "source_tables": "MATCH (n:SourceTable) RETURN count(n) AS n",
        "source_records": "MATCH (n:SourceRecord) RETURN count(n) AS n",
        "source_record_table_orphans": "MATCH (n:SourceRecord) WHERE NOT (n)-[:FROM_TABLE]->(:SourceTable) RETURN count(n) AS n",
        "scholars": "MATCH (n:Scholar) RETURN count(n) AS n",
        "enterprises": "MATCH (n:Enterprise) RETURN count(n) AS n",
        "papers": "MATCH (n:Paper) RETURN count(n) AS n",
        "projects": "MATCH (n:Project) RETURN count(n) AS n",
        "patents": "MATCH (n:Patent) RETURN count(n) AS n",
        "industry_segments": "MATCH (n:IndustrySegment) RETURN count(n) AS n",
        "industry_events": "MATCH (n:IndustryEvent) RETURN count(n) AS n",
        "technologies": "MATCH (n:Technology) RETURN count(n) AS n",
        "authorships": "MATCH (:Scholar)-[r:AUTHOR_OF]->(:Paper) RETURN count(r) AS n",
        "enterprise_roles": "MATCH (:Scholar)-[r:HAS_ENTERPRISE_ROLE]->(:Enterprise) RETURN count(r) AS n",
        "project_participations": "MATCH (:Scholar)-[r:PARTICIPATES_IN]->(:Project) RETURN count(r) AS n",
        "patent_assignees": "MATCH (:Patent)-[r:ASSIGNED_TO]->(:Enterprise) RETURN count(r) AS n",
        "enterprise_industry": "MATCH (:Enterprise)-[r:BELONGS_TO]->(:IndustrySegment) RETURN count(r) AS n",
        "segment_events": "MATCH (:IndustrySegment)-[r:HAS_EVENT]->(:IndustryEvent) RETURN count(r) AS n",
        "event_experts": "MATCH (:IndustryEvent)-[r:EVENT_EXPERT_REL]->(:Scholar) RETURN count(r) AS n",
        "foreign_nodes": "MATCH (n) WHERE coalesce(n.source_database,'') <> $source_database RETURN count(n) AS n",
    }
    result: dict[str, Any] = {}
    with driver.session(database=database) as session:
        for name, query in queries.items():
            result[name] = int(session.run(query, source_database=SOURCE_DATABASE).single()["n"])
    expected_minimums = {
        "source_tables": expected_tables, "source_records": expected_source_records,
        "scholars": 1_000, "enterprises": 1_000, "papers": 1_000,
        "projects": 1_000, "patents": 1_000, "industry_segments": 1_000,
        "industry_events": 1_000, "technologies": 1_000,
        "authorships": 3_000, "enterprise_roles": 1_000,
        "project_participations": 1_000, "patent_assignees": 1_000,
        "enterprise_industry": 1_000, "segment_events": 1_000, "event_experts": 1_000,
    }
    failures = {name: {"actual": result[name], "expected_at_least": minimum}
                for name, minimum in expected_minimums.items() if result[name] < minimum}
    if result["source_record_table_orphans"] != 0:
        failures["source_record_table_orphans"] = {"actual": result["source_record_table_orphans"], "expected": 0}
    if result["foreign_nodes"] != 0:
        failures["foreign_nodes"] = {"actual": result["foreign_nodes"], "expected": 0}
    result["passed"] = not failures
    result["failures"] = failures
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-database", default=DEFAULT_TARGET_DATABASE)
    parser.add_argument("--release-id", default=DEFAULT_RELEASE_ID)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--source-batch-size", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-target", default="")
    args = parser.parse_args()
    target = safe_identifier(args.target_database)
    if args.batch_size < 1 or args.batch_size > 1_000:
        raise ValueError("--batch-size must be between 1 and 1000")
    if args.source_batch_size < 1 or args.source_batch_size > 250:
        raise ValueError("--source-batch-size must be between 1 and 250")
    if args.apply and args.confirm_target != target:
        raise ValueError(f"Applying requires --confirm-target {target}")

    settings = Settings.from_env()
    if not settings.neo4j_password:
        raise ValueError("NEO4J_PASSWORD is not configured")
    from neo4j import GraphDatabase

    mysql = _connect(SOURCE_DATABASE, autocommit=False)
    driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    try:
        with mysql.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        driver.verify_connectivity()
        inventory = table_inventory(mysql)
        source_total = sum(int(row["row_count"]) for row in inventory)
        serving_counts = step_counts(mysql, (*NODE_STEPS, *RELATIONSHIP_STEPS))
        target_before = database_state(driver, target)
        preview = {
            "dry_run": not args.apply, "source_database": SOURCE_DATABASE,
            "target_database": target, "release_id": args.release_id,
            "source_tables": len(inventory), "source_rows": source_total,
            "serving_step_rows": serving_counts, "target_before": target_before,
        }
        if not args.apply:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            return
        if target_before.get("foreign_nodes", 0) > 0:
            raise RuntimeError(f"Target {target} contains non-gkx nodes; refusing to mix datasets")

        create_database(driver, target)
        with driver.session(database=target) as session:
            for constraint in CONSTRAINTS:
                session.run(constraint).consume()

        applied_steps: dict[str, int] = {}
        for step in NODE_STEPS:
            applied_steps[step.name] = run_step(mysql, driver, target, step, args.release_id, args.batch_size)
        for step in RELATIONSHIP_STEPS:
            applied_steps[step.name] = run_step(mysql, driver, target, step, args.release_id, args.batch_size)
        for name, query in FINALIZERS:
            run_finalizer(driver, target, name, query, args.release_id)

        source_records = sync_source_records(
            mysql, driver, target, inventory, args.release_id, args.source_batch_size
        )
        audit = audit_graph(driver, target, source_total, len(inventory))
        if not audit["passed"]:
            raise RuntimeError(f"Neo4j reconciliation failed: {audit['failures']}")
        print(json.dumps({**preview, "dry_run": False, "applied": True,
                          "applied_steps": applied_steps, "source_records_written": source_records,
                          "audit": audit}, ensure_ascii=False, indent=2), flush=True)
    finally:
        mysql.rollback()
        mysql.close()
        driver.close()


if __name__ == "__main__":
    main()
