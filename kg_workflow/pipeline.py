"""Recoverable Phase-1 KG build pipeline.

This is a local durable orchestrator. Each method is deliberately Activity-shaped so the
same boundaries can later be moved to Temporal without changing artifact contracts.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable
from uuid import uuid4

from data.synthetic_gkx import TABLE_ORDER
from data.synthetic_validation import PRIMARY_KEYS, validate_dataset
from kg_workflow.artifacts import mutation_plan, normalize_layer, read_layer, write_rows
from kg_workflow.registry import KGWorkflowRegistry
from models.settings import Settings
from repositories.milvus_entity_repository import MilvusEntityRepository
from scripts.audit_synthetic_gkx import audit


class QualityGateError(RuntimeError):
    pass


class ReconciliationError(RuntimeError):
    pass


class KGWorkflow:
    SUPPORTED_RUN_TYPES = {"SNAPSHOT"}
    STEPS = (
        "contract_check", "extract_bronze", "normalize_silver", "quality_gate",
        "build_gold_plan", "publish_neo4j", "publish_milvus", "reconcile", "activate",
    )

    def __init__(self, settings: Settings | None = None,
                 registry: KGWorkflowRegistry | None = None,
                 artifact_root: str | Path = ".runtime/kg-workflow"):
        self.settings = settings or Settings.from_env()
        self.registry = registry or KGWorkflowRegistry()
        self.artifact_root = Path(artifact_root)

    def start(self, *, release_id: str, apply: bool = False, run_id: str | None = None,
              run_type: str = "SNAPSHOT") -> dict[str, Any]:
        if run_type not in self.SUPPORTED_RUN_TYPES:
            raise NotImplementedError(f"{type(self).__name__}不支持{run_type}，不得静默退化为其他模式")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", release_id):
            raise ValueError("release_id 只能包含字母、数字、点、下划线和连字符")
        run_id = run_id or f"kg-{uuid4().hex[:16]}"
        existing = self.registry.run(run_id)
        config = {
            "apply": apply, "artifact_root": str(self.artifact_root),
            "neo4j_database": self.settings.neo4j_database,
            "embedding_provider": self.settings.embedding_provider,
        }
        if not existing:
            self.registry.create_run(run_id, release_id, run_type,
                                     self.settings.mysql_database, config)
        elif existing["release_id"] != release_id:
            raise ValueError("恢复运行时 release_id 必须保持不变")
        elif bool(existing["config"].get("apply")) != apply:
            raise ValueError("恢复运行时不能切换 dry-run/apply 模式，请创建新 run")
        release_dir = self.artifact_root / release_id
        context: dict[str, Any] = {
            "run_id": run_id, "release_id": release_id, "release_dir": release_dir,
            "apply": apply, "outputs": {},
        }
        self.registry.set_run_status(run_id, "RUNNING")
        try:
            for step_name in self.STEPS:
                previous = self.registry.step(run_id, step_name)
                if previous and previous["status"] == "COMPLETED":
                    context["outputs"][step_name] = previous["output"]
                    continue
                output = self._execute_step(run_id, step_name, getattr(self, f"_{step_name}"), context)
                context["outputs"][step_name] = output
            final_status = "ACTIVE" if apply else "DRY_RUN_COMPLETED"
            self.registry.set_run_status(run_id, final_status)
            return {"run_id": run_id, "release_id": release_id, "status": final_status,
                    "artifact_uri": str(release_dir), "steps": context["outputs"]}
        except Exception as exc:
            self.registry.set_run_status(run_id, "FAILED", {
                "type": type(exc).__name__, "message": str(exc),
            })
            raise

    def _execute_step(self, run_id: str, step_name: str,
                      operation: Callable[[dict[str, Any]], dict[str, Any]],
                      context: dict[str, Any]) -> dict[str, Any]:
        self.registry.start_step(run_id, step_name)
        try:
            output = operation(context)
            self.registry.complete_step(run_id, step_name, output)
            return output
        except Exception as exc:
            self.registry.fail_step(run_id, step_name, {
                "type": type(exc).__name__, "message": str(exc),
            })
            raise

    def _mysql(self):
        import pymysql
        return pymysql.connect(
            host=self.settings.mysql_host, port=self.settings.mysql_port,
            user=self.settings.mysql_user, password=self.settings.mysql_password,
            database=self.settings.mysql_database, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor, autocommit=False,
        )

    def _contract_check(self, context: dict[str, Any]) -> dict[str, Any]:
        if not (self.settings.mysql_database == "gkx_synthetic" or
                self.settings.mysql_database.startswith("gkx_synthetic_")):
            raise ValueError("Phase 1 当前只允许 gkx_synthetic 数据源")
        connection = self._mysql()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("""
                    SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY
                    FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s
                    ORDER BY TABLE_NAME,ORDINAL_POSITION
                """, (self.settings.mysql_database,))
                rows = cursor.fetchall()
            connection.rollback()
        finally:
            connection.close()
        actual_tables = {row["TABLE_NAME"] for row in rows}
        missing = sorted(set(TABLE_ORDER) - actual_tables)
        if missing:
            raise ValueError(f"缺少数据表: {missing}")
        schema_text = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        output = {"schema_sha256": hashlib.sha256(schema_text.encode()).hexdigest(),
                  "table_count": len(actual_tables), "required_table_count": len(TABLE_ORDER)}
        path = context["release_dir"] / "contracts" / "schema.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"summary": output, "columns": rows}, ensure_ascii=False,
                                   indent=2, default=str) + "\n", encoding="utf-8")
        return output | {"uri": str(path)}

    def _extract_bronze(self, context: dict[str, Any]) -> dict[str, Any]:
        bronze = context["release_dir"] / "bronze"
        manifest: dict[str, Any] = {"source_database": self.settings.mysql_database,
                                    "tables": {}, "watermarks": {}}
        connection = self._mysql()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                for table in TABLE_ORDER:
                    primary_key = PRIMARY_KEYS[table]
                    cursor.execute(f"SELECT * FROM {table} ORDER BY {primary_key}")
                    rows = cursor.fetchall()
                    manifest["tables"][table] = write_rows(bronze / f"{table}.jsonl", rows)
                    if rows and "updated_at" in rows[0]:
                        positions = [(str(row["updated_at"]), str(row[primary_key])) for row in rows
                                     if row.get("updated_at") is not None]
                        position = max(positions) if positions else (None, None)
                        manifest["watermarks"][table] = {"updated_at": position[0],
                                                          "primary_key": position[1]}
                    else:
                        manifest["watermarks"][table] = {"row_count": len(rows),
                                                          "primary_key": str(rows[-1][primary_key]) if rows else None}
            connection.rollback()
        finally:
            connection.close()
        (bronze / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return {"uri": str(bronze), "tables": manifest["tables"],
                "watermarks": manifest["watermarks"],
                "row_count": sum(item["rows"] for item in manifest["tables"].values())}

    def _normalize_silver(self, context: dict[str, Any]) -> dict[str, Any]:
        manifest = normalize_layer(context["release_dir"] / "bronze",
                                   context["release_dir"] / "silver")
        return {"uri": str(context["release_dir"] / "silver"),
                "normalizer_version": manifest["normalizer_version"],
                "row_count": sum(item["rows"] for item in manifest["tables"].values())}

    def _quality_gate(self, context: dict[str, Any]) -> dict[str, Any]:
        tables = read_layer(context["release_dir"] / "silver")
        validation = validate_dataset(tables)
        readiness = audit(tables)
        result = {"passed": validation["valid"] and readiness["ready"],
                  "validation": validation, "readiness": readiness}
        path = context["release_dir"] / "quality" / "quality-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        if not result["passed"]:
            raise QualityGateError(f"质量门禁失败，报告: {path}")
        return {"passed": True, "uri": str(path), "metrics": readiness["scale"]}

    def _build_gold_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        plan = mutation_plan(read_layer(context["release_dir"] / "silver"), context["release_id"])
        path = context["release_dir"] / "gold" / "mutation-plan.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        return plan | {"uri": str(path)}

    def _publish_neo4j(self, context: dict[str, Any]) -> dict[str, Any]:
        command = [sys.executable, "-m", "scripts.sync_neo4j_synthetic_graph",
                   "--release-id", context["release_id"]]
        if context["apply"]:
            command.append("--apply")
        completed = subprocess.run(command, cwd=Path.cwd(), check=True, capture_output=True, text=True)
        return json.loads(completed.stdout)

    def _publish_milvus(self, context: dict[str, Any]) -> dict[str, Any]:
        safe_release = re.sub(r"[^a-zA-Z0-9_]", "_", context["release_id"])
        collection = f"scholar_entities_{safe_release}"[:255]
        rows = read_layer(context["release_dir"] / "silver")["dwd_scholar"]
        entities = [{
            "entity_id": row["scholar_id"], "canonical_id": row["scholar_id"],
            "name": row.get("name_zh") or row.get("name_en") or "",
            "organization": row.get("scholar_org_name_zh") or row.get("scholar_org_name_en") or "",
            "title": row.get("work_experience_position_zh") or "",
        } for row in rows]
        if not context["apply"]:
            return {"dry_run": True, "collection": collection, "entity_count": len(entities)}
        settings = replace(self.settings, milvus_collection=collection)
        repository = MilvusEntityRepository(settings)
        try:
            inserted = 0
            for offset in range(0, len(entities), 500):
                inserted += repository.upsert_entities(entities[offset:offset + 500])
            count = repository.count()
        finally:
            repository.close()
        return {"dry_run": False, "collection": collection,
                "upsert_count": inserted, "entity_count": count}

    def _reconcile(self, context: dict[str, Any]) -> dict[str, Any]:
        plan = context["outputs"]["build_gold_plan"]
        if not context["apply"]:
            return {"dry_run": True, "passed": True,
                    "expected_nodes": plan["expected_nodes"],
                    "expected_relationships": plan["expected_relationships"]}
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(self.settings.neo4j_uri,
                                      auth=(self.settings.neo4j_user, self.settings.neo4j_password))
        try:
            with driver.session(database=self.settings.neo4j_database) as session:
                nodes = session.run("MATCH (n {release_id:$release_id}) RETURN count(n) AS n",
                                    release_id=context["release_id"]).single()["n"]
                relationships = session.run(
                    "MATCH ()-[r {release_id:$release_id}]->() RETURN count(r) AS n",
                    release_id=context["release_id"]).single()["n"]
        finally:
            driver.close()
        milvus_count = context["outputs"]["publish_milvus"]["entity_count"]
        passed = (nodes == plan["expected_nodes"] and
                  relationships == plan["expected_relationships"] and
                  milvus_count == len(read_layer(context["release_dir"] / "silver")["dwd_scholar"]))
        result = {"passed": passed, "neo4j_nodes": nodes,
                  "neo4j_relationships": relationships, "milvus_entities": milvus_count,
                  "expected_nodes": plan["expected_nodes"],
                  "expected_relationships": plan["expected_relationships"]}
        if not passed:
            raise ReconciliationError(json.dumps(result, ensure_ascii=False))
        return result

    def _activate(self, context: dict[str, Any]) -> dict[str, Any]:
        if not context["apply"]:
            return {"activated": False, "dry_run": True}
        quality_path = Path(context["outputs"]["quality_gate"]["uri"])
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        milvus_collection = context["outputs"]["publish_milvus"]["collection"]
        self.registry.register_release(
            context["release_id"], context["run_id"], str(context["release_dir"]),
            self.settings.neo4j_database, milvus_collection, quality,
        )
        watermarks = context["outputs"]["extract_bronze"]["watermarks"]
        self.registry.activate(context["release_id"], self.settings.mysql_database, watermarks)
        return {"activated": True, "release_id": context["release_id"],
                "milvus_collection": milvus_collection,
                "watermark_count": len(watermarks)}
