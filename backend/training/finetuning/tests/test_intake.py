from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from insightos_training.intake import (
    _safe_cleanup_staging,
    create_intake_bundle,
    generate_candidate_profiles,
    inspect_source,
    profile_dataset,
    read_source,
    validate_intake_bundle,
)


def write_csv(tmp_path: Path, name: str = "sales.csv") -> Path:
    path = tmp_path / name
    path.write_text(
        "month,revenue,region,category\n2024-01,10,North,A\n2024-02,20,South,B\n2024-03,15,North,A\n",
        encoding="utf-8",
    )
    return path


def test_inspect_csv_preview(tmp_path):
    source = write_csv(tmp_path)
    result = inspect_source(source)
    assert result["preview"] is True
    assert result["persisted"] is False
    assert result["row_count"] == 3
    assert result["column_count"] == 4


def test_read_json_object_and_jsonl(tmp_path):
    json_source = tmp_path / "data.json"
    json_source.write_text(json.dumps({"month": "2024-01", "value": 10}), encoding="utf-8")
    _, json_rows, _ = read_source(json_source)
    assert json_rows == [{"month": "2024-01", "value": 10}]

    jsonl_source = tmp_path / "data.jsonl"
    jsonl_source.write_text('{"value": 10}\n{"value": 20}\n', encoding="utf-8")
    _, jsonl_rows, _ = read_source(jsonl_source)
    assert len(jsonl_rows) == 2


def test_reject_unsupported_and_malformed_json(tmp_path):
    xlsx = tmp_path / "data.xlsx"
    xlsx.write_bytes(b"not actually xlsx")
    with pytest.raises(ValueError, match="Unsupported input format"):
        read_source(xlsx)

    malformed = tmp_path / "bad.json"
    malformed.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed JSON"):
        read_source(malformed)


def test_inconsistent_csv_rows_fail_closed(tmp_path):
    source = tmp_path / "bad.csv"
    source.write_text("a,b\n1\n2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed CSV"):
        read_source(source)


def test_duplicate_csv_headers_are_reported(tmp_path):
    source = tmp_path / "duplicate.csv"
    source.write_text("name,name,value\nA,B,1\n", encoding="utf-8")
    headers, rows, parser = read_source(source)
    assert headers == ["name", "name", "value"]
    assert parser["duplicate_columns"] == ["name"]
    assert len(rows[0]) == 3


def test_profile_infers_types_and_quality(tmp_path):
    source = write_csv(tmp_path)
    manifest, profile, quality, privacy, _ = profile_dataset(source)
    assert profile["row_count"] == 3
    assert profile["source_checksum"] == manifest["sha256"]
    assert any(column["original_field_name"] == "revenue" for column in profile["columns"])
    assert privacy["status"] == "no_suspicious_fields_detected"
    assert quality["duplicate_records"] == 0


def test_privacy_detection_redacts_in_profile_values(tmp_path):
    source = tmp_path / "customers.csv"
    source.write_text("customer_name,email,revenue\nAlice,a@example.com,42\n", encoding="utf-8")
    _, profile, _, privacy, _ = profile_dataset(source)
    suspicious = {item["field"] for item in privacy["suspicious_columns"]}
    assert {"customer_name", "email"} <= suspicious
    for column in profile["columns"]:
        if column["original_field_name"] in suspicious:
            assert column["example_values"] == ["[REDACTED]"]


def test_candidates_reference_existing_fields_and_registry(tmp_path):
    source = write_csv(tmp_path)
    manifest, profile, _, privacy, _ = profile_dataset(source)
    candidates, coverage = generate_candidate_profiles(manifest, profile, privacy)
    assert candidates
    assert coverage["annotation_status"] == "pending_review"
    assert all(candidate["review_status"] == "pending_review" for candidate in candidates)
    known_fields = {column["original_field_name"] for column in profile["columns"]}
    for candidate in candidates:
        for reference in candidate["available_field_references"]:
            assert reference["name"] in known_fields


def test_sensitive_columns_are_not_used_for_candidates(tmp_path):
    source = tmp_path / "private.csv"
    source.write_text("customer_id,metric,date\n1,10,2024-01-01\n2,20,2024-01-02\n", encoding="utf-8")
    manifest, profile, _, privacy, _ = profile_dataset(source)
    candidates, _ = generate_candidate_profiles(manifest, profile, privacy)
    assert all(
        reference["name"] != "customer_id"
        for candidate in candidates
        for reference in candidate["available_field_references"]
    )


def test_atomic_bundle_is_complete_and_valid(tmp_path):
    source = write_csv(tmp_path)
    output = tmp_path / "bundles"
    result = create_intake_bundle(source, output)
    assert result["valid"] is True
    assert result["candidate_count"] >= 1
    bundle = Path(result["bundle"])
    report = json.loads((bundle / "intake_report.json").read_text(encoding="utf-8"))
    assert report["governance_approval_status"] == "not_requested"
    assert report["training_export_status"] == "not_permitted"
    assert validate_intake_bundle(bundle)["valid"] is True


def test_duplicate_source_bundle_is_rejected(tmp_path):
    source = write_csv(tmp_path)
    output = tmp_path / "bundles"
    create_intake_bundle(source, output)
    with pytest.raises(ValueError, match="Duplicate intake prevented"):
        create_intake_bundle(source, output)


def test_source_symlink_is_rejected(tmp_path):
    source = write_csv(tmp_path, "real.csv")
    link = tmp_path / "linked.csv"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symbolic link"):
        inspect_source(link)


def test_staging_cleanup_removes_only_operation_directory(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    staging = Path(output / ".visualization-intake-test")
    staging.mkdir()
    marker = staging / "marker.txt"
    marker.write_text("temporary", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve", encoding="utf-8")
    _safe_cleanup_staging(staging, output)
    assert not staging.exists()
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_cleanup_rejects_path_outside_staging_root(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    _safe_cleanup_staging(outside, output)
    assert (outside / "keep.txt").exists()


def test_failed_write_cleans_staging(tmp_path, monkeypatch):
    source = write_csv(tmp_path)
    output = tmp_path / "output"
    real_write = Path.write_text
    staging_seen = []

    def failing_write(path: Path, *args, **kwargs):
        if path.parent.name.startswith(".visualization-intake-"):
            staging_seen.append(path.parent)
            raise OSError("simulated write failure")
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)
    with pytest.raises(OSError, match="simulated write failure"):
        create_intake_bundle(source, output)
    assert staging_seen
    assert not staging_seen[0].exists()
    assert not [item for item in output.iterdir() if item.name.startswith(".visualization-intake-")]


def test_cli_inspect_does_not_persist(tmp_path):
    source = write_csv(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "insightos_training.intake", "inspect", "--source", str(source)],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["persisted"] is False
