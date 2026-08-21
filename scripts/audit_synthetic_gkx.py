"""Report whether gkx_synthetic has enough coverage for graph and GraphRAG scenarios."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from data.synthetic_gkx import read_dataset
from data.synthetic_validation import validate_dataset


def _coverage(values: set[str], total: int) -> dict:
    return {"count": len(values), "rate": round(len(values) / total, 4) if total else 0.0}


def audit(tables: dict[str, list[dict]]) -> dict:
    quality = validate_dataset(tables)
    scholars = tables["dwd_scholar"]
    scholar_count = len(scholars)
    paper_scholars = {row["scholar_id"] for row in tables["dwd_scholar_paper_relation"]}
    project_scholars = {row["scholar_id"] for row in tables["scholar_project_relation"]}
    patent_scholars = {row["scholar_id"] for row in tables["scholar_patent_relation"]}
    enterprise_scholars = {row["scholar_id"] for row in tables["scholar_enterprise_relation"]}
    cross_domain = paper_scholars & project_scholars & patent_scholars & enterprise_scholars

    names: dict[str, list[dict]] = defaultdict(list)
    for scholar in scholars:
        names[scholar["name_zh"]].append(scholar)
    ambiguous = {name: rows for name, rows in names.items() if len(rows) > 1}
    multi_org_ambiguous = sum(1 for rows in ambiguous.values() if len({row["org_id"] for row in rows}) > 1)

    parent: dict[str, str] = {row["scholar_id"]: row["scholar_id"] for row in scholars}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    paper_authors: dict[str, list[str]] = defaultdict(list)
    for row in tables["dwd_scholar_paper_relation"]:
        paper_authors[row["related_paper_id"]].append(row["scholar_id"])
    for authors in paper_authors.values():
        for author in authors[1:]:
            union(authors[0], author)
    components = Counter(find(scholar_id) for scholar_id in parent)

    relation_rows = sum(len(tables[name]) for name in (
        "dwd_scholar_paper_relation", "scholar_project_relation", "scholar_patent_relation",
        "scholar_enterprise_relation", "enterprise_industry_relation", "industry_events",
    ))
    evidence_rows = sum(sum(bool(row.get("evidence_id")) for row in tables[name]) for name in (
        "dwd_scholar_paper_relation", "scholar_project_relation", "scholar_patent_relation",
        "scholar_enterprise_relation", "industry_events",
    ))
    expected_evidence_rows = relation_rows - len(tables["enterprise_industry_relation"])

    domains = {
        "research_fields": len({row["research_field"] for row in scholars}),
        "paper_years": sorted({row["year"] for row in tables["dwd_scholar_paper_relation"] if row.get("year")}),
        "organization_types": sorted({row["org_type"] for row in tables["organizations"]}),
        "enterprise_roles": sorted({row["role"] for row in tables["scholar_enterprise_relation"]}),
    }
    readiness_checks = {
        "base_quality": quality["valid"],
        "scholar_scale": scholar_count >= 2_000,
        "paper_scale": len(tables["dwd_scholar_papers"]) >= 15_000,
        "relationship_scale": relation_rows >= 50_000,
        "same_name_resolution": len(ambiguous) >= 100 and multi_org_ambiguous >= 100,
        "cross_domain_queries": len(cross_domain) >= 200,
        "connected_coauthor_graph": max(components.values(), default=0) / scholar_count >= 0.95,
        "evidence_coverage": evidence_rows == expected_evidence_rows,
        "domain_diversity": domains["research_fields"] >= 10,
    }
    return {
        "ready": all(readiness_checks.values()),
        "checks": readiness_checks,
        "quality": quality,
        "coverage": {
            "scholars_with_papers": _coverage(paper_scholars, scholar_count),
            "scholars_with_projects": _coverage(project_scholars, scholar_count),
            "scholars_with_patents": _coverage(patent_scholars, scholar_count),
            "scholars_with_enterprise_roles": _coverage(enterprise_scholars, scholar_count),
            "cross_domain_scholars": _coverage(cross_domain, scholar_count),
            "ambiguous_names": len(ambiguous),
            "ambiguous_names_across_organizations": multi_org_ambiguous,
            "coauthor_components": len(components),
            "largest_coauthor_component": max(components.values(), default=0),
            "evidence_rate": round(evidence_rows / expected_evidence_rows, 4) if expected_evidence_rows else 0.0,
        },
        "scale": {
            "nodes": sum(len(tables[name]) for name in (
                "organizations", "departments", "enterprises", "industry_segments", "dwd_scholar",
                "dwd_scholar_papers", "dwd_zh_project", "dwd_patent", "industry_events",
            )),
            "relationships": relation_rows,
        },
        "domains": domains,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="审计 gkx_synthetic 是否满足图谱构建与查询需求")
    parser.add_argument("--input", type=Path, default=Path(".runtime/synthetic_gkx"))
    args = parser.parse_args()
    result = audit(read_dataset(args.input))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
