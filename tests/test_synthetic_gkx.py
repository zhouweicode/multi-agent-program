import json

import pytest

from data.synthetic_gkx import SyntheticConfig, generate_dataset, read_dataset, write_dataset
from data.synthetic_validation import validate_dataset
from scripts.import_synthetic_gkx import validate_target


SMALL_CONFIG = SyntheticConfig(
    seed=42, scholar_count=40, organization_count=5, enterprise_count=8,
    paper_count=60, project_count=15, patent_count=20,
    industry_segment_count=6, industry_event_count=10,
)


def test_synthetic_dataset_is_deterministic_and_valid():
    first = generate_dataset(SMALL_CONFIG)
    second = generate_dataset(SMALL_CONFIG)
    assert first == second
    result = validate_dataset(first)
    assert result["valid"] is True, result["errors"]
    assert result["metrics"]["scholar_count"] == 40
    assert result["metrics"]["authorship_count"] >= 60


def test_write_and_read_dataset_round_trip(tmp_path):
    manifest = write_dataset(tmp_path, SMALL_CONFIG)
    loaded = read_dataset(tmp_path)
    assert manifest["synthetic"] is True
    assert manifest["tables"]["dwd_scholar"]["rows"] == 40
    assert len(loaded["dwd_scholar_papers"]) == 60
    assert json.loads((tmp_path / "manifest.json").read_text())["config"]["seed"] == 42


def test_import_target_guard_rejects_source_database():
    validate_target("gkx_synthetic")
    validate_target("gkx_synthetic_ci")
    for database in ("gkx", "production", "gkx-synthetic", "gkx_synthetic;DROP DATABASE gkx"):
        with pytest.raises(ValueError):
            validate_target(database)
