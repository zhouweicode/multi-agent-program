"""True tuple-watermark incremental KG workflow for gkx_synthetic."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from data.synthetic_gkx import TABLE_ORDER
from data.synthetic_validation import PRIMARY_KEYS
from kg_workflow.artifacts import json_value, write_rows
from kg_workflow.pipeline import KGWorkflow, QualityGateError, ReconciliationError
from repositories.milvus_entity_repository import MilvusEntityRepository
from scripts.sync_neo4j_synthetic_graph import STEPS


STEP_SOURCES = {
    "organizations": ("organizations",),
    "departments": ("departments", "department_org"),
    "enterprises": ("enterprises",),
    "industry_segments": ("industry_segments", "industry_hierarchy"),
    "dwd_scholar": ("scholars", "scholar_org", "scholar_department"),
    "dwd_scholar_papers": ("papers",),
    "dwd_scholar_paper_relation": ("authorships",),
    "dwd_zh_project": ("projects",),
    "scholar_project_relation": ("project_participation",),
    "dwd_patent": ("patents", "patent_assignees"),
    "dwd_patent_title": ("patents",),
    "scholar_patent_relation": ("inventorships",),
    "scholar_enterprise_relation": ("scholar_enterprise",),
    "enterprise_industry_relation": ("enterprise_industry",),
    "industry_events": ("industry_events", "industry_event_links"),
}

NODE_DELETES = {
    "organizations": ("Organization", "org_id"), "departments": ("Department", "dept_id"),
    "enterprises": ("Enterprise", "enterprise_id"),
    "industry_segments": ("IndustrySegment", "segment_id"),
    "dwd_scholar": ("Scholar", "scholar_id"), "dwd_scholar_papers": ("Paper", "id"),
    "dwd_zh_project": ("Project", "id"), "dwd_patent": ("Patent", "patent_id"),
    "industry_events": ("IndustryEvent", "event_id"),
}

RELATION_DELETES = {
    "dwd_scholar_paper_relation": """MATCH (s:Scholar {scholar_id:$row.scholar_id})
        -[r:AUTHOR_OF]->(p:Paper {paper_id:$row.related_paper_id}) DELETE r""",
    "scholar_project_relation": """MATCH (s:Scholar {scholar_id:$row.scholar_id})
        -[r:PARTICIPATES_IN]->(p:Project {project_id:$row.project_id}) DELETE r""",
    "scholar_patent_relation": """MATCH (s:Scholar {scholar_id:$row.scholar_id})
        -[r:INVENTED]->(p:Patent {patent_id:$row.patent_id}) DELETE r""",
    "scholar_enterprise_relation": """MATCH (s:Scholar {scholar_id:$row.scholar_id})
        -[r:HAS_ENTERPRISE_ROLE]->(e:Enterprise {enterprise_id:$row.enterprise_id}) DELETE r""",
    "enterprise_industry_relation": """MATCH (e:Enterprise {enterprise_id:$row.enterprise_id})
        -[r:BELONGS_TO]->(i:IndustrySegment {segment_id:$row.segment_id}) DELETE r""",
}


def _event_rows(directory: Path) -> list[dict[str, Any]]:
    rows = []
    for table in TABLE_ORDER:
        path = directory / f"{table}.jsonl"
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


class IncrementalKGWorkflow(KGWorkflow):
    SUPPORTED_RUN_TYPES = {"INCREMENTAL"}
    STEPS = (
        "contract_check", "extract_changes", "normalize_changes", "incremental_quality_gate",
        "build_incremental_plan", "publish_incremental_neo4j", "publish_incremental_milvus",
        "incremental_reconcile", "incremental_activate",
    )

    def _extract_changes(self, context: dict[str, Any]) -> dict[str, Any]:
        previous = self.registry.watermarks(self.settings.mysql_database)
        missing = [table for table in TABLE_ORDER if not previous.get(table, {}).get("updated_at")]
        if missing:
            raise ValueError(f"增量运行缺少组合Watermark，请先执行新SNAPSHOT: {missing}")
        directory = context["release_dir"] / "bronze-events"
        manifest: dict[str, Any] = {"tables": {}, "watermarks": dict(previous)}
        connection = self._mysql()
        total = 0
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                for table in TABLE_ORDER:
                    key = PRIMARY_KEYS[table]
                    watermark = previous[table]
                    cursor.execute(f"""SELECT * FROM {table}
                        WHERE updated_at > %s OR (updated_at = %s AND {key} > %s)
                        ORDER BY updated_at,{key}""",
                        (watermark["updated_at"], watermark["updated_at"], watermark["primary_key"]))
                    source_rows = cursor.fetchall()
                    events = []
                    for row in source_rows:
                        payload = {column: json_value(value) for column, value in row.items()}
                        record_id = str(row[key])
                        payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                        payload_hash = hashlib.sha256(payload_text.encode()).hexdigest()
                        updated_at = str(row["updated_at"])
                        operation = "DELETE" if int(row.get("status", 1)) == 0 else "UPSERT"
                        event_id = hashlib.sha256(
                            f"{self.settings.mysql_database}:{table}:{record_id}:{updated_at}:{payload_hash}".encode()
                        ).hexdigest()
                        events.append({"event_id": event_id, "dataset": table,
                                       "record_id": record_id, "operation": operation,
                                       "updated_at": updated_at, "payload_hash": payload_hash,
                                       "payload": payload})
                    manifest["tables"][table] = write_rows(directory / f"{table}.jsonl", events)
                    total += len(events)
                    if source_rows:
                        last = source_rows[-1]
                        manifest["watermarks"][table] = {
                            "updated_at": str(last["updated_at"]), "primary_key": str(last[key])}
            connection.rollback()
        finally:
            connection.close()
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"uri": str(directory), "event_count": total,
                "tables": manifest["tables"], "watermarks": manifest["watermarks"]}

    def _normalize_changes(self, context: dict[str, Any]) -> dict[str, Any]:
        source = Path(context["outputs"]["extract_changes"]["uri"])
        target = context["release_dir"] / "silver-events"
        count = 0
        for table in TABLE_ORDER:
            events = []
            with (source / f"{table}.jsonl").open(encoding="utf-8") as handle:
                for line in handle:
                    event = json.loads(line)
                    event["payload"] = {key: " ".join(value.strip().split()) if isinstance(value, str) else value
                                        for key, value in event["payload"].items()}
                    events.append(event)
            write_rows(target / f"{table}.jsonl", events)
            count += len(events)
        return {"uri": str(target), "event_count": count, "normalizer_version": "change-normalizer@1"}

    def _incremental_quality_gate(self, context: dict[str, Any]) -> dict[str, Any]:
        events = _event_rows(Path(context["outputs"]["normalize_changes"]["uri"]))
        ids = [event.get("event_id") for event in events]
        errors = []
        if len(ids) != len(set(ids)):
            errors.append("duplicate event_id")
        for event in events:
            if event.get("operation") not in {"UPSERT", "DELETE"}:
                errors.append(f"invalid operation: {event.get('event_id')}")
            if not all(event.get(key) for key in ("dataset", "record_id", "updated_at", "payload_hash")):
                errors.append(f"incomplete envelope: {event.get('event_id')}")
        report = {"passed": not errors, "event_count": len(events), "errors": errors,
                  "delete_count": sum(event["operation"] == "DELETE" for event in events)}
        path = context["release_dir"] / "quality" / "incremental-quality.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if errors:
            raise QualityGateError(str(path))
        return report | {"uri": str(path)}

    def _build_incremental_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        events = _event_rows(Path(context["outputs"]["normalize_changes"]["uri"]))
        counts: dict[str, dict[str, int]] = {}
        for event in events:
            counts.setdefault(event["dataset"], {"UPSERT": 0, "DELETE": 0})[event["operation"]] += 1
        plan = {"release_id": context["release_id"], "event_count": len(events), "mutations": counts,
                "event_ids": [event["event_id"] for event in events]}
        path = context["release_dir"] / "gold" / "incremental-mutation-plan.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return plan | {"uri": str(path)}

    def _enriched_patents(self, patent_ids: set[str]) -> list[dict[str, Any]]:
        if not patent_ids:
            return []
        connection = self._mysql()
        try:
            with connection.cursor() as cursor:
                placeholders = ",".join(["%s"] * len(patent_ids))
                cursor.execute(f"""SELECT p.*,t.title_zh,t.title_localized FROM dwd_patent p
                    LEFT JOIN dwd_patent_title t ON t.patent_id=p.patent_id AND t.status=1
                    WHERE p.patent_id IN ({placeholders}) AND p.status=1""", tuple(sorted(patent_ids)))
                rows = cursor.fetchall()
            connection.rollback()
            return [{key: json_value(value) for key, value in row.items()} for row in rows]
        finally:
            connection.close()

    def _publish_incremental_neo4j(self, context: dict[str, Any]) -> dict[str, Any]:
        events = _event_rows(Path(context["outputs"]["normalize_changes"]["uri"]))
        if not context["apply"]:
            return {"dry_run": True, "mutation_count": len(events)}
        from neo4j import GraphDatabase
        step_map = {step.name: step for step in STEPS}
        by_dataset: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLE_ORDER}
        for event in events:
            by_dataset[event["dataset"]].append(event)
        driver = GraphDatabase.driver(self.settings.neo4j_uri,
                                      auth=(self.settings.neo4j_user, self.settings.neo4j_password))
        applied = 0
        try:
            with driver.session(database=self.settings.neo4j_database) as session:
                for dataset, dataset_events in by_dataset.items():
                    for event in (item for item in dataset_events if item["operation"] == "DELETE"):
                        row = event["payload"]
                        if dataset in NODE_DELETES:
                            label, key = NODE_DELETES[dataset]
                            session.run(f"MATCH (n:{label} {{{key}:$id}}) DETACH DELETE n", id=row[key]).consume()
                        elif dataset in RELATION_DELETES:
                            session.run(RELATION_DELETES[dataset], row=row).consume()
                        applied += 1
                    upserts = [item["payload"] for item in dataset_events if item["operation"] == "UPSERT"]
                    if not upserts:
                        continue
                    for step_name in STEP_SOURCES[dataset]:
                        rows = upserts
                        if step_name == "patents":
                            ids = {str(row.get("patent_id")) for row in upserts if row.get("patent_id")}
                            rows = self._enriched_patents(ids)
                        if rows:
                            session.run(step_map[step_name].cypher, rows=rows,
                                        release_id=context["release_id"]).consume()
                    applied += len(upserts)
        finally:
            driver.close()
        return {"dry_run": False, "mutation_count": len(events), "applied": applied}

    def _publish_incremental_milvus(self, context: dict[str, Any]) -> dict[str, Any]:
        active = self.registry.active_release()
        if not active:
            raise ValueError("没有active release，不能执行增量索引")
        events = _event_rows(Path(context["outputs"]["normalize_changes"]["uri"]))
        scholar_events = [event for event in events if event["dataset"] == "dwd_scholar"]
        if not context["apply"]:
            return {"dry_run": True, "collection": active["milvus_collection"],
                    "changed_scholars": len(scholar_events)}
        settings = replace(self.settings, milvus_collection=active["milvus_collection"])
        repository = MilvusEntityRepository(settings)
        try:
            deleted = [event["record_id"] for event in scholar_events if event["operation"] == "DELETE"]
            if deleted:
                repository.delete_entities(deleted)
            rows = [event["payload"] for event in scholar_events if event["operation"] == "UPSERT"]
            entities = [{"entity_id": row["scholar_id"], "canonical_id": row["scholar_id"],
                         "name": row.get("name_zh") or row.get("name_en") or "",
                         "organization": row.get("scholar_org_name_zh") or "",
                         "title": row.get("work_experience_position_zh") or ""} for row in rows]
            upserted = repository.upsert_entities(entities)
            count = repository.count()
        finally:
            repository.close()
        return {"dry_run": False, "collection": active["milvus_collection"],
                "upserted": upserted, "deleted": len(deleted), "entity_count": count}

    def _incremental_reconcile(self, context: dict[str, Any]) -> dict[str, Any]:
        if not context["apply"]:
            return {"dry_run": True, "passed": True}
        command = [sys.executable, "-m", "scripts.sync_neo4j_synthetic_graph",
                   "--release-id", context["release_id"]]
        preview = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
        steps = preview["steps"]
        expected_nodes = sum(steps[name] for name in (
            "organizations", "departments", "enterprises", "industry_segments", "scholars",
            "papers", "projects", "patents", "industry_events"))
        expected_relationships = sum(steps[name] for name in (
            "department_org", "scholar_org", "scholar_department", "authorships",
            "project_participation", "inventorships", "patent_assignees", "scholar_enterprise",
            "enterprise_industry", "industry_hierarchy", "industry_event_links"))
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(self.settings.neo4j_uri,
                                      auth=(self.settings.neo4j_user, self.settings.neo4j_password))
        try:
            with driver.session(database=self.settings.neo4j_database) as session:
                nodes = session.run("MATCH (n) WHERE n.synthetic=true RETURN count(n) AS n").single()["n"]
                relationships = session.run(
                    "MATCH ()-[r]->() WHERE r.synthetic=true RETURN count(r) AS n").single()["n"]
        finally:
            driver.close()
        milvus_count = context["outputs"]["publish_incremental_milvus"]["entity_count"]
        expected_scholars = steps["scholars"]
        passed = nodes == expected_nodes and relationships == expected_relationships and milvus_count == expected_scholars
        result = {"passed": passed, "neo4j_nodes": nodes, "expected_nodes": expected_nodes,
                  "neo4j_relationships": relationships, "expected_relationships": expected_relationships,
                  "milvus_entities": milvus_count, "expected_scholars": expected_scholars}
        if not passed:
            raise ReconciliationError(json.dumps(result, ensure_ascii=False))
        return result

    def _incremental_activate(self, context: dict[str, Any]) -> dict[str, Any]:
        if not context["apply"]:
            return {"dry_run": True, "activated": False}
        events = _event_rows(Path(context["outputs"]["normalize_changes"]["uri"]))
        inserted, duplicates = self.registry.record_events(context["run_id"], events)
        active = self.registry.active_release()
        quality = context["outputs"]["incremental_quality_gate"]
        self.registry.register_release(context["release_id"], context["run_id"],
                                       str(context["release_dir"]), self.settings.neo4j_database,
                                       active["milvus_collection"], quality)
        watermarks = context["outputs"]["extract_changes"]["watermarks"]
        self.registry.activate(context["release_id"], self.settings.mysql_database, watermarks)
        return {"activated": True, "event_count": len(events), "inbox_inserted": inserted,
                "inbox_duplicates": duplicates, "watermark_count": len(watermarks),
                "milvus_collection": active["milvus_collection"]}
