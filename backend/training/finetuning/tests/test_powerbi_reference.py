from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from insightos_training.powerbi_reference import (
    _safe_output_cleanup,
    candidate_collections,
    extract_pdf_inventory,
    inspect_pbix,
    project_pairings,
    publish_extraction,
    registry_compatibility,
    source_inventory,
)


def write_layout_pbix(path: Path, page_title: str = "Overview") -> None:
    layout = {
        "sections": [
            {
                "name": "page1",
                "displayName": page_title,
                "ordinal": 0,
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {
                        "x": 10,
                        "y": 20,
                        "z": 100,
                        "width": 300,
                        "height": 200,
                        "config": json.dumps(
                            {
                                "name": "visual1",
                                "singleVisual": {
                                    "visualType": "cardVisual",
                                    "projections": {"Data": [{"queryRef": "Measure.Total Sales"}]},
                                },
                            }
                        ),
                        "filters": [],
                    }
                ],
            }
        ]
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Version", b"1")
        archive.writestr("Report/Layout", json.dumps(layout).encode("utf-16"))
        archive.writestr("Report/StaticResources/test.txt", b"not extracted source content")


def test_inventory_hashes_duplicates_and_document_types(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = source / "same.pbix"
    second = source / "copy.pbix"
    first.write_bytes(b"payload")
    second.write_bytes(b"payload")
    pdf = source / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    inventory = source_inventory(source)
    assert inventory["source_count"] == 3
    assert len(inventory["duplicate_groups"]) == 1
    assert inventory["duplicate_groups"][0]["filenames"] == ["copy.pbix", "same.pbix"]
    pdf_record = next(record for record in inventory["records"] if record["original_filename"] == "report.pdf")
    assert pdf_record["document_type"] == "pdf_report_export"


def test_inventory_rejects_source_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    source = tmp_path / "source"
    source.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        source_inventory(source)


def test_inventory_records_nested_symlink_without_following(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.pbix"
    write_layout_pbix(outside)
    (source / "linked.pbix").symlink_to(outside)
    inventory = source_inventory(source)
    record = inventory["records"][0]
    assert record["intake_status"] == "rejected_symlink"
    assert record["sha256"] is None


def test_pbix_layout_is_decoded_without_copying_archive(tmp_path):
    path = tmp_path / "Demo.pbix"
    write_layout_pbix(path)
    result = inspect_pbix(path)
    assert result["layout_decoded"] is True
    assert len(result["report_pages"]) == 1
    assert result["report_pages"][0]["page_title"] == "Overview"
    assert len(result["visuals"]) == 1
    visual = result["visuals"][0]
    assert visual["visual_type"] == "cardVisual"
    assert visual["data_fields"] == ["Measure.Total Sales"]
    assert visual["source_pbix_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert visual["review_status"] == "pending_review"


def test_pbix_path_traversal_is_rejected(tmp_path):
    path = tmp_path / "unsafe.pbix"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape.txt", b"unsafe")
    with pytest.raises(ValueError, match="path traversal"):
        inspect_pbix(path)


def test_pbix_archive_symlink_member_is_rejected(tmp_path):
    path = tmp_path / "unsafe.pbix"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("Report/Layout")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"not a layout")
    with pytest.raises(ValueError, match="symlink"):
        inspect_pbix(path)


def test_project_pairing_normalized_and_unresolved(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    pbix = source / "Customer Churn & Retention Analysis Dashboard (2).pbix"
    pbix.write_bytes(b"pbix")
    pdf = source / "Customer Churn & Retention Analysis Dashboard.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    inventory = source_inventory(source)
    result = project_pairings(inventory)
    paired = next(pair for pair in result["pairs"] if pair["pbix_filename"])
    assert paired["project_id"] == "customer_churn"
    assert paired["pairing_status"] == "candidate"
    assert paired["confidence"] >= 0.9


def test_pdf_inventory_is_conservative_and_page_counted(tmp_path):
    pdf = tmp_path / "Demo.pdf"
    pdf.write_bytes(b"%PDF-1.4\n/Type /Page\n/Type /Page\nstream\nHello dashboard sales revenue\nendstream\n")
    result = extract_pdf_inventory(pdf)
    assert result["page_count"] == 2
    assert len(result["pages"]) == 2
    assert all(page["review_status"] == "pending_review" for page in result["pages"])


def test_registry_compatibility_and_extension_review(tmp_path):
    path = tmp_path / "Demo.pbix"
    write_layout_pbix(path)
    inspection = inspect_pbix(path)
    compatibility = registry_compatibility(inspection["visuals"])
    assert compatibility["visuals"][0]["classification"] == "directly_supported"
    unknown_visual = dict(inspection["visuals"][0], visual_type="waterfallChart", visual_id="waterfall")
    compatibility = registry_compatibility([unknown_visual])
    assert compatibility["visuals"][0]["classification"] == "requires_registry_extension_review"
    assert compatibility["proposed_extensions"][0]["power_bi_source_visual_identifier"] == "waterfallChart"


def test_candidates_are_pending_and_provenance_preserving(tmp_path):
    path = tmp_path / "Demo.pbix"
    write_layout_pbix(path)
    inspection = inspect_pbix(path)
    inventory = {
        "records": [
            {
                "original_filename": path.name,
                "sha256": inspection["source_pbix_hash"],
            }
        ]
    }
    collections = candidate_collections([inspection], inventory)
    assert collections["chart_selection.jsonl"]
    assert all(item["review_status"] == "pending_review" for item in collections["chart_selection.jsonl"])
    assert all(item["approval_status"] == "not_approved" for item in collections["chart_selection.jsonl"])
    selection = collections["chart_selection.jsonl"][0]
    assert selection["source_pbix_hash"] == inspection["source_pbix_hash"]
    assert collections["business_insights.jsonl"] == []


def test_atomic_publication_and_pending_manifest(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    pbix = source / "Demo.pbix"
    write_layout_pbix(pbix)
    output = tmp_path / "output"
    result = publish_extraction(source, output)
    destination = Path(result["output_root"])
    assert destination.is_dir()
    manifest = json.loads((destination / "manifests" / "extraction_manifest.json").read_text(encoding="utf-8"))
    assert manifest["visual_count"] >= 1
    assert manifest["all_candidates_pending_review"] is True
    assert manifest["no_candidates_approved"] is True
    assert result["source_checksums_reverified"] is True


def test_existing_bundle_is_preserved_on_publication_failure(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    write_layout_pbix(source / "Demo.pbix")
    existing = tmp_path / "output" / "existing"
    existing.mkdir(parents=True)
    marker = existing / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")

    def fail_before_rename(source_path, destination):
        raise OSError("simulated publication failure")

    monkeypatch.setattr("insightos_training.powerbi_reference.os.rename", fail_before_rename)
    with pytest.raises(OSError, match="simulated publication failure"):
        publish_extraction(source, existing.parent)
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert not any(item.name.startswith(".powerbi-reference-") for item in existing.parent.iterdir())


def test_cleanup_removes_only_operation_staging(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    staging = output / ".powerbi-reference-test"
    staging.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    _safe_output_cleanup(staging, output)
    assert not staging.exists()
    assert marker.read_text(encoding="utf-8") == "keep"
