"""Bronze, Silver and Gold artifact helpers."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

from data.synthetic_gkx import TABLE_ORDER
from data.synthetic_validation import PRIMARY_KEYS


def json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def write_rows(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps({key: json_value(value) for key, value in row.items()},
                              ensure_ascii=False, sort_keys=True) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
    return {"rows": len(rows), "sha256": digest.hexdigest(), "uri": str(path)}


def read_layer(directory: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for table in TABLE_ORDER:
        with (directory / f"{table}.jsonl").open(encoding="utf-8") as handle:
            result[table] = [json.loads(line) for line in handle if line.strip()]
    return result


def normalize_layer(bronze_dir: Path, silver_dir: Path) -> dict[str, Any]:
    tables = read_layer(bronze_dir)
    manifest: dict[str, Any] = {"normalizer_version": "synthetic-normalizer@1", "tables": {}}
    for table, rows in tables.items():
        normalized = []
        for row in rows:
            item = dict(row)
            for key, value in item.items():
                if isinstance(value, str):
                    item[key] = " ".join(value.strip().split())
            for json_column in ("participants", "inventors"):
                if json_column in item and isinstance(item[json_column], str):
                    item[json_column] = json.dumps(json.loads(item[json_column]), ensure_ascii=False,
                                                   separators=(",", ":"))
            normalized.append(item)
        manifest["tables"][table] = write_rows(silver_dir / f"{table}.jsonl", normalized)
    (silver_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def mutation_plan(tables: dict[str, list[dict[str, Any]]], release_id: str) -> dict[str, Any]:
    node_tables = ("organizations", "departments", "enterprises", "industry_segments",
                   "dwd_scholar", "dwd_scholar_papers", "dwd_zh_project", "dwd_patent",
                   "industry_events")
    relationship_tables = {
        "department_org": len(tables["departments"]),
        "scholar_org": len(tables["dwd_scholar"]),
        "scholar_department": len(tables["dwd_scholar"]),
        "authorships": len(tables["dwd_scholar_paper_relation"]),
        "project_participation": len(tables["scholar_project_relation"]),
        "inventorships": len(tables["scholar_patent_relation"]),
        "patent_assignees": len(tables["dwd_patent"]),
        "scholar_enterprise": len(tables["scholar_enterprise_relation"]),
        "enterprise_industry": len(tables["enterprise_industry_relation"]),
        "industry_hierarchy": sum(bool(row.get("parent_segment_id")) for row in tables["industry_segments"]),
        "industry_event_links": len(tables["industry_events"]),
    }
    return {
        "release_id": release_id, "operation": "UPSERT_SYNTHETIC_RELEASE",
        "node_counts": {table: len(tables[table]) for table in node_tables},
        "relationship_counts": relationship_tables,
        "expected_nodes": sum(len(tables[table]) for table in node_tables),
        "expected_relationships": sum(relationship_tables.values()),
        "id_keys": {table: PRIMARY_KEYS[table] for table in node_tables},
    }
