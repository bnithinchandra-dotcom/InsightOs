from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from insightos_training.reference_quality.audit import (
    audit_all_sources,
    latest_extraction_root,
    load_external_zip_references,
    load_json_references,
    reconcile_extraction_manifest,
    safe_cleanup,
    training_readiness_gate,
)


def write_manifest(extraction_root: Path, visual_count: int = 632) -> Path:
    (extraction_root / "manifests").mkdir(parents=True)
    (extraction_root / "candidates").mkdir()
    (extraction_root / "projects").mkdir()
    (extraction_root / "quality").mkdir()
    manifest = {
        "visual_count": visual_count,
        "all_candidates_pending_review": True,
        "no_candidates_approved": True,
        "candidate_counts": {
            "chart_selection.jsonl": visual_count,
            "chart_configuration.jsonl": visual_count,
            "dashboard_layout.jsonl": 42,
        },
    }
    (extraction_root / "manifests" / "extraction_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return extraction_root


def write_extraction(root: Path, visual_count: int = 632) -> None:
    write_manifest(root, visual_count)
    visual = {
        "project_id": "demo",
        "report_page_id": "page1",
        "visual_id": "visual1",
        "visual_type": "lineChart",
        "source_pbix_hash": "a" * 64,
    }
    inspection = {
        "source_pbix_hash": "a" * 64,
        "report_pages": [],
        "visuals": [visual] * visual_count,
    }
    (root / "projects" / "pbix_inspections.json").write_text(json.dumps([inspection]), encoding="utf-8")
    with (root / "candidates" / "chart_selection.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(visual_count):
            handle.write(
                json.dumps(
                    {
                        "candidate_id": str(index),
                        "source_pbix_hash": "a" * 64,
                        "report_page_id": "page1",
                        "data_field_references": ["Sales"],
                        "review_status": "pending_review",
                        "approval_status": "not_approved",
                    }
                )
                + "\n"
            )
    with (root / "candidates" / "chart_configuration.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(visual_count):
            handle.write(
                json.dumps(
                    {
                        "candidate_id": str(index),
                        "source_pbix_hash": "a" * 64,
                        "report_page_id": "page1",
                        "visual_id": "visual1",
                        "review_status": "pending_review",
                        "approval_status": "not_approved",
                    }
                )
                + "\n"
            )
    (root / "candidates" / "dashboard_layout.jsonl").write_text("", encoding="utf-8")
    (root / "quality" / "duplicate_report.json").write_text(json.dumps({"duplicate_groups": []}), encoding="utf-8")


def write_json_source(path: Path, count: int = 50) -> None:
    payload = {
        "dataset_name": "test",
        "version": 1,
        "total_examples": count,
        "examples": [{"id": f"json-{index}", "business_question": f"Question {index}"} for index in range(count)],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_json_reference_counts_and_checksum(tmp_path):
    path = tmp_path / "references.json"
    write_json_source(path)
    result = load_json_references(path)
    assert result["actual_count"] == 50
    assert result["declared_count"] == 50
    assert result["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_zip_records_and_manifest(tmp_path):
    path = tmp_path / "references.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "viz_training_data/manifest.json",
            json.dumps({"total_examples": 33, "dataset_name": "test"}),
        )
        for index in range(34):
            archive.writestr(
                f"viz_training_data/example_{index}.json",
                json.dumps({"id": f"zip-{index}", "business_question": "Q"}),
            )
    result = load_external_zip_references(path)
    assert result["declared_count"] == 33
    assert result["actual_count"] == 34
    assert len(result["records"]) == 34


def test_zip_traversal_is_rejected(tmp_path):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape.json", "{}")
    with pytest.raises(ValueError, match="traversal"):
        load_external_zip_references(path)


def test_zip_symlink_is_rejected(tmp_path):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("manifest.json")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "{}")
    with pytest.raises(ValueError, match="symlink"):
        load_external_zip_references(path)


def test_manifest_reconciles_candidate_counts(tmp_path):
    extraction = tmp_path / "extraction"
    write_extraction(extraction, 632)
    result = reconcile_extraction_manifest(extraction)
    assert result["visual_object_count"] == 632
    assert result["selection_candidate_count"] == 632
    assert result["configuration_candidate_count"] == 632
    assert result["candidate_review_states"]["pending_review"] == 1264


def test_manifest_rejects_visual_object_count_mismatch(tmp_path):
    extraction = tmp_path / "extraction"
    write_extraction(extraction, 632)
    result = reconcile_extraction_manifest(extraction)
    result["manifest"]["visual_count"] = 631
    extraction_visits = result["visual_object_count"]
    assert extraction_visits != result["manifest"]["visual_count"]


def test_readiness_gate_is_fail_closed_without_approval():
    reconciliation = {
        "manifest": {
            "all_candidates_pending_review": True,
            "no_candidates_approved": True,
            "visual_count": 632,
            "candidate_counts": {
                "chart_selection.jsonl": 609,
                "chart_configuration.jsonl": 609,
            },
        },
        "visual_object_count": 632,
        "selection_candidate_count": 609,
        "configuration_candidate_count": 609,
    }
    references = {
        "json_source": {"actual_count": 50, "declared_count": 50},
        "zip_source": {"actual_count": 34, "declared_count": 33},
    }
    gate = training_readiness_gate(reconciliation, references)
    assert gate["gate_status"] == "TRAINING_BLOCKED_PENDING_DATA_APPROVAL"
    assert gate["training_executed"] is False
    assert gate["approved_training_count"] == 0


def test_readiness_gate_does_not_autoapprove(tmp_path):
    source = tmp_path / "source"
    extraction_parent = tmp_path / "extractions"
    extraction = extraction_parent / "extraction"
    source.mkdir()
    write_json_source(source / "visualization_training_dataset.json")
    with zipfile.ZipFile(source / "viz_training_data.zip", "w") as archive:
        archive.writestr(
            "viz_training_data/manifest.json",
            json.dumps({"total_examples": 33}),
        )
        for index in range(34):
            archive.writestr(
                f"viz_training_data/example_{index}.json",
                json.dumps({"id": f"zip-{index}", "business_question": f"Q {index}"}),
            )
    write_extraction(extraction, 632)
    payload = audit_all_sources(source, extraction_parent, output_root=None)
    assert payload["training_readiness_gate"]["gate_status"] == "TRAINING_BLOCKED_PENDING_DATA_APPROVAL"
    assert payload["no_candidates_approved"] is True
    assert payload["model_training_executed"] is False


def test_latest_extraction_prefers_most_recent_directory(tmp_path):
    root = tmp_path / "extractions"
    older = root / "older"
    newer = root / "newer"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    newer.touch()
    (older / "marker").write_text("older", encoding="utf-8")
    (newer / "marker").write_text("newer", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    assert latest_extraction_root(root) == newer


def test_safe_cleanup_only_removes_owned_staging(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    staging = output / ".reference-quality-test"
    staging.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    safe_cleanup(staging, output)
    safe_cleanup(outside, output)
    assert not staging.exists()
    assert outside.exists()
