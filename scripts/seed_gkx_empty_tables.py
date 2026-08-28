"""Transactionally fill every currently empty table in ``gkx`` to 1,000 rows.

The existing database schema is authoritative.  This command never creates, drops,
truncates, updates, or deletes tables/rows.  A table is in scope only when it exists and
contains zero rows at preflight time.  Related tables share stable identifiers so the
result is useful for joins instead of being 81 isolated piles of random values.

The default mode is read-only.  Applying requires both ``--apply`` and the exact
``--confirm-database gkx`` guard.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.seed_gkx_excel51 import Column, _connect, coerce_value


DATABASE = "gkx"
TARGET = 1_000
SOURCE = "gkx_empty81_relational_seed_v1_20260827"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FIELDS = ("人工智能", "知识图谱", "新能源", "生物医药", "新材料", "机器人", "量子信息", "集成电路")
CITIES = ("北京", "上海", "深圳", "广州", "杭州", "南京", "武汉", "西安", "成都", "合肥")
PROVINCES = ("北京市", "上海市", "广东省", "广东省", "浙江省", "江苏省", "湖北省", "陕西省", "四川省", "安徽省")


@dataclass(frozen=True)
class ColumnMeta:
    name: str
    column_type: str
    data_type: str
    nullable: bool
    default: Any
    extra: str
    character_length: int | None
    numeric_precision: int | None
    numeric_scale: int | None
    primary: bool
    comment: str

    def as_column(self) -> Column:
        return Column(
            name=self.name,
            column_type=self.column_type,
            data_type=self.data_type,
            nullable=self.nullable,
            default=self.default,
            extra=self.extra,
            character_length=self.character_length,
            numeric_precision=self.numeric_precision,
            numeric_scale=self.numeric_scale,
            primary=self.primary,
        )


@dataclass
class ReferenceData:
    orgs: list[dict[str, Any]]
    scholars: list[dict[str, Any]]
    foreign_orgs: list[dict[str, Any]]
    patents: list[dict[str, Any]]
    zh_projects: list[dict[str, Any]]
    en_projects: list[dict[str, Any]]
    reports: list[dict[str, Any]]
    zh_papers: list[dict[str, Any]]


def quoted(name: str) -> str:
    if not IDENTIFIER.fullmatch(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f"`{name}`"


def fetch_rows(connection: Any, table: str, columns: Iterable[str]) -> list[dict[str, Any]]:
    selected = ", ".join(quoted(column) for column in columns)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {selected} FROM {quoted(table)} ORDER BY 1 LIMIT %s", (TARGET,))
        return list(cursor.fetchall())


def load_references(connection: Any) -> ReferenceData:
    refs = ReferenceData(
        orgs=fetch_rows(connection, "dwd_org_base_info", ("org_id", "name_cn", "external_id")),
        scholars=fetch_rows(connection, "dwd_scholar", ("scholar_id", "name_zh")),
        foreign_orgs=fetch_rows(connection, "dwd_forg_base_info", ("org_id", "name_en", "external_id", "country_code", "country")),
        patents=fetch_rows(connection, "dwd_patent", ("patent_id", "publication_number", "first_inventor_name")),
        zh_projects=fetch_rows(connection, "dwd_zh_project", ("id", "project_number", "title")),
        en_projects=fetch_rows(connection, "dwd_en_project", ("id", "project_number", "title")),
        reports=fetch_rows(connection, "dwd_zh_report", ("report_id", "title_cn")),
        zh_papers=fetch_rows(connection, "dwd_zh_paper", ("id", "doi", "zh_name")),
    )
    for name, rows in vars(refs).items():
        if len(rows) < TARGET:
            raise ValueError(f"Reference dataset {name} has {len(rows)} rows; expected at least {TARGET}")
    return refs


def table_counts(connection: Any) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT table_name AS name
                 FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
                ORDER BY table_name"""
        )
        names = [row["name"] for row in cursor.fetchall()]
        result: dict[str, int] = {}
        for name in names:
            cursor.execute(f"SELECT COUNT(*) AS total FROM {quoted(name)}")
            result[name] = int(cursor.fetchone()["total"])
        return result


def load_columns(connection: Any, tables: Iterable[str]) -> dict[str, list[ColumnMeta]]:
    result: dict[str, list[ColumnMeta]] = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(
                """SELECT column_name AS name, column_type, data_type,
                          is_nullable, column_default, extra,
                          character_maximum_length, numeric_precision, numeric_scale,
                          column_key, column_comment
                     FROM information_schema.columns
                    WHERE table_schema = DATABASE() AND table_name = %s
                    ORDER BY ordinal_position""",
                (table,),
            )
            rows = cursor.fetchall()
            if not rows:
                raise ValueError(f"Table disappeared during preflight: {table}")
            result[table] = [
                ColumnMeta(
                    name=row["name"], column_type=row["COLUMN_TYPE"], data_type=row["DATA_TYPE"],
                    nullable=row["IS_NULLABLE"] == "YES", default=row["COLUMN_DEFAULT"],
                    extra=row["EXTRA"] or "", character_length=row["CHARACTER_MAXIMUM_LENGTH"],
                    numeric_precision=row["NUMERIC_PRECISION"], numeric_scale=row["NUMERIC_SCALE"],
                    primary=row["COLUMN_KEY"] == "PRI", comment=row["COLUMN_COMMENT"] or "",
                )
                for row in rows
            ]
    return result


def load_unique_indexes(connection: Any, tables: Iterable[str]) -> dict[str, list[tuple[str, ...]]]:
    result: dict[str, list[tuple[str, ...]]] = {table: [] for table in tables}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(
                """SELECT index_name AS idx, column_name AS name, seq_in_index AS seq
                     FROM information_schema.statistics
                    WHERE table_schema = DATABASE() AND table_name = %s AND non_unique = 0
                    ORDER BY index_name, seq_in_index""",
                (table,),
            )
            grouped: dict[str, list[str]] = {}
            for row in cursor.fetchall():
                grouped.setdefault(row["idx"], []).append(row["name"])
            result[table] = [tuple(names) for names in grouped.values()]
    return result


def stable_uuid(namespace: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE}:{namespace}:{index}"))


def at(rows: list[dict[str, Any]], index: int) -> dict[str, Any]:
    return rows[(index - 1) % len(rows)]


def when(index: int, offset: int = 0) -> datetime:
    return datetime(2020, 1, 1, 9) + timedelta(days=((index - 1) * 7) % 1_800 + offset)


def patent_id(index: int) -> str:
    return f"CN{2020 + index % 7}{index:010d}A"


def en_paper_id(index: int) -> str:
    return f"2-s2.0-GKX{index:010d}"


def author_id(index: int) -> int:
    return 7_100_000_000_000 + index


def chain_parts(index: int) -> dict[str, Any]:
    chain_index = (index - 1) % len(FIELDS)
    sequence = (index - 1) // len(FIELDS)
    chain_code = f"INB{1001 + chain_index:04d}"
    chain_name = FIELDS[chain_index]
    is_root = sequence == 0
    node_id = chain_code if is_root else f"{chain_code}{sequence:04d}"
    node_name = chain_name if is_root else f"{chain_name}{('上游', '中游', '下游')[sequence % 3]}技术环节{sequence:03d}"
    next_id = None if index + len(FIELDS) > TARGET else f"{chain_code}{sequence + 1:04d}"
    return {
        "chain_code": chain_code, "chain_name": chain_name, "node_id": node_id,
        "node_name": node_name, "node_type": 0 if is_root else sequence % 4 + 1,
        "level": 0 if is_root else 1, "node_seq": sequence,
        "parent_id": None if is_root else chain_code,
        "parent_name": None if is_root else chain_name,
        "node_imp_level": sequence % 5 + 1,
        "downstream_link_code": next_id,
        "node_stage": sequence % 3 + 1,
        "node_path": chain_code if is_root else f"{chain_code}/{node_id}",
    }


def paired_identifier(table: str, name: str, index: int) -> str | None:
    if name == "notice_id":
        if "announcement" in table:
            return f"ANNOUNCEMENT{index:08d}"
        if "judicial_sale" in table:
            return f"SALE{index:08d}"
        return f"COURTNOTICE{index:08d}"
    if name == "case_unique_id":
        return f"FILEDCASE{index:08d}"
    if name in {"main_doc_id", "judgment_doc_id"}:
        return f"LAWSUIT{index:08d}"
    if name == "judicial_assist_id":
        return f"JUSTICE{index:08d}"
    if name == "pledge_id":
        return f"PLEDGE{index:08d}"
    if name == "official_id":
        return f"ZHONGBEN{index:08d}"
    if name == "xhfgk_id":
        return f"XIANXIAO{index:08d}"
    return None


def structured_value(table: str, column: ColumnMeta, index: int, refs: ReferenceData) -> Any:
    name = column.name
    lower = name.lower()
    field = FIELDS[(index - 1) % len(FIELDS)]
    city_index = (index - 1) % len(CITIES)
    org = at(refs.orgs, index)
    next_org = at(refs.orgs, index % TARGET + 1)
    scholar = at(refs.scholars, index)
    foreign_org = at(refs.foreign_orgs, index)
    patent = at(refs.patents, index)
    zh_project = at(refs.zh_projects, index)
    en_project = at(refs.en_projects, index)
    report = at(refs.reports, index)
    zh_paper = at(refs.zh_papers, index)
    chain = chain_parts(index)

    # Tables with physical or logical parent-child relationships.
    if table.startswith("kg_schema_"):
        schema_id = stable_uuid("schema", index)
        if table == "kg_schema_definition":
            mapping = {
                "id": schema_id, "schema_key": f"gkx_schema_{index:04d}",
                "kind": "entity" if index <= 500 else "relation",
                "name": f"GKXSchema{index:04d}", "label": f"知识图谱模式{index:04d}",
                "description": f"用于关系检索与映射验证的知识图谱模式定义{index:04d}",
                "identity_key": "id", "attribute_identity_key": "external_id",
                "attribute_source": SOURCE, "instance_count": TARGET,
                "version": "1.0.0", "display_order": index,
                "is_core": 1 if index <= 16 else 0,
                "relation_category": None if index <= 500 else "association",
                "is_system": 0, "created_by": "codex-relational-seeder",
                "source_schema_id": None if index <= 500 else stable_uuid("schema", (index - 501) % 500 + 1),
                "target_schema_id": None if index <= 500 else stable_uuid("schema", (index - 500) % 500 + 1),
                "source_expression": None if index <= 500 else "source.id",
                "target_expression": None if index <= 500 else "target.id",
            }
            if lower in mapping:
                return mapping[lower]
        elif table == "kg_schema_mapping":
            mapping = {"id": stable_uuid("mapping", index), "schema_id": schema_id,
                       "source_name": f"source_field_{index:04d}", "position": index}
            if lower in mapping:
                return mapping[lower]
        elif table == "kg_schema_property":
            mapping = {"id": stable_uuid("property", index), "schema_id": schema_id,
                       "name": f"property_{index:04d}", "data_type": "string",
                       "required": index % 2, "rule": "trim|non_empty",
                       "category": "attribute", "position": index}
            if lower in mapping:
                return mapping[lower]
        elif table == "kg_schema_script":
            digest = hashlib.sha256(f"{SOURCE}:{index}".encode()).hexdigest()
            mapping = {"id": stable_uuid("script", index), "schema_id": schema_id,
                       "bucket": "gkx-schema", "object_key": f"schemas/{schema_id}.py",
                       "original_filename": f"schema_{index:04d}.py", "content_type": "text/x-python",
                       "size_bytes": 1024 + index, "etag": digest[:32], "sha256": digest,
                       "uploaded_by": "codex-relational-seeder",
                       "workflow_definition_id": f"workflow-{index:04d}",
                       "workflow_function_name": f"map_schema_{index:04d}"}
            if lower in mapping:
                return mapping[lower]

    if table in {"dwd_author_info", "dwd_author_affiliation"}:
        mapping = {
            "auid": author_id(index), "affiliation_id": 8_100_000 + index,
            "afid": 8_100_000 + index, "affiliation_id_parent": 8_100_000 + index,
            "preferred_name": scholar["name_zh"], "afdispname": org["name_cn"],
            "given_name": scholar["name_zh"][-1:], "surname": scholar["name_zh"][:1],
            "indexed_name": scholar["name_zh"], "current_affiliations": org["org_id"],
            "current_affiliations_parent": org["org_id"], "orcid": f"0000-0002-{index:04d}-{(index * 7) % 10000:04d}",
        }
        if lower in mapping:
            return mapping[lower]

    if table == "dwd_en_paper_info":
        mapping = {
            "eid": en_paper_id(index), "doi": f"10.20268/gkx.en.{index:08d}",
            "author_id": str(author_id(index)), "author_list": json.dumps([author_id(index)], ensure_ascii=False),
            "author_surname": scholar["name_zh"][:1], "author_initials": scholar["name_zh"][-1:].upper(),
            "author_count": 1, "title": f"Research on {field} Technology and Applications {index:04d}",
            "sort_year": 2020 + index % 6, "pub_year": 2020 + index % 6,
            "sort_yyyymm": f"{2020 + index % 6}{index % 12 + 1:02d}",
            "cited_by_count": index % 50, "country": "China", "language": "English",
        }
        if lower in mapping:
            return mapping[lower]
    if table == "dwd_en_paper_cited_by":
        mapping = {"paper_eid": en_paper_id(index),
                   "citing_eid": en_paper_id(index % TARGET + 1),
                   "citing_year": 2021 + index % 5}
        if lower in mapping:
            return mapping[lower]

    if table.startswith("ods_en_paper") or table in {"ods_en_author", "ods_en_journal"}:
        mapping = {
            "logic_id": en_paper_id(index), "paper_id": en_paper_id(index),
            "pmid": f"PMID{30_000_000 + index}", "orcid": f"0000-0002-{index:04d}-{(index * 7) % 10000:04d}",
            "surname": scholar["name_zh"][:1], "given_name": scholar["name_zh"][-1:],
            "preferred_name": scholar["name_zh"], "publisher_name": f"GKX Science Press {index % 20 + 1}",
        }
        if lower in mapping:
            return mapping[lower]
    if table.startswith("ods_zh_") and table in {
        "ods_zh_journal", "ods_zh_paper_abstract", "ods_zh_paper_author",
        "ods_zh_paper_classification", "ods_zh_paper_reference", "ods_zh_paper_title",
    } and lower == "logic_id":
        return zh_paper["id"]

    if table in {"ods_en_project", "ods_en_project_output"} and lower == "id":
        return en_project["id"]
    if table in {"ods_zh_project", "ods_zh_project_output"} and lower == "id":
        return zh_project["id"]
    if table.startswith("ods_en_project") or table.startswith("ods_zh_project"):
        project = en_project if table.startswith("ods_en") else zh_project
        mapping = {
            "project_number": project["project_number"], "title": project["title"],
            "project_host": scholar["name_zh"],
            "participants": json.dumps([{"id": scholar["scholar_id"], "name": scholar["name_zh"]}], ensure_ascii=False),
            "participating_institution": json.dumps([{"id": org["org_id"], "name": org["name_cn"]}], ensure_ascii=False),
            "total_outputs": sum((index % 7 + 1, index % 5 + 1, index % 3 + 1)),
        }
        if lower in mapping:
            return mapping[lower]

    if table.startswith("ods_patent"):
        pid = patent_id(index)
        mapping = {
            "id": pid, "patent_id": pid, "publication_number": pid, "pn": pid,
            "application_number": pid[:-1], "application_number_formatted": pid[:-1],
            "country_code": "CN", "kind_code": "A", "application_kind": "A",
            "publication_year": 2020 + index % 7, "filing_year": 2019 + index % 7,
            "grant_year": 2021 + index % 6, "priority_year": 2019 + index % 7,
            "expiration_year": 2040 + index % 7, "invention_title": f"一种{field}技术方法及系统{index:04d}",
            "inventor": json.dumps([scholar["name_zh"]], ensure_ascii=False),
            "assignee": json.dumps([org["name_cn"]], ensure_ascii=False),
            "current_assignee": json.dumps([org["name_cn"]], ensure_ascii=False),
        }
        if lower in mapping:
            return mapping[lower]

    if table == "dwd_zh_report_scholar":
        mapping = {
            "scholar_id": scholar["scholar_id"], "scholar_name": scholar["name_zh"],
            "scholar_unit": [org["org_id"]], "scholar_project": [zh_project["id"]],
            "report_id": [report["report_id"]], "report_source": "dwd_zh_report",
        }
        if lower in mapping:
            return mapping[lower]

    if table == "dwd_industry_chain_info" and lower in chain:
        return chain[lower]
    if table.startswith("dwd_org_industry_chain") or table == "dwd_industry_chain_news_info":
        mapping = {
            **chain, "credit_code": org["external_id"], "company_name": org["name_cn"],
            "antitypic": "企业", "chain_score": Decimal("60.00") + Decimal(index % 40),
            "apno": patent["patent_id"], "pn": patent["publication_number"],
            "pat_name": f"一种{field}关键技术方法及系统{index:04d}",
            "inventors": json.dumps([patent["first_inventor_name"]], ensure_ascii=False),
            "current_assign": json.dumps([org["name_cn"]], ensure_ascii=False),
            "tech_product": f"{field}核心产品{index:04d}", "tech_product_seq": index,
            "news_id": f"CHAINNEWS{index:08d}",
            "title": f"{chain['chain_name']}{chain['node_name']}产业动态{index:04d}",
            "relaese_date": when(index),
            "summary": f"{org['name_cn']}在{chain['node_name']}取得研发、产品或市场进展。",
            "source": "国家科技产业信息平台",
        }
        if lower in mapping:
            return mapping[lower]

    pair = paired_identifier(table, lower, index)
    if pair is not None:
        return pair

    # Reuse live master entities across organization and foreign-organization satellites.
    if table.startswith("dwd_forg_"):
        mapping = {
            "org_id": foreign_org["org_id"], "ename_en": foreign_org["name_en"],
            "entity_eid": foreign_org["external_id"], "invested_eid": at(refs.foreign_orgs, index % TARGET + 1)["external_id"],
            "entity_name": foreign_org["name_en"], "invested_name": at(refs.foreign_orgs, index % TARGET + 1)["name_en"],
            "country_code": foreign_org["country_code"], "entity_country_code": foreign_org["country_code"],
            "affiliates_country_code": foreign_org["country_code"], "affiliates_country": foreign_org["country"],
            "affiliate": at(refs.foreign_orgs, index % TARGET + 1)["org_id"],
            "affiliates_company_id": at(refs.foreign_orgs, index % TARGET + 1)["external_id"],
            "affiliates_name": at(refs.foreign_orgs, index % TARGET + 1)["name_en"],
        }
        if lower in mapping:
            return mapping[lower]

    org_mapping = {
        "org_id": org["org_id"], "name_cn": org["name_cn"],
        "org_name": org["name_cn"], "company_name": org["name_cn"],
        "social_credit_code": org["external_id"], "credit_code": org["external_id"],
        "tender_org_id": org["org_id"], "tender_name_cn": org["name_cn"],
        "tender_social_credit_code": org["external_id"],
        "winner_org_id": next_org["org_id"], "winner_name_cn": next_org["name_cn"],
        "winner_social_credit_code": next_org["external_id"],
        "related_company": org["name_cn"], "party_name": org["name_cn"],
        "plaintiff": org["name_cn"], "defendant": next_org["name_cn"],
        "plaintiff_appellant": org["name_cn"], "defendant_appellee": next_org["name_cn"],
    }
    if lower in org_mapping:
        return org_mapping[lower]

    if lower in {"scholar_id", "author_id"}:
        return scholar["scholar_id"]
    if lower in {"scholar_name", "author", "authors", "project_host", "judge", "presiding_judge", "related_person_name"}:
        return scholar["name_zh"]
    if lower in {"patent_id", "apno"}:
        return patent["patent_id"]
    if lower in {"report_id"}:
        return report["report_id"]
    if lower in {"project_id"}:
        return zh_project["id"]

    if lower in {"data_source", "db_source", "source_table"}:
        return SOURCE
    if lower in {"created_time", "create_time", "updated_time", "update_time", "created_at", "updated_at", "uploaded_at"}:
        return datetime(2026, 8, 27, 16, 0)
    if column.data_type in {"date", "datetime", "timestamp"}:
        return when(index).date() if column.data_type == "date" else when(index)
    if "date" in lower or lower.endswith("_time") or lower in {"crtime", "pubtime", "effectivetime", "expiration_date"}:
        return when(index).strftime("%Y-%m-%d %H:%M:%S")
    if "year" in lower:
        return 2020 + index % 6

    if lower in {"province", "funded_province"}:
        return PROVINCES[city_index]
    if lower in {"city"}:
        return CITIES[city_index]
    if lower in {"state", "district", "area", "region"}:
        return "高新技术产业开发区"
    if lower in {"country"}:
        return "China"
    if lower in {"country_code", "priority_country", "dwpi_priority_country"}:
        return "CN"
    if lower in {"language", "ir_language"}:
        return "zh-CN"
    if lower in {"email"}:
        return f"contact{index:04d}@example.invalid"
    if "phone" in lower or lower == "contact_number":
        return f"010-{60000000 + index:08d}"
    if lower in {"postal_code"}:
        return f"{100000 + index:06d}"[-6:]
    if "url" in lower or lower in {"website", "domain", "link", "original_link", "source_website"}:
        return f"https://example.invalid/{table}/{index:04d}"
    if lower in {"addr_lng", "lng", "longitude"}:
        return Decimal("116.300000") + Decimal(index % 100) / Decimal(1000)
    if lower in {"addr_lat", "lat", "latitude"}:
        return Decimal("39.900000") + Decimal(index % 100) / Decimal(1000)

    if lower == "id":
        return stable_uuid(table, index)
    if lower.endswith("_id") or lower in {"recordid", "logic_id", "lngid", "u_id", "eid"}:
        return f"GKX{hashlib.sha1(table.encode()).hexdigest()[:6].upper()}{index:010d}"
    if lower.endswith("_code") or lower in {"code", "status", "type", "kind", "datatype", "docstatus"}:
        return index % 5 + 1
    if lower in {"publication_number", "pn"}:
        return patent_id(index)
    if lower in {"title", "title_cn", "zh_name", "information_title", "announcement_title", "notice_name", "doc_title"}:
        return f"{field}关键技术与产业应用数据{index:04d}"
    if "name" in lower:
        return f"{field}{column.comment or name}{index:04d}"
    if lower in {"keywords", "keyword", "keywords_cn", "sy_keywords"}:
        return f"{field},科技创新,成果转化"
    if any(token in lower for token in ("abstract", "content", "description", "desc", "text", "summary", "scope", "remark", "claim")):
        return f"本条为{field}领域的关联测试数据，用于检索、图谱关系与统计分析，记录序号{index:04d}。"
    if any(token in lower for token in ("amount", "assets", "liabilities", "revenue", "profit", "equity", "capital", "price", "size_bytes")):
        return Decimal(500_000 + index * 1_000)
    if any(token in lower for token in ("percent", "pct", "ratio", "rate", "score")):
        return Decimal(index % 100) / Decimal(100)
    if any(token in lower for token in ("count", "number", "seq", "position", "page", "volume", "issue", "level")):
        return index % 100 + 1
    if column.data_type in {"tinyint", "smallint", "mediumint", "int", "bigint", "decimal", "numeric", "float", "double", "real"}:
        return index
    if column.data_type == "json":
        return {"value": f"{field}-{index:04d}", "source": SOURCE}
    return f"{field}-{name}-{index:04d}"


def value_for(table: str, column: ColumnMeta, index: int, refs: ReferenceData) -> Any:
    raw = structured_value(table, column, index, refs)
    if raw is None:
        if column.nullable:
            return None
        raw = ""
    return coerce_value(column.as_column(), raw, index)


def writable(columns: list[ColumnMeta]) -> list[ColumnMeta]:
    return [column for column in columns
            if "auto_increment" not in column.extra.lower()
            and "stored generated" not in column.extra.lower()
            and "virtual generated" not in column.extra.lower()]


def generate_rows(table: str, columns: list[ColumnMeta], refs: ReferenceData,
                  start: int, count: int) -> list[dict[str, Any]]:
    cols = writable(columns)
    return [
        {column.name: value_for(table, column, index, refs) for column in cols}
        for index in range(start + 1, start + count + 1)
    ]


def validate_rows(table: str, rows: list[dict[str, Any]], columns: list[ColumnMeta],
                  unique_indexes: list[tuple[str, ...]]) -> list[str]:
    errors: list[str] = []
    cols = writable(columns)
    expected = {column.name for column in cols}
    for row_number, row in enumerate(rows, 1):
        if set(row) != expected:
            errors.append(f"{table} row {row_number}: column set mismatch")
            break
        for column in cols:
            value = row[column.name]
            if value is None and not column.nullable and column.default is None:
                errors.append(f"{table}.{column.name} row {row_number}: NULL in NOT NULL column")
            if column.character_length is not None and value is not None and len(str(value)) > int(column.character_length):
                errors.append(f"{table}.{column.name} row {row_number}: exceeds {column.character_length}")
            if column.data_type == "json" and value is not None:
                try:
                    json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    errors.append(f"{table}.{column.name} row {row_number}: invalid JSON")
    for names in unique_indexes:
        seen: set[tuple[Any, ...]] = set()
        for row_number, row in enumerate(rows, 1):
            if any(name not in row for name in names):
                continue
            key = tuple(row[name] for name in names)
            if any(value is None for value in key):
                continue
            if key in seen:
                errors.append(f"{table} row {row_number}: duplicate unique key {names}={key}")
                break
            seen.add(key)
    return errors


def insert_rows(connection: Any, table: str, columns: list[ColumnMeta], refs: ReferenceData,
                batch_size: int) -> int:
    cols = writable(columns)
    names = [column.name for column in cols]
    placeholders = ", ".join(["%s"] * len(names))
    sql = (f"INSERT INTO {quoted(table)} (" + ", ".join(quoted(name) for name in names)
           + f") VALUES ({placeholders})")
    inserted = 0
    with connection.cursor() as cursor:
        for start in range(0, TARGET, batch_size):
            rows = generate_rows(table, columns, refs, start, min(batch_size, TARGET - start))
            cursor.executemany(sql, [tuple(row[name] for name in names) for row in rows])
            inserted += len(rows)
    return inserted


def association_audit(connection: Any) -> dict[str, int]:
    checks = {
        "author_affiliation_orphans": """SELECT COUNT(*) AS n FROM dwd_author_affiliation a
            LEFT JOIN dwd_author_info i ON i.auid=a.auid WHERE i.auid IS NULL""",
        "paper_citation_source_orphans": """SELECT COUNT(*) AS n FROM dwd_en_paper_cited_by c
            LEFT JOIN dwd_en_paper_info p ON p.eid=c.paper_eid WHERE p.eid IS NULL""",
        "paper_citation_target_orphans": """SELECT COUNT(*) AS n FROM dwd_en_paper_cited_by c
            LEFT JOIN dwd_en_paper_info p ON p.eid=c.citing_eid WHERE p.eid IS NULL""",
        "industry_org_node_orphans": """SELECT COUNT(*) AS n FROM dwd_org_industry_chain_dtl d
            LEFT JOIN dwd_industry_chain_info n ON n.node_id=d.node_id WHERE n.node_id IS NULL""",
        "industry_patent_node_orphans": """SELECT COUNT(*) AS n FROM dwd_org_industry_chain_pat_dtl d
            LEFT JOIN dwd_industry_chain_info n ON n.node_id=d.node_id WHERE n.node_id IS NULL""",
        "industry_org_master_orphans": """SELECT COUNT(*) AS n FROM dwd_org_industry_chain_dtl d
            LEFT JOIN dwd_org_base_info o ON o.external_id=d.credit_code WHERE o.org_id IS NULL""",
        "ods_patent_biblio_orphans": """SELECT COUNT(*) AS n FROM ods_patent_Biblio_ d
            LEFT JOIN ods_patent p ON p.id=d.patent_id WHERE p.id IS NULL""",
        "ods_patent_claim_orphans": """SELECT COUNT(*) AS n FROM ods_patent_Claims d
            LEFT JOIN ods_patent p ON p.id=d.patent_id WHERE p.id IS NULL""",
        "ods_zh_project_output_orphans": """SELECT COUNT(*) AS n FROM ods_zh_project_output o
            LEFT JOIN ods_zh_project p ON p.id=o.id WHERE p.id IS NULL""",
        "ods_en_project_output_orphans": """SELECT COUNT(*) AS n FROM ods_en_project_output o
            LEFT JOIN ods_en_project p ON p.id=o.id WHERE p.id IS NULL""",
        "court_announcement_party_orphans": """SELECT COUNT(*) AS n FROM dwd_org_risk_court_announcement_list d
            LEFT JOIN dwd_org_risk_court_announcement p ON p.notice_id=d.notice_id WHERE p.notice_id IS NULL""",
        "filed_case_litigant_orphans": """SELECT COUNT(*) AS n FROM dwd_org_risk_court_filed_case_litigant d
            LEFT JOIN dwd_org_risk_court_filed_case p ON p.case_unique_id=d.case_unique_id WHERE p.case_unique_id IS NULL""",
        "court_notice_party_orphans": """SELECT COUNT(*) AS n FROM dwd_org_risk_court_notice_list d
            LEFT JOIN dwd_org_risk_court_notice p ON p.notice_id=d.notice_id WHERE p.notice_id IS NULL""",
        "lawsuit_party_orphans": """SELECT COUNT(*) AS n FROM dwd_org_risk_lawsuit_list d
            LEFT JOIN dwd_org_risk_lawsuit p ON p.main_doc_id=d.main_doc_id WHERE p.main_doc_id IS NULL""",
        "judicial_sale_company_orphans": """SELECT COUNT(*) AS n FROM dwd_org_tb_judicial_sale_info_company d
            LEFT JOIN dwd_org_tb_judicial_sale p ON p.notice_id=d.notice_id WHERE p.notice_id IS NULL""",
        "kg_mapping_orphans": """SELECT COUNT(*) AS n FROM kg_schema_mapping d
            LEFT JOIN kg_schema_definition p ON p.id=d.schema_id WHERE p.id IS NULL""",
        "kg_property_orphans": """SELECT COUNT(*) AS n FROM kg_schema_property d
            LEFT JOIN kg_schema_definition p ON p.id=d.schema_id WHERE p.id IS NULL""",
        "kg_script_orphans": """SELECT COUNT(*) AS n FROM kg_schema_script d
            LEFT JOIN kg_schema_definition p ON p.id=d.schema_id WHERE p.id IS NULL""",
    }
    result: dict[str, int] = {}
    with connection.cursor() as cursor:
        for name, sql in checks.items():
            cursor.execute(sql)
            result[name] = int(cursor.fetchone()["n"])
    return result


def ordered_tables(tables: list[str]) -> list[str]:
    first = ["kg_schema_definition", "dwd_author_info", "dwd_en_paper_info", "dwd_industry_chain_info",
             "ods_patent", "ods_en_project", "ods_zh_project",
             "dwd_org_risk_court_announcement", "dwd_org_risk_court_filed_case",
             "dwd_org_risk_court_notice", "dwd_org_risk_lawsuit", "dwd_org_tb_judicial_sale"]
    rank = {name: index for index, name in enumerate(first)}
    return sorted(tables, key=lambda name: (rank.get(name, len(rank)), name))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the validated plan")
    parser.add_argument("--confirm-database", default="", help="must equal gkx when applying")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 500:
        raise ValueError("--batch-size must be between 1 and 500")
    if args.apply and args.confirm_database != DATABASE:
        raise ValueError("Applying requires --confirm-database gkx")

    connection = _connect(DATABASE, autocommit=False)
    try:
        before = table_counts(connection)
        empty = ordered_tables([table for table, count in before.items() if count == 0])
        refs = load_references(connection)
        columns = load_columns(connection, empty)
        unique_indexes = load_unique_indexes(connection, empty)

        validation_errors: list[str] = []
        for table in empty:
            rows = generate_rows(table, columns[table], refs, 0, TARGET)
            validation_errors.extend(validate_rows(table, rows, columns[table], unique_indexes[table]))
        if validation_errors:
            raise ValueError("Preflight validation failed:\n" + "\n".join(validation_errors[:50]))

        preview = {
            "database": DATABASE, "apply": args.apply, "target_per_table": TARGET,
            "table_count": len(before), "empty_table_count": len(empty),
            "nonempty_table_count": len(before) - len(empty),
            "planned_insert_rows": len(empty) * TARGET,
            "tables": empty,
            "validation_errors": 0,
        }
        if not args.apply:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            connection.rollback()
            return

        # Recheck immediately before the first INSERT to avoid racing another writer.
        latest = table_counts(connection)
        changed = {table: latest.get(table) for table in empty if latest.get(table) != 0}
        if changed:
            raise RuntimeError(f"Tables changed after preflight; refusing to write: {changed}")

        inserted: dict[str, int] = {}
        for table in empty:
            inserted[table] = insert_rows(connection, table, columns[table], refs, args.batch_size)

        after_in_transaction = table_counts(connection)
        wrong_counts = {table: after_in_transaction[table] for table in empty
                        if after_in_transaction[table] != TARGET}
        associations = association_audit(connection)
        orphaned = {name: count for name, count in associations.items() if count != 0}
        if wrong_counts or orphaned:
            raise RuntimeError(f"Pre-commit audit failed: wrong_counts={wrong_counts}, orphaned={orphaned}")
        connection.commit()

        final = table_counts(connection)
        final_wrong = {table: final[table] for table in empty if final[table] != TARGET}
        if final_wrong:
            raise RuntimeError(f"Post-commit row-count audit failed: {final_wrong}")
        print(json.dumps({
            **preview, "applied": True, "inserted_rows": sum(inserted.values()),
            "post_empty_table_count": sum(count == 0 for count in final.values()),
            "post_nonempty_table_count": sum(count > 0 for count in final.values()),
            "post_total_rows": sum(final.values()), "association_audit": associations,
        }, ensure_ascii=False, indent=2))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
