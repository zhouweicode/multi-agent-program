"""Create and seed the 39 tables defined by the research-output reference workbook.

The workbook covers Chinese/foreign papers, global patents, domestic/foreign projects,
and Chinese/foreign reports. Existing schemas are respected; only missing tables are
created, and every table is topped up to at least the requested row count.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.seed_gkx_excel51 import (
    Column,
    FieldSpec,
    _columns_from_specs,
    _connect,
    apply_plan,
    audit_after,
    coerce_value,
    inspect_database,
    parse_workbook,
    validate_in_mysql,
    validate_rows,
)

WORKBOOK = PROJECT_ROOT / "数据表" / "论文、专利、项目、报告数据汇总表.xlsx"
EXPECTED_TABLES = 39
DEFAULT_TARGET = 1_000
SOURCE = "gkx_research39_seed_v1_20260827"

ZH_PAPER_TABLES = {
    "dwd_zh_paper", "dwd_zh_paper_title", "dwd_zh_paper_abstract", "dwd_zh_author",
    "dwd_zh_journal", "dwd_zh_paper_citation", "dwd_zh_paper_reference",
    "dwd_zh_paper_classification", "dwd_zh_paper_related",
}
EN_PAPER_TABLES = {
    "dwd_en_paper", "dwd_en_paper_title", "dwd_en_paper_abstract", "dwd_en_author",
    "dwd_en_journal", "dwd_en_paper_reference", "dwd_en_paper_citation",
    "dwd_en_paper_funding", "dwd_en_paper_classification", "dwd_en_paper_related",
}
PATENT_TABLES = {
    "dwd_patent", "dwd_patent_abstract", "dwd_patent_cited", "dwd_patent_family",
    "dwd_patent_legal", "dwd_patent_title", "dwd_patent_transfer",
}
ZH_PROJECT_TABLES = {"dwd_zh_project", "dwd_zh_project_output"}
EN_PROJECT_TABLES = {"dwd_en_project", "dwd_en_project_output"}
ZH_REPORT_TABLES = {
    "dwd_zh_report", "ods_zh_report", "dwd_zh_report_author", "dwd_zh_report_org",
    "dwd_zh_report_paper", "dwd_zh_report_project",
}
EN_REPORT_TABLES = {"dwd_en_report", "dwd_en_report_author", "dwd_en_report_org"}
FIELDS = ("人工智能", "知识图谱", "新能源", "生物医药", "新材料", "机器人", "量子信息", "集成电路")
INSTITUTIONS_ZH = ("北京大学", "清华大学", "中国科学院", "浙江大学", "上海交通大学")
INSTITUTIONS_EN = ("Peking University", "Tsinghua University", "Chinese Academy of Sciences",
                   "Zhejiang University", "Shanghai Jiao Tong University")


def zh_paper_id(index: int) -> str:
    return f"GKXRZHPAPER{index:010d}"


def en_paper_id(index: int) -> str:
    return f"GKXRENPAPER{index:010d}"


def patent_id(index: int) -> str:
    return f"GKXRPATENT{index:010d}"


def zh_project_id(index: int) -> str:
    return f"GKXRZHPROJ{index:010d}"


def en_project_id(index: int) -> str:
    return f"GKXRENPROJ{index:010d}"


def zh_report_id(index: int) -> str:
    return f"GKXRZHREPORT{index:010d}"


def en_report_id(index: int) -> str:
    return f"GKXRENREPORT{index:010d}"


def author_id(index: int) -> str:
    return f"GKXRAUTH{index:010d}"


def org_id(index: int) -> str:
    return f"GKXRORG{index:010d}"


def _field(index: int) -> str:
    return FIELDS[(index - 1) % len(FIELDS)]


def _now() -> datetime:
    return datetime(2026, 8, 27, 14)


def _event_date(index: int, offset: int = 0) -> date:
    return date(2018, 1, 1) + timedelta(days=(index * 11) % 2_000 + offset)


def _paper_id(table: str, index: int) -> str:
    return zh_paper_id(index) if table in ZH_PAPER_TABLES else en_paper_id(index)


def _project_id(table: str, index: int) -> str:
    return zh_project_id(index) if table in ZH_PROJECT_TABLES else en_project_id(index)


def _report_id(table: str, index: int) -> str:
    return zh_report_id(index) if table in ZH_REPORT_TABLES else en_report_id(index)


def _json_payload(table: str, name: str, index: int) -> Any:
    field = _field(index)
    lower = name.lower()
    if lower in {"report_id"}:
        return [_report_id(table, index)]
    if lower in {"paper_id", "literature_id", "related_literature_id"}:
        return [zh_paper_id(index) if table in ZH_REPORT_TABLES else en_paper_id(index)]
    if lower in {"project_id", "related_project_id"}:
        return [zh_project_id(index) if table in ZH_REPORT_TABLES else en_project_id(index)]
    if lower in {"org_id", "related_org_id", "related_institutions", "source_org"}:
        return [org_id(index)]
    if lower in {"author_id", "authors_id", "scholar_id", "related_author_id", "related_scholars"}:
        return [author_id(index)]
    if lower in {"authors", "participants", "related_authors"}:
        return [{"id": author_id(index), "name": f"示例学者{index:04d}"}]
    if lower in {"organization", "corporate_author", "participating_institution", "author_unit",
                 "affiliation"}:
        return [{"id": org_id(index), "name": INSTITUTIONS_ZH[index % len(INSTITUTIONS_ZH)]}]
    if lower in {"keywords", "keywords_cn", "keywords_en", "classification_fi",
                 "classification_loc", "further_classification_ipcr", "further_classification_cpc",
                 "language", "simple_family_pn"}:
        return [field, "科技创新", "成果转化"]
    if lower in {"email"}:
        return [f"researcher{index:04d}@example.invalid"]
    if lower in {"titles"}:
        return {"zh": f"{field}关键技术研究{index:04d}",
                "en": f"Research on {field} Technology {index:04d}"}
    if lower in {"abstracts"}:
        return {"zh": f"围绕{field}开展方法与应用研究。",
                "en": f"Methods and applications in {field}."}
    if lower in {"claims", "description"}:
        return {"zh": f"一种面向{field}的测试方法及系统，记录{index:04d}。"}
    if lower in {"applicants", "assignees", "inventors", "agents", "agency", "examiners",
                 "transfer_before", "transfer_after"}:
        return [{"sequence": 1, "name": f"示例主体{index:04d}"}]
    if lower in {"publication_reference"}:
        return {"kind": "A", "pbdt": _event_date(index).isoformat(),
                "pbdt_year": _event_date(index).year}
    if lower in {"application_reference", "pct_or_regional_filing_data",
                 "pct_or_regional_publishing_data", "priority_filings", "related_documents"}:
        return {"sequence": 1, "number": f"CN2026{index:08d}",
                "date": _event_date(index, -30).isoformat()}
    if lower in {"legal_events", "patent_legal/prs_data", "patent_legal"}:
        return [{"date": _event_date(index).isoformat(), "status": "Active"}]
    if lower in {"cited_by_date", "patent_citation_date", "non_patent_date"}:
        return [_event_date(index).isoformat()]
    if "citation" in lower or lower in {"cited_by", "simple_family", "family_citations",
                                        "cited_by_family", "patent_family"}:
        return [{"patent_id": patent_id(index % 1_000 + 1)}]
    if lower.startswith("output_"):
        return [{"title": f"{field}项目产出{index:04d}", "year": 2020 + index % 7}]
    if lower in {"relevant", "related_literature"}:
        related = zh_paper_id(index % 1_000 + 1) if table in ZH_PAPER_TABLES | ZH_REPORT_TABLES else en_paper_id(index % 1_000 + 1)
        return [{"id": related, "score": 0.8}]
    if lower in {"file_path"}:
        return [f"/research/reports/{index:04d}.pdf"]
    if lower in {"figures"}:
        return [{"figure": 1, "url": f"https://example.invalid/patents/{index:04d}/figure/1"}]
    return [{"value": f"{field}-{index:04d}"}]


def _raw_value(table: str, column: Column, index: int, spec: FieldSpec | None) -> Any:
    name = column.name
    lower = name.lower()
    field = _field(index)
    if column.data_type.lower() == "json":
        return _json_payload(table, lower, index)
    if (table in ZH_REPORT_TABLES | EN_REPORT_TABLES and column.data_type.lower() in {"char", "varchar"}
            and lower in {"publication_date", "preparation_time", "updated_time"}):
        return _event_date(index).strftime("%Y%m%d")
    if lower in {"created_time", "create_time", "updated_time", "update_time", "created_time_2",
                 "updated_time_2", "approval_time", "cover_date_start"}:
        return _now()
    if lower.endswith("_date") or lower in {"transfer_effective_date", "publicdate", "proposaldate",
                                             "startdate", "enddate", "downtime"}:
        return _event_date(index).isoformat()
    if lower in {"id"}:
        if table in ZH_PAPER_TABLES | EN_PAPER_TABLES:
            return _paper_id(table, index)
        if table in ZH_PROJECT_TABLES | EN_PROJECT_TABLES:
            return _project_id(table, index)
        if table in PATENT_TABLES:
            return 9_600_000_000 + index if column.data_type.lower() in {"bigint", "int"} else f"GKXR{index:016d}"
        if table in ZH_REPORT_TABLES | EN_REPORT_TABLES:
            return _report_id(table, index)
    if lower == "paper_id":
        if table == "dwd_zh_report_paper":
            return zh_paper_id(index)
        return _paper_id(table, index)
    if lower == "patent_id" or lower == "pn":
        return patent_id(index)
    if lower == "project_id":
        return zh_project_id(index) if table in ZH_REPORT_TABLES else _project_id(table, index)
    if lower == "report_id":
        return _report_id(table, index)
    if lower in {"author_id", "authors_id", "scholar_id"}:
        return author_id(index)
    if lower == "org_id":
        return org_id(index)
    if lower == "logic_id":
        return f"GKXRLOGIC{index:010d}"
    if lower == "publication_id":
        return 8_100_000 + index
    if lower in {"doi", "paper_doi"}:
        return f"10.20268/gkxr.{index:08d}"
    if lower in {"publication_number", "granted_number"}:
        return f"CN2026{index:08d}A"
    if lower in {"project_number", "contract_number"}:
        return f"GKXR-2026-{index:06d}"
    if lower == "report_number":
        return f"GKXR-RPT-{index:06d}"
    if lower in {"author_sequence", "title_sequence", "abstract_sequence", "sequence"}:
        return 1
    if lower in {"original_abstract", "original_title", "review", "top", "warning", "is_sci",
                 "correspond", "abstract_available", "open_access", "language_classify"}:
        return index % 2
    if lower == "total_outputs":
        return 8 if table == "dwd_zh_project_output" else 9
    if lower.endswith("_count") or lower.endswith("_nums") or lower in {"paper_nums", "cite_nums",
                                                                         "page_count", "reference_cited"}:
        return 1
    if lower in {"approval_year", "cover_year_start", "founding_time", "establish_time",
                 "expiration_year", "anticipated_expiration", "project_annual_number"}:
        return 2020 + index % 7
    if lower in {"impact_factor", "self_rate"}:
        return Decimal("3.50") + Decimal(index % 20) / Decimal(10)
    if lower in {"funded_amount", "value"}:
        return Decimal(500_000 + index * 1_000)
    if column.data_type.lower() in {"tinyint", "smallint", "mediumint", "int", "bigint",
                                    "decimal", "numeric", "double", "float", "real"}:
        return index % 10 + 1
    if lower in {"zh_name", "title_cn", "paper_name", "title"}:
        return f"{field}关键技术与应用研究{index:04d}"
    if lower in {"en_name", "title_en"}:
        return f"Research on {field} Technology and Applications {index:04d}"
    if lower in {"author_name", "authors_name", "creator", "responsibleperson", "project_host"}:
        return f"示例学者{index:04d}"
    if lower in {"org_name", "organization", "institution", "organizer", "prepareorganization",
                 "creatororganization", "competentorg", "funded_institution"}:
        return INSTITUTIONS_ZH[index % len(INSTITUTIONS_ZH)]
    if lower in {"abstract", "abstract_cn", "abstract_en", "zh_abstract", "en_abstract",
                 "original_abstract", "final_report_abstract", "content", "funds"}:
        return f"围绕{field}开展理论方法、关键技术、实验验证与成果转化研究，记录序号{index:04d}。"
    if lower in {"keywords", "keywordscn", "keywordsen", "keywords_cn", "keywords_en"}:
        return f"{field};科技创新;成果转化"
    if lower in {"publication_zh_name"}:
        return f"{field}学报"
    if lower in {"publication_en_name"}:
        return f"Journal of {field} Research"
    if lower in {"data_source"}:
        return SOURCE
    if lower in {"db_source"}:
        return "ods_patent"
    if lower in {"report_source"}:
        return "cn_report" if table in ZH_REPORT_TABLES else "en_report"
    if lower in {"paper_source"}:
        return "cn_paper"
    if lower in {"project_source"}:
        return "国家自然科学基金" if table in ZH_PROJECT_TABLES | ZH_REPORT_TABLES else "Horizon Europe"
    if lower in {"source_agency"}:
        return "National Science Foundation"
    if lower in {"source_url", "paper_url", "project_page_url", "jn_official"}:
        return f"https://example.invalid/{table}/{index:04d}"
    if lower in {"country", "org_country"}:
        return "China" if table not in EN_PAPER_TABLES | EN_PROJECT_TABLES | EN_REPORT_TABLES else "United States"
    if lower in {"country_code"}:
        return "CN"
    if lower in {"language", "language_code"}:
        return "zh" if table in ZH_PAPER_TABLES | ZH_REPORT_TABLES else "en"
    if lower in {"status"}:
        return "Active"
    if lower in {"issn", "issn_print", "issn_online", "eissn"}:
        return f"{1000 + index % 8999:04d}-{1000 + index % 8999:04d}"
    if lower in {"email", "linkmanemail"}:
        return f"researcher{index:04d}@example.invalid"
    if lower in {"phone", "contact_phone", "lnkmanphone"}:
        return f"010-{60000000 + index:08d}"
    if lower in {"main_classification_ipcr", "main_classification_cpc"}:
        return f"G06F{index % 100:02d}/00"
    if lower == "application_kind":
        return "A"
    sample = spec.sample.strip() if spec else ""
    if sample and sample not in {"/", "-", "—"} and not sample.startswith("46"):
        return sample
    return f"{spec.name_cn if spec and spec.name_cn else name}-{index:04d}"


def generate_rows(table: str, fields: list[FieldSpec], columns: list[Column], start: int,
                  count: int) -> list[dict[str, Any]]:
    specs = {field.name.lower(): field for field in fields}
    writable = [column for column in columns
                if "auto_increment" not in column.extra.lower() and "generated" not in column.extra.lower()]
    rows = []
    for index in range(start + 1, start + count + 1):
        row = {}
        for column in writable:
            spec = specs.get(column.name.lower())
            row[column.name] = coerce_value(column, _raw_value(table, column, index, spec), index)
        rows.append(row)
    return rows


def validate_relationships(generated: dict[str, list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    zh_papers = {row["id"] for row in generated["dwd_zh_paper"]}
    en_papers = {row["id"] for row in generated["dwd_en_paper"]}
    for table in sorted(ZH_PAPER_TABLES - {"dwd_zh_paper", "dwd_zh_journal"}):
        key = "paper_id" if table == "dwd_zh_author" else "id"
        if any(row[key] not in zh_papers for row in generated[table]):
            errors.append(f"{table}: 存在中文论文孤儿记录")
    if any(row["paper_id"] not in zh_papers for row in generated["dwd_zh_journal"]):
        errors.append("dwd_zh_journal: 存在中文论文孤儿记录")
    for table in sorted(EN_PAPER_TABLES - {"dwd_en_paper", "dwd_en_journal"}):
        key = "paper_id" if table == "dwd_en_author" else "id"
        if any(row[key] not in en_papers for row in generated[table]):
            errors.append(f"{table}: 存在外文论文孤儿记录")
    patent_ids = {row["patent_id"] for row in generated["dwd_patent"]}
    for table in PATENT_TABLES - {"dwd_patent"}:
        if any(row["patent_id"] not in patent_ids for row in generated[table]):
            errors.append(f"{table}: 存在专利孤儿记录")
    for prefix in ("zh", "en"):
        parent = {row["id"] for row in generated[f"dwd_{prefix}_project"]}
        if any(row["id"] not in parent for row in generated[f"dwd_{prefix}_project_output"]):
            errors.append(f"dwd_{prefix}_project_output: 存在项目孤儿记录")
        for row in generated[f"dwd_{prefix}_project_output"]:
            count_columns = [name for name in row if name.endswith("_count")]
            if row.get("total_outputs") != sum(int(row[name] or 0) for name in count_columns):
                errors.append(f"dwd_{prefix}_project_output: total_outputs 与分项不一致")
                break
    zh_reports = {row["report_id"] for row in generated["dwd_zh_report"]}
    if any(row["report_id"] not in zh_reports for row in generated["dwd_zh_report_project"]):
        errors.append("dwd_zh_report_project: 存在报告孤儿记录")
    for table in ("dwd_zh_report_author", "dwd_zh_report_paper", "dwd_en_report_author", "dwd_en_report_org"):
        for row in generated[table]:
            report_ids = json.loads(row["report_id"])
            expected = zh_reports if table.startswith("dwd_zh") else {item["report_id"] for item in generated["dwd_en_report"]}
            if any(item not in expected for item in report_ids):
                errors.append(f"{table}: JSON 报告关联存在孤儿")
                break
    return errors


def build_plan(specs: dict[str, list[FieldSpec]], state: dict[str, dict[str, Any]], target: int):
    plan: dict[str, Any] = {}
    generated: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for table, fields in sorted(specs.items()):
        item = state[table]
        current = int(item["count"])
        needed = max(0, target - current)
        columns = item["columns"] if item["exists"] else _columns_from_specs(fields)
        rows = generate_rows(table, fields, columns, current, needed)
        errors.extend(validate_rows(table, rows, columns))
        generated[table] = rows
        excel_names = {field.name.lower() for field in fields}
        actual_names = {column.name.lower() for column in columns}
        plan[table] = {
            "sheet": fields[0].sheet, "exists_before": item["exists"], "current_rows": current,
            "target_rows": target, "planned_inserts": needed, "excel_fields": len(fields),
            "actual_fields": len(columns),
            "excel_fields_missing_in_existing_table": sorted(excel_names - actual_names) if item["exists"] else [],
        }
    if all(len(rows) == target for rows in generated.values()):
        errors.extend(validate_relationships(generated))
    return plan, generated, errors


def post_relation_audit(connection: Any) -> dict[str, int]:
    queries = {
        "zh_author_orphans": "SELECT COUNT(*) total FROM dwd_zh_author x LEFT JOIN dwd_zh_paper p ON p.id=x.paper_id WHERE p.id IS NULL",
        "en_author_orphans": "SELECT COUNT(*) total FROM dwd_en_author x LEFT JOIN dwd_en_paper p ON p.id=x.paper_id WHERE p.id IS NULL",
        "patent_title_orphans": "SELECT COUNT(*) total FROM dwd_patent_title x LEFT JOIN dwd_patent p ON p.patent_id=x.patent_id WHERE p.patent_id IS NULL",
        "patent_abstract_orphans": "SELECT COUNT(*) total FROM dwd_patent_abstract x LEFT JOIN dwd_patent p ON p.patent_id=x.patent_id WHERE p.patent_id IS NULL",
        "zh_project_output_orphans": "SELECT COUNT(*) total FROM dwd_zh_project_output x LEFT JOIN dwd_zh_project p ON p.id=x.id WHERE p.id IS NULL",
        "en_project_output_orphans": "SELECT COUNT(*) total FROM dwd_en_project_output x LEFT JOIN dwd_en_project p ON p.id=x.id WHERE p.id IS NULL",
        "zh_report_project_orphans": "SELECT COUNT(*) total FROM dwd_zh_report_project x LEFT JOIN dwd_zh_report r ON r.report_id=x.report_id WHERE r.report_id IS NULL",
    }
    result = {}
    with connection.cursor() as cursor:
        for name, query in queries.items():
            cursor.execute(query)
            result[name] = int(cursor.fetchone()["total"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="按论文/专利/项目/报告 Excel 定义补齐 gkx 39 表并补到千级")
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--database", default="gkx")
    parser.add_argument("--target-per-table", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args()
    if args.database != "gkx":
        raise SystemExit("安全限制：此脚本只允许目标数据库 gkx")
    if args.target_per_table < 1:
        raise SystemExit("--target-per-table 必须大于 0")
    specs = parse_workbook(args.workbook.resolve(), expected_tables=EXPECTED_TABLES)
    connection = _connect(args.database)
    try:
        state = inspect_database(connection, specs)
        plan, generated, errors = build_plan(specs, state, args.target_per_table)
        mysql_validation = (validate_in_mysql(connection, specs, plan, generated, args.batch_size)
                            if not errors else {"valid": False, "tables_checked": 0, "rows_checked": 0})
        preview = {
            "database": args.database, "workbook": str(args.workbook.resolve()), "dry_run": not args.apply,
            "source_marker": SOURCE, "excel_table_count": len(specs),
            "existing_tables": sum(item["exists"] for item in state.values()),
            "missing_tables_to_create": [table for table, item in state.items() if not item["exists"]],
            "planned_insert_rows": sum(item["planned_inserts"] for item in plan.values()),
            "tables_needing_inserts": sum(item["planned_inserts"] > 0 for item in plan.values()),
            "validation": {"valid": not errors, "errors": errors[:100]},
            "mysql_temporary_table_validation": mysql_validation, "plan": plan,
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2, default=str))
        if errors:
            raise SystemExit(1)
        if not args.apply:
            return
        if args.confirm_database != args.database:
            raise SystemExit("拒绝写入：必须同时使用 --database gkx --confirm-database gkx")
        applied = apply_plan(connection, specs, plan, generated, args.batch_size, args.target_per_table)
        audit = audit_after(connection, specs, args.target_per_table)
        relations = post_relation_audit(connection)
        if any(relations.values()):
            raise RuntimeError(f"写入后关联复核失败: {relations}")
        print(json.dumps({"applied": True, **applied, "post_audit": audit,
                          "relationship_audit": relations}, ensure_ascii=False, indent=2, default=str))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
