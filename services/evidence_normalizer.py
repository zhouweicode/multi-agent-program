"""把领域 Tool 返回值归一为统一 EvidenceRecord。"""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

from models.schemas import EvidenceRecord

FACT_TYPES = {
    "get_person_profile": "person_profile",
    "get_employment_history": "employment",
    "get_education_history": "education",
    "match_employment_overlap": "employment_overlap",
    "get_author_papers": "paper",
    "get_common_papers": "common_paper",
    "get_common_projects": "common_project",
    "get_person_patents": "patent",
    "get_common_patents": "common_patent",
    "get_person_company_roles": "company_role",
    "get_company_projects": "company_project",
    "get_company_patents": "company_patent",
    "search_industry_segments": "industry_segment",
    "get_chain_structure": "industry_chain",
    "get_node_companies": "industry_company",
    "get_node_events": "industry_event",
    "rank_top_events": "industry_event",
    "get_neighbors": "graph_relation",
    "get_neighbors_filtered": "graph_relation",
    "find_path": "graph_path",
    "find_paths": "graph_path",
    "query_subgraph": "graph_subgraph",
    "aggregate_graph": "graph_aggregate",
    "get_graph_schema": "graph_schema",
    "calculate_path_strength": "graph_path_strength",
    "search_web": "external_web_source",
}


def _source_type(source: str, evidence_id: str = "") -> str:
    prefix = source.split(":", 1)[0].lower()
    if prefix in {"mysql", "neo4j", "milvus", "mock", "derived"}:
        return prefix
    return "mysql" if evidence_id.startswith("mysql_") else ("neo4j" if evidence_id.startswith("neo4j_") else
           ("mock" if evidence_id.startswith("ev_") else "unknown"))


def _entity_ids(row: dict[str, Any], fallback: list[str]) -> list[str]:
    for key in ("entity_ids", "authors", "participant_ids", "inventor_ids"):
        value = row.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
    if row.get("entity_id"):
        return [str(row["entity_id"])]
    return list(fallback)


def _record_id(row: dict[str, Any], evidence_id: str) -> str:
    for key in ("paper_id", "project_id", "patent_id", "event_id", "company_id", "entity_id",
                "chain_id", "segment_id", "node_id"):
        if row.get(key) is not None:
            return str(row[key])
    return evidence_id


def normalize_tool_output(tool_name: str, output: Any, fallback_entity_ids: list[str]) -> list[dict]:
    """只为具有 evidence_id 的事实建证据；聚合统计不伪造原始证据。"""
    if tool_name == "search_web" and isinstance(output, dict):
        provider = str(output.get("provider") or "unknown")
        records = []
        for row in output.get("results", []):
            if not isinstance(row, dict) or not row.get("url"):
                continue
            url = str(row["url"])
            evidence_id = "web_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
            hostname = urlparse(url).hostname or "unknown"
            content = dict(row)
            content.update({"query": output.get("query", ""), "provider": provider})
            records.append(EvidenceRecord(
                evidence_id=evidence_id,
                fact_type=FACT_TYPES[tool_name],
                source_type="web",
                source_name=f"web:{hostname}",
                source_record_id=url,
                entity_ids=list(fallback_entity_ids),
                event_time=row.get("published_at"),
                content=content,
                source_tool=tool_name,
            ).model_dump())
        return records
    if tool_name == "get_person_profile" and isinstance(output, dict) and output.get("entity_id"):
        row = dict(output)
        entity_id = str(row["entity_id"])
        evidence_id = str(row.get("evidence_id") or
                          "profile_" + hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:20])
        row["evidence_id"] = evidence_id
        return [EvidenceRecord(
            evidence_id=evidence_id,
            fact_type=FACT_TYPES[tool_name],
            source_type=_source_type(str(row.get("source_backend") or "derived:entity_registry"), evidence_id),
            source_name=str(row.get("source_backend") or "derived:entity_registry"),
            source_record_id=entity_id,
            entity_ids=[entity_id],
            content=row,
            source_tool=tool_name,
        ).model_dump()]
    rows = output if isinstance(output, list) else [output]
    if tool_name in {"find_path", "calculate_path_strength"} and isinstance(output, dict):
        path = output.get("path", output)
        rows = path.get("edges", []) if isinstance(path, dict) else []
    elif tool_name == "find_paths" and isinstance(output, dict):
        rows = [
            edge
            for path in output.get("paths", [])
            if isinstance(path, dict)
            for edge in path.get("edges", [])
        ]
    elif tool_name == "query_subgraph" and isinstance(output, dict):
        rows = output.get("edges", [])
    records: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ids = row.get("evidence_ids") or ([row.get("evidence_id")] if row.get("evidence_id") else [])
        if not ids and tool_name in {"search_industry_segments", "get_chain_structure", "get_node_companies"} \
                and not row.get("error"):
            record_id = _record_id(row, "unknown")
            evidence_id = "derived_" + hashlib.sha256(
                f"{tool_name}:{record_id}".encode()
            ).hexdigest()[:20]
            ids = [evidence_id]
            row = dict(row) | {"evidence_id": evidence_id}
        source = str(row.get("source_backend") or row.get("source_name") or row.get("source") or
                     ("derived:industry_graph" if tool_name in {
                         "search_industry_segments", "get_chain_structure", "get_node_companies"
                     } else ("derived:tool" if row.get("evidence_ids") else "unknown:tool")))
        for evidence_id in dict.fromkeys(str(item) for item in ids if item):
            effective_source = ("mock:domain_repository"
                                if source == "unknown:tool" and evidence_id.startswith("ev_") else source)
            record = EvidenceRecord(
                evidence_id=evidence_id,
                fact_type=FACT_TYPES.get(tool_name, tool_name),
                source_type=_source_type(effective_source, evidence_id),
                source_name=effective_source,
                source_record_id=_record_id(row, evidence_id),
                entity_ids=_entity_ids(row, fallback_entity_ids),
                event_time=row.get("year") or row.get("start_year") or row.get("date"),
                content=row,
                source_tool=tool_name,
            )
            records.append(record.model_dump())
    return records
