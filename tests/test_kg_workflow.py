from data.synthetic_gkx import SyntheticConfig, write_dataset
from data.synthetic_validation import validate_dataset
from kg_workflow.artifacts import mutation_plan, normalize_layer, read_layer
from kg_workflow.registry import KGWorkflowRegistry
from models.settings import Settings
from services.resources import active_release_settings
import pytest
from kg_workflow.pipeline import KGWorkflow


def test_registry_step_resume_and_atomic_activation(tmp_path):
    registry = KGWorkflowRegistry(tmp_path / "registry.sqlite")
    registry.create_run("run-1", "release-1", "SNAPSHOT", "gkx_synthetic", {"apply": True})
    assert registry.start_step("run-1", "extract") == 1
    registry.fail_step("run-1", "extract", {"type": "TransientError"})
    assert registry.start_step("run-1", "extract") == 2
    registry.complete_step("run-1", "extract", {"rows": 10})
    registry.register_release("release-1", "run-1", "/tmp/release-1", "neo4j", "entities_r1", {"passed": True})
    registry.activate("release-1", "gkx_synthetic", {"dwd_scholar": {"updated_at": "2026-01-01"}})
    assert registry.step("run-1", "extract")["attempt"] == 2
    assert registry.active_release()["release_id"] == "release-1"
    registry.close()


def test_bronze_to_silver_and_gold_plan(tmp_path):
    config = SyntheticConfig(seed=7, scholar_count=40, organization_count=5,
                             enterprise_count=8, paper_count=60, project_count=15,
                             patent_count=20, industry_segment_count=6,
                             industry_event_count=10)
    bronze = tmp_path / "bronze"
    write_dataset(bronze, config)
    manifest = normalize_layer(bronze, tmp_path / "silver")
    tables = read_layer(tmp_path / "silver")
    quality = validate_dataset(tables)
    plan = mutation_plan(tables, "release-test")
    assert quality["valid"] is True
    assert manifest["normalizer_version"] == "synthetic-normalizer@1"
    assert plan["expected_nodes"] > 0
    assert plan["expected_relationships"] > 0


def test_active_release_resolves_serving_collection(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry = KGWorkflowRegistry(path)
    registry.create_run("run-2", "release-2", "SNAPSHOT", "gkx_synthetic", {"apply": True})
    registry.register_release("release-2", "run-2", "/tmp/release-2", "neo4j", "entities_release_2", {"passed": True})
    registry.activate("release-2", "gkx_synthetic", {})
    registry.close()
    settings, active = active_release_settings(Settings(kg_workflow_registry_path=str(path)))
    assert active["release_id"] == "release-2"
    assert settings.milvus_collection == "entities_release_2"


def test_incremental_mode_is_not_silently_treated_as_snapshot(tmp_path):
    registry = KGWorkflowRegistry(tmp_path / "registry.sqlite")
    workflow = KGWorkflow(settings=Settings(), registry=registry, artifact_root=tmp_path)
    with pytest.raises(NotImplementedError):
        workflow.start(release_id="release-inc", run_type="INCREMENTAL")
    registry.close()


def test_inbox_deduplicates_stable_change_event(tmp_path):
    registry = KGWorkflowRegistry(tmp_path / "registry.sqlite")
    registry.create_run("run-inc", "release-inc", "INCREMENTAL", "gkx_synthetic", {"apply": True})
    event = {"event_id": "evt-1", "dataset": "dwd_scholar", "record_id": "SCH1",
             "operation": "UPSERT", "payload_hash": "abc"}
    assert registry.record_events("run-inc", [event]) == (1, 0)
    assert registry.record_events("run-inc", [event]) == (0, 1)
    registry.close()
