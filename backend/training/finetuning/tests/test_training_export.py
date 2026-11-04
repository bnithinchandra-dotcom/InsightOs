from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from insightos_training.training_export.pipeline import (
    BUNDLE_NAME,
    build_training_bundle,
    normalize_question,
    overlap_report,
    source_inventory,
    verify_training_bundle,
)


def write_source_set(source_root: Path) -> None:
    source_root.mkdir()
    (source_root / "visualization_training_dataset.json").write_text(
        json.dumps(
            {
                "total_examples": 2,
                "examples": [
                    {
                        "id": "a",
                        "category": "chart_selection",
                        "business_question": "Question one?",
                        "available_fields": [{"name": "sales", "type": "number"}],
                        "expected_visualization": {
                            "visualization_id": "ranked_bar",
                            "data_mappings": {"category": "product", "measure": "sales"},
                        },
                        "rationale": "Ranking by sales.",
                    },
                    {
                        "id": "b",
                        "category": "negative",
                        "business_question": "",
                        "available_fields": [],
                        "expected_visualization": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    with zipfile.ZipFile(source_root / "viz_training_data.zip", "w") as archive:
        archive.writestr("viz_training_data/manifest.json", json.dumps({"total_examples": 1}))
        archive.writestr(
            "viz_training_data/chart_selection/a.json",
            json.dumps(
                {
                    "id": "a",
                    "category": "chart_selection",
                    "business_question": "Question one?",
                    "available_fields": [{"name": "sales", "type": "number"}],
                    "expected_visualization": {
                        "visualization_id": "ranked_bar",
                        "data_mappings": {"category": "product", "measure": "sales"},
                    },
                }
            ),
        )


def write_extraction(extraction_root: Path) -> None:
    extraction = extraction_root / "extraction-test"
    for directory in ("manifests", "candidates", "projects", "inventory", "quality"):
        (extraction / directory).mkdir(parents=True, exist_ok=True)
    (extraction / "manifests" / "extraction_manifest.json").write_text(
        json.dumps(
            {
                "visual_count": 0,
                "candidate_counts": {
                    "chart_selection.jsonl": 0,
                    "chart_configuration.jsonl": 0,
                    "dashboard_layout.jsonl": 0,
                },
                "all_candidates_pending_review": True,
                "no_candidates_approved": True,
            }
        ),
        encoding="utf-8",
    )
    (extraction / "candidates" / "chart_selection.jsonl").write_text("", encoding="utf-8")
    (extraction / "candidates" / "chart_configuration.jsonl").write_text("", encoding="utf-8")
    (extraction / "candidates" / "dashboard_layout.jsonl").write_text("", encoding="utf-8")
    (extraction / "inventory" / "source_inventory.json").write_text(
        json.dumps({"records": []}),
        encoding="utf-8",
    )
    (extraction / "inventory" / "source_checksums.json").write_text(
        json.dumps({"records": []}),
        encoding="utf-8",
    )


def test_normalize_question_rejects_empty_questions():
    assert normalize_question("") == ""
    assert normalize_question("  Revenue by Region? ") == "revenue by region"
    assert normalize_question(None) == ""


def test_overlap_excludes_empty_normalized_questions():
    result = overlap_report(
        [{"id": "j1", "business_question": ""}, {"id": "j2", "business_question": "Revenue by region?"}],
        [{"id": "z1", "business_question": None}, {"id": "z2", "business_question": "Revenue by region?"}],
    )
    assert result["empty_question_json_count"] == 1
    assert result["empty_question_zip_count"] == 1
    assert result["distinct_normalized_question_count"] == 1
    assert result["groups"][0]["json_record_ids"] == ["j2"]
    assert result["groups"][0]["zip_record_ids"] == ["z2"]


def test_source_inventory_rejects_checksum_change(tmp_path):
    source = tmp_path / "source"
    extraction_parent = tmp_path / "extractions"
    source.mkdir()
    extraction = extraction_parent / "extraction"
    extraction.mkdir(parents=True)
    (extraction / "inventory").mkdir()
    (source / "one.json").write_text("{}", encoding="utf-8")
    (extraction / "inventory" / "source_inventory.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "document_type": "json_training_reference",
                        "project_id": "test",
                        "original_filename": "one.json",
                        "original_path": str(source / "one.json"),
                        "sha256": "0" * 64,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (extraction / "inventory" / "source_checksums.json").write_text(
        json.dumps({"records": [{"filename": "one.json", "sha256": "0" * 64}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        source_inventory(source, extraction_parent)


def test_build_is_fail_closed_and_verify_passes(tmp_path):
    source = tmp_path / "source"
    extraction_parent = tmp_path / "extractions"
    destination = tmp_path / "destination"
    write_source_set(source)
    write_extraction(extraction_parent)
    result = build_training_bundle(source, extraction_parent, destination)
    bundle = destination / BUNDLE_NAME
    assert bundle.is_file()
    assert result["readiness_status"] == "TRAINING_BLOCKED_PENDING_DATA_APPROVAL"
    verification = verify_training_bundle(bundle)
    assert verification["valid"] is True, verification["errors"]
    with zipfile.ZipFile(bundle) as archive:
        assert "insightos_qwen3_0_6b_training_bundle/training/train.jsonl" in archive.namelist()


def test_hardened_export_separates_categories_and_review_registers(tmp_path):
    source = tmp_path / "source"
    extraction_parent = tmp_path / "extractions"
    destination = tmp_path / "destination"
    write_source_set(source)
    write_extraction(extraction_parent)
    build_training_bundle(source, extraction_parent, destination)
    bundle = destination / BUNDLE_NAME
    root = "insightos_qwen3_0_6b_training_bundle"

    with zipfile.ZipFile(bundle) as archive:
        negative_text = archive.read(f"{root}/candidates/negative_examples.jsonl").decode("utf-8")
        complete_text = archive.read(f"{root}/candidates/complete_configuration.jsonl").decode("utf-8")
        pending = archive.read(f"{root}/review/pending_review.csv").decode("utf-8")
        excluded = archive.read(f"{root}/review/excluded_records.csv").decode("utf-8")
        train = archive.read(f"{root}/training/train.jsonl").decode("utf-8")
        canonical_schema = json.loads(archive.read(f"{root}/schemas/canonical_record.schema.json"))

        assert negative_text.strip()
        assert complete_text.strip() == ""
        assert "Candidate ID" in pending
        assert "Candidate ID" in excluded
        assert train.strip() == ""

    pending_lines = [line for line in pending.splitlines() if line and not line.startswith("Candidate ID")]
    excluded_lines = [line for line in excluded.splitlines() if line and not line.startswith("Candidate ID")]
    assert pending_lines
    assert excluded_lines

    invalid_candidate = {
        "record_id": "",
        "source_filename": "",
        "source_sha256": "not-a-sha256",
        "source_record_id": None,
        "task_category": "unknown",
        "review_status": "unknown",
        "approval_status": "unknown",
        "evidence_references": [],
        "content_hash": "not-a-hash",
    }
    errors = list(Draft202012Validator(canonical_schema).iter_errors(invalid_candidate))
    assert errors


def test_adjacent_checksum_and_loader_are_included(tmp_path):
    source = tmp_path / "source"
    extraction_parent = tmp_path / "extractions"
    destination = tmp_path / "destination"
    write_source_set(source)
    write_extraction(extraction_parent)
    result = build_training_bundle(source, extraction_parent, destination)
    checksum = destination / f"{BUNDLE_NAME}.sha256"
    assert checksum.is_file()
    assert result["bundle_sha256"] in checksum.read_text(encoding="ascii")

    with zipfile.ZipFile(destination / BUNDLE_NAME) as archive:
        loader = archive.read("insightos_qwen3_0_6b_training_bundle/kaggle/load_dataset.py").decode("utf-8")
        assert "def load_bundle" in loader
        assert "sha256" in loader.lower()
        assert "readiness" in loader.lower()
