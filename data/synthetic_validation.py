"""Quality gates for the generated gkx_synthetic dataset."""
from __future__ import annotations

from collections import Counter
import json
from typing import Any


PRIMARY_KEYS = {
    "organizations": "org_id", "departments": "dept_id", "enterprises": "enterprise_id",
    "industry_segments": "segment_id", "dwd_scholar": "scholar_id",
    "dwd_scholar_papers": "id", "dwd_scholar_paper_relation": "id",
    "dwd_zh_project": "id", "scholar_project_relation": "id", "dwd_patent": "patent_id",
    "dwd_patent_title": "patent_id", "scholar_patent_relation": "id",
    "scholar_enterprise_relation": "id", "enterprise_industry_relation": "id",
    "industry_events": "event_id",
}


def validate_dataset(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for table, key in PRIMARY_KEYS.items():
        values = [row.get(key) for row in tables.get(table, [])]
        if not values:
            errors.append(f"{table}: empty table")
        if any(value in (None, "") for value in values):
            errors.append(f"{table}: null primary key")
        duplicates = [value for value, count in Counter(values).items() if count > 1]
        if duplicates:
            errors.append(f"{table}: duplicate primary keys ({len(duplicates)})")

    scholar_ids = {row["scholar_id"] for row in tables["dwd_scholar"]}
    paper_ids = {row["id"] for row in tables["dwd_scholar_papers"]}
    project_ids = {row["id"] for row in tables["dwd_zh_project"]}
    patent_ids = {row["patent_id"] for row in tables["dwd_patent"]}
    org_ids = {row["org_id"] for row in tables["organizations"]}
    dept_ids = {row["dept_id"] for row in tables["departments"]}
    enterprise_ids = {row["enterprise_id"] for row in tables["enterprises"]}
    segment_ids = {row["segment_id"] for row in tables["industry_segments"]}

    references = (
        ("dwd_scholar", "org_id", org_ids), ("dwd_scholar", "dept_id", dept_ids),
        ("dwd_scholar_paper_relation", "scholar_id", scholar_ids),
        ("dwd_scholar_paper_relation", "related_paper_id", paper_ids),
        ("scholar_project_relation", "scholar_id", scholar_ids),
        ("scholar_project_relation", "project_id", project_ids),
        ("scholar_patent_relation", "scholar_id", scholar_ids),
        ("scholar_patent_relation", "patent_id", patent_ids),
        ("dwd_patent", "assignee_enterprise_id", enterprise_ids),
        ("scholar_enterprise_relation", "scholar_id", scholar_ids),
        ("scholar_enterprise_relation", "enterprise_id", enterprise_ids),
        ("enterprise_industry_relation", "enterprise_id", enterprise_ids),
        ("enterprise_industry_relation", "segment_id", segment_ids),
        ("industry_events", "segment_id", segment_ids),
    )
    for table, column, valid_ids in references:
        missing = sum(1 for row in tables[table] if row.get(column) not in valid_ids)
        if missing:
            errors.append(f"{table}.{column}: {missing} orphan references")

    for table, column in (("dwd_zh_project", "participants"), ("dwd_patent", "inventors")):
        for row in tables[table]:
            try:
                value = json.loads(row[column])
                if not isinstance(value, list):
                    raise ValueError("not a list")
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append(f"{table}.{column}: invalid JSON in {row[PRIMARY_KEYS[table]]}")
                break

    dois = [row["doi"] for row in tables["dwd_scholar_papers"]]
    if len(dois) != len(set(dois)):
        errors.append("dwd_scholar_papers.doi: duplicate DOI")
    names = Counter(row["name_zh"] for row in tables["dwd_scholar"])
    ambiguous_name_count = sum(1 for count in names.values() if count > 1)
    if not ambiguous_name_count:
        warnings.append("no ambiguous scholar names; entity-resolution cases are too easy")

    evidence_tables = ("dwd_scholar_paper_relation", "scholar_project_relation",
                       "scholar_patent_relation", "scholar_enterprise_relation", "industry_events")
    missing_evidence = sum(1 for table in evidence_tables for row in tables[table] if not row.get("evidence_id"))
    if missing_evidence:
        errors.append(f"relations: {missing_evidence} rows without evidence_id")

    return {
        "valid": not errors, "errors": errors, "warnings": warnings,
        "metrics": {
            "table_count": len(tables), "row_count": sum(len(rows) for rows in tables.values()),
            "scholar_count": len(scholar_ids), "paper_count": len(paper_ids),
            "project_count": len(project_ids), "patent_count": len(patent_ids),
            "ambiguous_scholar_name_count": ambiguous_name_count,
            "authorship_count": len(tables["dwd_scholar_paper_relation"]),
        },
    }
