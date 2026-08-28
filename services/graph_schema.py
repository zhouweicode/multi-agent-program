"""图查询治理 Schema：这是允许查询的契约，不是数据库任意 Schema 转储。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

NODE_TYPES: dict[str, dict[str, Any]] = {
    "Scholar": {
        "id_field": "scholar_id",
        "queryable_fields": (
            "scholar_id",
            "name",
            "name_zh",
            "name_en",
            "title",
            "research_field",
            "synthetic",
        ),
    },
    "Organization": {
        "id_field": "org_id",
        "queryable_fields": ("org_id", "name", "name_zh", "name_en", "synthetic"),
    },
    "Department": {
        "id_field": "dept_id",
        "queryable_fields": ("dept_id", "name", "name_zh", "synthetic"),
    },
    "Enterprise": {
        "id_field": "enterprise_id",
        "queryable_fields": (
            "enterprise_id",
            "name",
            "name_zh",
            "name_en",
            "industry",
            "synthetic",
        ),
    },
    "Paper": {
        "id_field": "paper_id",
        "queryable_fields": ("paper_id", "title", "year", "venue", "synthetic"),
    },
    "Project": {
        "id_field": "project_id",
        "queryable_fields": (
            "project_id",
            "name",
            "title",
            "start_year",
            "end_year",
            "year",
            "synthetic",
        ),
    },
    "Patent": {
        "id_field": "patent_id",
        "queryable_fields": (
            "patent_id",
            "title",
            "publication_number",
            "year",
            "synthetic",
        ),
    },
    "IndustrySegment": {
        "id_field": "segment_id",
        "queryable_fields": (
            "segment_id",
            "name",
            "name_zh",
            "level",
            "parent_segment_id",
            "synthetic",
        ),
    },
    "IndustryEvent": {
        "id_field": "event_id",
        "queryable_fields": (
            "event_id",
            "title",
            "name",
            "date",
            "event_date",
            "year",
            "importance",
            "score",
            "synthetic",
        ),
    },
    "Technology": {
        "id_field": "tech_id",
        "queryable_fields": ("tech_id", "name", "name_zh", "synthetic"),
    },
    "School": {
        "id_field": "school_id",
        "queryable_fields": ("school_id", "name", "name_zh", "synthetic"),
    },
    "College": {
        "id_field": "college_id",
        "queryable_fields": ("college_id", "name", "name_zh", "synthetic"),
    },
    "Team": {
        "id_field": "team_id",
        "queryable_fields": ("team_id", "name", "synthetic"),
    },
    "Employment": {
        "id_field": "employment_id",
        "queryable_fields": (
            "employment_id",
            "position",
            "start_year",
            "end_year",
            "synthetic",
        ),
    },
    "Education": {
        "id_field": "education_id",
        "queryable_fields": (
            "education_id",
            "degree",
            "start_year",
            "end_year",
            "synthetic",
        ),
    },
    "TrendReport": {
        "id_field": "report_id",
        "queryable_fields": ("report_id", "title", "year", "synthetic"),
    },
}

RELATION_TYPES: dict[str, dict[str, Any]] = {
    "WORKS_AT": {"source": ("Scholar",), "target": ("Organization", "Enterprise")},
    "AFFILIATED_WITH": {"source": ("Scholar",), "target": ("Organization",)},
    "MEMBER_OF": {"source": ("Scholar",), "target": ("Department", "Team")},
    "AUTHOR_OF": {"source": ("Scholar",), "target": ("Paper",)},
    "PARTICIPATES_IN": {"source": ("Scholar", "Enterprise"), "target": ("Project",)},
    "INVENTED": {"source": ("Scholar",), "target": ("Patent",)},
    "ASSIGNED_TO": {"source": ("Patent",), "target": ("Enterprise",)},
    "HAS_ENTERPRISE_ROLE": {"source": ("Scholar",), "target": ("Enterprise",)},
    "HAS_EMPLOYMENT": {"source": ("Scholar",), "target": ("Employment",)},
    "EMPLOYED_BY": {
        "source": ("Employment",),
        "target": ("Organization", "Enterprise"),
    },
    "HAS_EDUCATION": {"source": ("Scholar",), "target": ("Education",)},
    "STUDIED_AT": {"source": ("Education",), "target": ("School", "College")},
    "PAPER_COOP_REL": {"source": ("Scholar",), "target": ("Scholar",)},
    "COLLEAGUE_REL": {"source": ("Scholar",), "target": ("Scholar",)},
    "ALUMNI_REL": {"source": ("Scholar",), "target": ("Scholar",)},
    "COOPERATES_ON": {"source": ("Enterprise",), "target": ("Project",)},
    "BELONGS_TO": {
        "source": ("Enterprise", "IndustryEvent", "Department"),
        "target": ("IndustrySegment", "Organization"),
    },
    "SUBSEGMENT_OF": {"source": ("IndustrySegment",), "target": ("IndustrySegment",)},
    "UPSTREAM_OF": {"source": ("IndustrySegment",), "target": ("IndustrySegment",)},
    "HAS_EVENT": {"source": ("IndustrySegment",), "target": ("IndustryEvent",)},
    "PART_OF_SEGMENT": {
        "source": ("Project", "Technology"),
        "target": ("IndustrySegment",),
    },
    "OWNS_TECH": {"source": ("Enterprise",), "target": ("Technology",)},
    "EVENT_EXPERT_REL": {"source": ("IndustryEvent",), "target": ("Scholar",)},
    "HAS_TREND_REPORT": {"source": ("Scholar",), "target": ("TrendReport",)},
    # Mock 图的稳定关系也纳入同一白名单，保证双后端契约一致。
    "COAUTHOR": {"source": ("Scholar",), "target": ("Scholar",)},
    "ADVISOR_OF": {"source": ("Scholar",), "target": ("Enterprise",)},
    "LEADS_LAB": {"source": ("Scholar",), "target": ("Enterprise",)},
    "LOCATED_IN_CHAIN": {"source": ("Enterprise",), "target": ("IndustrySegment",)},
}

RELATION_QUERYABLE_FIELDS = (
    "role",
    "year",
    "start_year",
    "end_year",
    "weight",
    "confidence",
    "strength_score",
    "importance",
    "evidence_id",
    "synthetic",
)


def validate_node_labels(labels: list[str]) -> None:
    unknown = set(labels) - set(NODE_TYPES)
    if unknown:
        raise ValueError("图查询包含未授权 Label: " + ", ".join(sorted(unknown)))


def validate_relation_types(relation_types: list[str]) -> None:
    unknown = set(relation_types) - set(RELATION_TYPES)
    if unknown:
        raise ValueError("图查询包含未授权关系: " + ", ".join(sorted(unknown)))


def validate_field(scope: str, field: str, labels: list[str]) -> None:
    if scope == "relation":
        if field not in RELATION_QUERYABLE_FIELDS:
            raise ValueError(f"关系属性未授权: {field}")
        return
    if not labels:
        raise ValueError(f"{scope} 字段校验缺少 Label")
    allowed = set.intersection(
        *(set(NODE_TYPES[label]["queryable_fields"]) for label in labels)
    )
    if field not in allowed:
        raise ValueError(f"{scope} 属性未授权或并非所有 Label 共有: {field}")


def graph_schema_payload() -> dict[str, Any]:
    payload = {
        "schema_version": "graph-query-v1",
        "node_types": {
            name: {
                "id_field": value["id_field"],
                "queryable_fields": list(value["queryable_fields"]),
            }
            for name, value in NODE_TYPES.items()
        },
        "relation_types": {
            name: {
                "source": list(value["source"]),
                "target": list(value["target"]),
                "queryable_fields": list(RELATION_QUERYABLE_FIELDS),
            }
            for name, value in RELATION_TYPES.items()
        },
        "limits": {
            "max_hops": 6,
            "subgraph_max_hops": 3,
            "max_nodes": 200,
            "max_edges": 500,
            "max_rows": 100,
            "top_k_paths": 10,
        },
        "read_only": True,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return payload | {
        "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    }


def infer_mock_label(entity_id: str) -> str:
    prefixes = (
        ("person_", "Scholar"),
        ("SCH", "Scholar"),
        ("company_", "Enterprise"),
        ("node_", "IndustrySegment"),
        ("chain_", "IndustrySegment"),
        ("paper_", "Paper"),
        ("project_", "Project"),
        ("patent_", "Patent"),
    )
    return next(
        (label for prefix, label in prefixes if entity_id.startswith(prefix)),
        "Technology",
    )
