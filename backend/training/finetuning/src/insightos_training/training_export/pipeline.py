from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

EXPORTER_VERSION = "insightos-training-export-v1"
DEFAULT_SOURCE_ROOT = Path("/mnt/c/Users/NITHIN/Desktop/data")
DEFAULT_EXTRACTION_ROOT = Path.home() / ".cache" / "insightos" / "powerbi_reference"
DEFAULT_DESTINATION = Path.home() / ".cache" / "insightos" / "training_exports"
PACKAGE_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = PACKAGE_ROOT / "visualization" / "registry.json"
OUTPUT_SCHEMA_PATH = PACKAGE_ROOT / "visualization" / "output_schema.json"
BUNDLE_NAME = "insightos_qwen3_0_6b_training_bundle.zip"
ROOT_NAME = "insightos_qwen3_0_6b_training_bundle"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_hash(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "content_hash"}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def normalize_question(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"JSONL record must be an object: {path}")
                records.append(value)
    return records


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def latest_extraction(extraction_root: Path) -> Path:
    extraction_root = extraction_root.expanduser().resolve(strict=True)
    if not extraction_root.is_dir():
        raise ValueError(f"Power BI extraction root does not exist: {extraction_root}")
    directories = [path for path in extraction_root.iterdir() if path.is_dir() and not path.is_symlink()]
    if not directories:
        raise ValueError("No Power BI extraction directories found")
    return max(directories, key=lambda path: path.stat().st_mtime)


def load_registry() -> tuple[dict[str, Any], set[str], dict[str, str]]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    supported = {entry["id"] for entry in registry["visualizations"]}
    power_bi_mapping = {
        "card": "kpi_scorecard",
        "cardVisual": "kpi_scorecard",
        "clusteredBarChart": "grouped_bar",
        "clusteredColumnChart": "grouped_bar",
        "barChart": "ranked_bar",
        "columnChart": "ranked_bar",
        "lineChart": "line_chart",
        "areaChart": "area_chart",
        "donutChart": "donut_chart",
        "pieChart": "donut_chart",
        "scatterChart": "scatter_plot",
        "map": "map_symbol",
        "treemap": "treemap",
        "funnel": "funnel",
        "tableEx": "table",
        "pivotTable": "matrix_pivot",
    }
    return registry, supported, power_bi_mapping


def source_inventory(source_root: Path, extraction_root: Path) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve(strict=True)
    extraction = latest_extraction(extraction_root)
    inventory_path = extraction / "inventory" / "source_inventory.json"
    checksums_path = extraction / "inventory" / "source_checksums.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    records = []
    checksum_by_name = {record["filename"]: record["sha256"] for record in checksums["records"]}
    for original in inventory["records"]:
        filename = original["original_filename"]
        path = source_root / filename
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Required source is missing or is not a regular file: {filename}")
        actual = sha256_file(path)
        recorded = checksum_by_name.get(filename, original.get("sha256"))
        if recorded != actual:
            raise ValueError(f"Source checksum mismatch: {filename}")
        records.append(
            {
                "filename": filename,
                "source_path": str(path.resolve()),
                "document_type": original["document_type"],
                "project_id": original["project_id"],
                "sha256": actual,
                "file_size": path.stat().st_size,
            }
        )
    return {
        "exporter_version": EXPORTER_VERSION,
        "source_root": str(source_root),
        "extraction_root": str(extraction),
        "source_count": len(records),
        "records": sorted(records, key=lambda record: (record["filename"], record["sha256"])),
    }


def ingest_json_source(source_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = source_root / "visualization_training_dataset.json"
    if not path.is_file() or path.is_symlink():
        raise ValueError("JSON visualization source is missing or is not a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    examples = payload.get("examples")
    if not isinstance(examples, list):
        raise ValueError("JSON visualization source must contain an examples array")
    source_meta = {
        "filename": path.name,
        "sha256": sha256_file(path),
        "declared_count": payload.get("total_examples"),
        "actual_count": len(examples),
    }
    return examples, source_meta


def ingest_zip_source(source_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = source_root / "viz_training_data.zip"
    if not path.is_file() or path.is_symlink():
        raise ValueError("ZIP visualization source is missing or is not a regular file")
    records: list[dict[str, Any]] = []
    manifest: dict[str, Any] | None = None
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 2000:
            raise ValueError("Visualization ZIP exceeds the member safety limit")
        for info in infos:
            normalized_name = info.filename.replace("\\", "/")
            if normalized_name.startswith("/") or ".." in Path(normalized_name).parts:
                raise ValueError(f"Visualization ZIP traversal rejected: {info.filename}")
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError(f"Visualization ZIP symlink rejected: {info.filename}")
            if info.is_dir() or not info.filename.lower().endswith(".json"):
                continue
            value = json.loads(archive.read(info))
            if not isinstance(value, dict):
                raise ValueError(f"ZIP JSON member must be an object: {info.filename}")
            if info.filename.endswith("manifest.json"):
                manifest = value
            else:
                value["_member_filename"] = info.filename
                records.append(value)
    source_meta = {
        "filename": path.name,
        "sha256": sha256_file(path),
        "declared_count": manifest.get("total_examples") if manifest else None,
        "actual_count": len(records),
    }
    return records, source_meta


def overlap_report(json_records: list[dict[str, Any]], zip_records: list[dict[str, Any]]) -> dict[str, Any]:
    zip_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in zip_records:
        question = normalize_question(record.get("business_question"))
        if question:
            zip_groups[question].append(record)
    groups: dict[str, dict[str, Any]] = {}
    for record in json_records:
        original = record.get("business_question")
        normalized = normalize_question(original)
        if not normalized:
            continue
        matches = zip_groups.get(normalized, [])
        if not matches:
            continue
        entry = groups.setdefault(
            normalized,
            {
                "normalized_question": normalized,
                "json_record_ids": [],
                "zip_record_ids": [],
                "original_json_questions": [],
                "original_zip_questions": [],
                "match_types": [],
            },
        )
        if record.get("id") not in entry["json_record_ids"]:
            entry["json_record_ids"].append(record.get("id"))
        if original not in entry["original_json_questions"]:
            entry["original_json_questions"].append(original)
        for match in matches:
            if match.get("id") not in entry["zip_record_ids"]:
                entry["zip_record_ids"].append(match.get("id"))
            if match.get("business_question") not in entry["original_zip_questions"]:
                entry["original_zip_questions"].append(match.get("business_question"))
            match_type = "exact" if original == match.get("business_question") else "normalized"
            if match_type not in entry["match_types"]:
                entry["match_types"].append(match_type)
    return {
        "meaningful_overlap_group_count": len(groups),
        "distinct_normalized_question_count": len(groups),
        "empty_question_json_count": sum(
            not normalize_question(record.get("business_question")) for record in json_records
        ),
        "empty_question_zip_count": sum(
            not normalize_question(record.get("business_question")) for record in zip_records
        ),
        "groups": sorted(groups.values(), key=lambda group: group["normalized_question"]),
    }


def base_canonical_record(
    *,
    candidate_id: str,
    source_filename: str,
    source_sha256: str,
    source_record_id: Any,
    source_project: str | None,
    report_page: str | None,
    visual_identity: str | None,
    extractor_version: str,
    business_question: str | None,
    available_fields: list[Any],
    field_mappings: list[Any] | None = None,
    measures: list[Any] | None = None,
    filters: list[Any] | None = None,
    visualization_id: str | None = None,
    complete_configuration: dict[str, Any] | None = None,
    evidence_references: list[Any] | None = None,
    evidence_quality: str = "secondary",
    review_status: str = "pending_review",
    exclusion_reason: str | None = None,
) -> dict[str, Any]:
    record = {
        "record_id": candidate_id,
        "source_filename": source_filename,
        "source_sha256": source_sha256,
        "source_record_id": source_record_id,
        "source_project": source_project,
        "report_page": report_page,
        "visual_identity": visual_identity,
        "extractor_version": extractor_version,
        "business_question": business_question,
        "dataset_fields": available_fields,
        "field_semantic_types": [
            {"name": field.get("name"), "type": field.get("type")}
            for field in available_fields
            if isinstance(field, dict)
        ],
        "field_to_visual_mappings": field_mappings or [],
        "measures_and_aggregations": measures or [],
        "filters_and_known_context": filters or [],
        "visualization_id": visualization_id,
        "complete_configuration": complete_configuration,
        "evidence_references": evidence_references or [],
        "evidence_quality": evidence_quality,
        "review_status": review_status,
        "approval_status": "not_approved",
        "exclusion_reason": exclusion_reason,
    }
    record["content_hash"] = content_hash(record)
    return record


def process_external_examples(
    records: list[dict[str, Any]],
    source_filename: str,
    source_sha256: str,
    source_label: str,
    supported_ids: set[str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    candidates: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    complete_configs: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    dashboard_layouts: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        record_id = record.get("id") or f"{source_label}_{index:05d}"
        category = record.get("category")
        expected = record.get("expected_visualization")
        visualization_id = expected.get("visualization_id") if isinstance(expected, dict) else None
        available_fields = record.get("available_fields")
        if not isinstance(available_fields, list):
            available_fields = []
        mappings = expected.get("data_mappings") if isinstance(expected, dict) else None
        raw_question = record.get("business_question")
        question = raw_question.strip() if isinstance(raw_question, str) and normalize_question(raw_question) else None
        member = record.get("_member_filename")
        evidence = [
            {
                "type": "source_record",
                "filename": source_filename,
                "member": member if member else None,
                "source_record_id": record_id,
            }
        ]
        candidate = base_canonical_record(
            candidate_id=f"{source_label}:{record_id}",
            source_filename=source_filename,
            source_sha256=source_sha256,
            source_record_id=record_id,
            source_project=record.get("industry"),
            report_page=None,
            visual_identity=None,
            extractor_version=EXPORTER_VERSION,
            business_question=question,
            available_fields=available_fields,
            field_mappings=[mappings] if isinstance(mappings, dict) else [],
            visualization_id=visualization_id,
            complete_configuration=None,
            evidence_references=evidence,
            evidence_quality="draft_reference",
        )
        task_category = {
            "chart_selection": "chart_selection",
            "chart_alternatives": "chart_alternatives",
            "complete_configuration": "complete_configuration",
            "negative_example": "negative_examples",
            "negative": "negative_examples",
            "dashboard_layout": "dashboard_layout",
        }.get(category, "chart_selection")
        candidate["task_category"] = task_category
        candidate["selection_rationale"] = record.get("rationale")
        candidate["analytical_objective"] = record.get("analytical_objective")
        candidate["original_source_category"] = category
        is_negative = category in {"negative", "negative_example"} or (
            source_label == "zip" and "/negatives/" in str(member)
        )
        unsupported = visualization_id is not None and visualization_id not in supported_ids
        missing_question = not normalize_question(question)
        if is_negative:
            candidate["review_status"] = "excluded"
            candidate["exclusion_reason"] = "Negative or incompatible example; excluded from positive training"
            negatives.append(candidate)
        elif category == "dashboard_layout" or source_label == "zip" and "/dashboards/" in str(member):
            candidate["task_category"] = "dashboard_layout"
            dashboard_layouts.append(candidate)
        elif category == "chart_alternatives":
            alternatives.append(candidate)
        elif category == "complete_configuration":
            complete_target = expected if isinstance(expected, dict) else None
            candidate["complete_configuration"] = complete_target
            required_target_keys = {
                "schema_version",
                "visualization_id",
                "purpose",
                "data_mappings",
                "aggregation",
                "formatting",
                "legend",
                "tooltip",
                "layout",
                "accessibility",
                "rationale",
            }
            if not complete_target or not required_target_keys.issubset(complete_target):
                candidate["task_category"] = "incomplete_review"
                candidate["review_status"] = "excluded"
                candidate["exclusion_reason"] = "Complete configuration target is missing required fields"
                excluded.append(candidate)
            else:
                complete_configs.append(candidate)
        elif unsupported:
            candidate["review_status"] = "excluded"
            candidate["exclusion_reason"] = f"Unsupported visualization identifier: {visualization_id}"
            excluded.append(candidate)
        elif missing_question:
            candidate["review_status"] = "excluded"
            candidate["exclusion_reason"] = "Missing business question"
            excluded.append(candidate)
        else:
            candidates.append(candidate)
        candidate["content_hash"] = content_hash(candidate)
    return candidates, alternatives, complete_configs, negatives, dashboard_layouts, excluded


def process_powerbi(
    extraction: Path, supported_ids: set[str], mapping: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    selections = read_jsonl(extraction / "candidates" / "chart_selection.jsonl")
    configurations = read_jsonl(extraction / "candidates" / "chart_configuration.jsonl")
    layouts = read_jsonl(extraction / "candidates" / "dashboard_layout.jsonl")
    source_inventory = json.loads((extraction / "inventory" / "source_inventory.json").read_text(encoding="utf-8"))
    filenames_by_hash: dict[str, list[str]] = defaultdict(list)
    for record in source_inventory["records"]:
        if record["document_type"] == "power_bi_project":
            filenames_by_hash[record["sha256"]].append(record["original_filename"])

    chart_candidates: list[dict[str, Any]] = []
    config_candidates: list[dict[str, Any]] = []
    layout_candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for record in selections:
        source_hash = record["source_pbix_hash"]
        visual_type = record.get("observed_visual_type") or record.get("visual_type")
        visualization_id = mapping.get(visual_type)
        fields = record.get("data_field_references")
        fields = fields if isinstance(fields, list) else []
        filenames = sorted(set(filenames_by_hash.get(source_hash, [])))
        evidence = [
            {
                "type": "pbix_layout_metadata",
                "member": "Report/Layout",
                "source_hash": source_hash,
                "source_filenames": filenames,
                "confidence": item.get("confidence"),
            }
            for item in record.get("provenance", [])
        ]
        candidate_id = f"pbix-selection:{record['candidate_id']}"
        candidate = base_canonical_record(
            candidate_id=candidate_id,
            source_filename=", ".join(filenames) if filenames else "unknown.pbix",
            source_sha256=source_hash,
            source_record_id=record["candidate_id"],
            source_project=record.get("project_id"),
            report_page=record.get("report_page_id"),
            visual_identity=f"{source_hash}:{record.get('report_page_id')}:{record.get('candidate_id')}",
            extractor_version="insightos-powerbi-reference-v1",
            business_question=record.get("business_question_draft"),
            available_fields=[],
            field_mappings=fields,
            visualization_id=visualization_id,
            complete_configuration=None,
            evidence_references=evidence,
            evidence_quality="layout_metadata",
        )
        candidate["task_category"] = "chart_selection"
        candidate["selection_rationale"] = record.get("selection_rationale")
        if not fields:
            candidate["review_status"] = "excluded"
            candidate["exclusion_reason"] = "Missing field mappings"
            excluded.append(candidate)
        elif visualization_id is None or visualization_id not in supported_ids:
            candidate["review_status"] = "excluded"
            candidate["exclusion_reason"] = f"Unsupported or non-chart visual type: {visual_type}"
            excluded.append(candidate)
        elif not candidate["business_question"]:
            candidate["review_status"] = "excluded"
            candidate["exclusion_reason"] = "Missing business question; chart purpose is not evidenced"
            excluded.append(candidate)
        else:
            chart_candidates.append(candidate)

    for record in configurations:
        mappings = record.get("mappings")
        mappings = mappings if isinstance(mappings, list) else []
        visualization_id = mapping.get(record.get("visual_type"))
        filenames = sorted(set(filenames_by_hash.get(record["source_pbix_hash"], [])))
        candidate = base_canonical_record(
            candidate_id=f"pbix-config:{record['candidate_id']}",
            source_filename=", ".join(filenames) if filenames else "unknown.pbix",
            source_sha256=record["source_pbix_hash"],
            source_record_id=record["candidate_id"],
            source_project=record.get("project_id"),
            report_page=record.get("report_page_id"),
            visual_identity=f"{record['source_pbix_hash']}:{record.get('report_page_id')}:{record.get('visual_id')}",
            extractor_version="insightos-powerbi-reference-v1",
            business_question=None,
            available_fields=[],
            field_mappings=mappings,
            measures=[],
            filters=[],
            visualization_id=visualization_id,
            complete_configuration=None,
            evidence_references=[
                {"type": "pbix_layout_metadata", "member": "Report/Layout", "source_hash": record["source_pbix_hash"]}
            ],
            evidence_quality="layout_metadata",
        )
        candidate["task_category"] = "incomplete_visualization_configuration"
        candidate["missing_information"] = [
            "business_question",
            "dataset_schema",
            "field_semantic_types",
            "measure_and_aggregation",
            "filters_and_context",
            "complete_visualization_configuration",
            "tooltip_and_formatting_details",
        ]
        candidate["review_status"] = "excluded"
        candidate["exclusion_reason"] = (
            "Incomplete Power BI visual metadata candidate; not a complete visualization configuration"
        )
        config_candidates.append(candidate)

    for record in layouts:
        filenames = sorted(set(filenames_by_hash.get(record["source_pbix_hash"], [])))
        layout_identity = canonical_json(
            {
                "source_pbix_hash": record["source_pbix_hash"],
                "project_id": record.get("project_id"),
                "report_page_id": record.get("report_page_id"),
                "candidate_id": record["candidate_id"],
                "canvas_dimensions": record.get("canvas_dimensions"),
                "visual_ids": record.get("visual_ids"),
                "visual_types": record.get("visual_types"),
            }
        )
        layout_suffix = hashlib.sha256(layout_identity).hexdigest()[:16]
        candidate = base_canonical_record(
            candidate_id=f"pbix-layout:{record['candidate_id']}:{layout_suffix}",
            source_filename=", ".join(filenames) if filenames else "unknown.pbix",
            source_sha256=record["source_pbix_hash"],
            source_record_id=record["candidate_id"],
            source_project=record.get("project_id"),
            report_page=record.get("report_page_id"),
            visual_identity=record.get("report_page_id"),
            extractor_version="insightos-powerbi-reference-v1",
            business_question=None,
            available_fields=[],
            field_mappings=[],
            visualization_id=None,
            complete_configuration={
                "canvas_dimensions": record.get("canvas_dimensions"),
                "visual_ids": record.get("visual_ids"),
                "visual_types": record.get("visual_types"),
            },
            evidence_references=[
                {"type": "pbix_layout_metadata", "member": "Report/Layout", "source_hash": record["source_pbix_hash"]}
            ],
            evidence_quality="layout_metadata",
        )
        candidate["task_category"] = "dashboard_layout"
        candidate["missing_information"] = []
        if not record.get("canvas_dimensions"):
            candidate["missing_information"].append("page_canvas_dimensions")
        if not record.get("visual_ids"):
            candidate["missing_information"].append("visual_identity")
        if not record.get("visual_types"):
            candidate["missing_information"].append("visual_types")
        if candidate["missing_information"]:
            candidate["review_status"] = "excluded"
            candidate["exclusion_reason"] = (
                "Dashboard layout evidence is incomplete; required page structure is missing"
            )
        layout_candidates.append(candidate)
    return chart_candidates, config_candidates, layout_candidates, excluded


def load_decisions(decisions_path: Path | None) -> dict[str, dict[str, Any]]:
    if decisions_path is None:
        return {}
    decisions_path = decisions_path.expanduser().resolve(strict=True)
    decisions: dict[str, dict[str, Any]] = {}
    if decisions_path.suffix.lower() == ".csv":
        with decisions_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if not row.get("Candidate ID") and not row.get("candidate_id"):
                    continue
                candidate_id = row.get("Candidate ID") or row.get("candidate_id")
                decision = row.get("Decision") or row.get("decision")
                decisions[str(candidate_id)] = {
                    "candidate_id": str(candidate_id),
                    "candidate_content_hash": row.get("Candidate Content Hash") or row.get("candidate_content_hash"),
                    "decision": str(decision).strip() if decision else "",
                    "reviewer_identifier": row.get("Reviewer Identifier") or row.get("reviewer_identifier"),
                    "review_timestamp": row.get("Review Timestamp") or row.get("review_timestamp"),
                    "reviewer_notes": row.get("Reviewer Notes") or row.get("reviewer_notes") or "",
                }
    elif decisions_path.suffix.lower() == ".jsonl":
        with decisions_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    decisions[str(row["candidate_id"])] = row
    else:
        raise ValueError("Decision imports must be CSV or JSONL")
    return decisions


def apply_decisions(
    decisions_path: Path, bundle_path: Path | None = None, persistent_path: Path | None = None
) -> dict[str, Any]:
    decisions = load_decisions(decisions_path)
    if not decisions:
        raise ValueError("Decision file contains no explicit decisions")
    required = {"candidate_id", "candidate_content_hash", "decision", "reviewer_identifier", "review_timestamp"}
    allowed = {"approved", "rejected", "excluded"}
    for candidate_id, decision in decisions.items():
        missing = sorted(name for name in required if not decision.get(name))
        if missing:
            raise ValueError(f"Decision for {candidate_id} is missing: {', '.join(missing)}")
        if decision["decision"] not in allowed:
            raise ValueError(f"Invalid decision for {candidate_id}: {decision['decision']}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(decision["candidate_content_hash"])):
            raise ValueError(f"Invalid candidate content hash for {candidate_id}")
    if bundle_path is None:
        bundle_path = DEFAULT_DESTINATION / BUNDLE_NAME
    candidate_hashes: dict[str, str] = {}
    if bundle_path.is_file():
        with tempfile.TemporaryDirectory(prefix="insightos-review-") as temporary:
            with zipfile.ZipFile(bundle_path) as archive:
                archive.extractall(temporary)
            root = Path(temporary)
            candidates_root = next((item for item in root.iterdir() if item.is_dir()), None)
            if candidates_root is not None:
                candidate_files = [
                    candidates_root / "candidates" / "chart_selection.jsonl",
                    candidates_root / "candidates" / "chart_alternatives.jsonl",
                    candidates_root / "candidates" / "complete_configuration.jsonl",
                    candidates_root / "candidates" / "visualization_configuration.jsonl",
                    candidates_root / "candidates" / "dashboard_layout.jsonl",
                    candidates_root / "candidates" / "negative_examples.jsonl",
                ]
                for candidate_file in candidate_files:
                    for candidate in read_jsonl(candidate_file):
                        candidate_hashes[str(candidate["record_id"])] = candidate["content_hash"]
    for candidate_id, decision in decisions.items():
        expected_hash = candidate_hashes.get(candidate_id)
        if expected_hash is not None and expected_hash != decision["candidate_content_hash"]:
            raise ValueError(f"Stale approval decision: candidate content hash changed for {candidate_id}")
    if candidate_hashes:
        unknown = sorted(set(decisions) - set(candidate_hashes))
        if unknown:
            raise ValueError(f"Decision references unknown candidates: {', '.join(unknown)}")
    if persistent_path is None:
        persistent_path = DEFAULT_DESTINATION / "review_decisions.jsonl"
    persistent_path = persistent_path.expanduser()
    persistent_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if persistent_path.is_file():
        with persistent_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    existing[str(row["candidate_id"])] = row
    existing.update(decisions)
    with persistent_path.open("w", encoding="utf-8") as handle:
        for candidate_id in sorted(existing):
            handle.write(json.dumps(existing[candidate_id], sort_keys=True, ensure_ascii=False) + "\n")
    return {
        "persistent_path": str(persistent_path),
        "decision_count": len(decisions),
        "decision_sha256": sha256_file(persistent_path),
    }


def review_template(bundle_path: Path | None, output_path: Path) -> Path:
    if bundle_path is None or not bundle_path.is_file():
        raise ValueError("Portable bundle is required to generate a review template")
    candidate_records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="insightos-review-template-") as temporary:
        temporary_root = Path(temporary)
        with zipfile.ZipFile(bundle_path) as archive:
            archive.extractall(temporary_root)
        bundle_root = next(path for path in temporary_root.iterdir() if path.is_dir())
        for relative in (
            "candidates/chart_selection.jsonl",
            "candidates/chart_alternatives.jsonl",
            "candidates/complete_configuration.jsonl",
            "candidates/visualization_configuration.jsonl",
            "candidates/dashboard_layout.jsonl",
            "candidates/negative_examples.jsonl",
        ):
            candidate_records.extend(read_jsonl(bundle_root / relative))
    candidate_records = [
        candidate for candidate in candidate_records if candidate.get("review_status") == "pending_review"
    ]
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "Candidate ID",
        "Candidate Content Hash",
        "Source",
        "Project",
        "Question",
        "Task Category",
        "Proposed Target",
        "Evidence Summary",
        "Missing Information",
        "Validation Findings",
        "Decision",
        "Reviewer Identifier",
        "Review Timestamp",
        "Reviewer Notes",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in sorted(candidate_records, key=lambda item: item["record_id"]):
            writer.writerow(
                {
                    "Candidate ID": candidate["record_id"],
                    "Candidate Content Hash": candidate["content_hash"],
                    "Source": candidate["source_filename"],
                    "Project": candidate["source_project"] or "",
                    "Question": candidate["business_question"] or "",
                    "Task Category": candidate["task_category"],
                    "Proposed Target": candidate["visualization_id"] or "",
                    "Evidence Summary": json.dumps(candidate["evidence_references"], sort_keys=True),
                    "Missing Information": json.dumps(
                        candidate.get("missing_information") or candidate["exclusion_reason"] or [],
                        sort_keys=True,
                    ),
                    "Validation Findings": candidate["exclusion_reason"] or "Pending human review",
                    "Decision": candidate["review_status"],
                    "Reviewer Identifier": "",
                    "Review Timestamp": "",
                    "Reviewer Notes": "",
                }
            )
    return output_path.resolve()


def build_training_bundle(
    source_root: Path | None = None,
    extraction_root: Path | None = None,
    destination: Path | None = None,
    decisions_path: Path | None = None,
) -> dict[str, Any]:
    source_root = (source_root or DEFAULT_SOURCE_ROOT).expanduser()
    extraction_root = (extraction_root or DEFAULT_EXTRACTION_ROOT).expanduser()
    destination = (destination or DEFAULT_DESTINATION).expanduser()
    destination.mkdir(parents=True, exist_ok=True)

    inventory = source_inventory(source_root, extraction_root)
    json_records, json_meta = ingest_json_source(source_root)
    zip_records, zip_meta = ingest_zip_source(source_root)
    overlaps = overlap_report(json_records, zip_records)
    _, supported_ids, power_bi_mapping = load_registry()
    extraction = latest_extraction(extraction_root)

    chart_candidates: list[dict[str, Any]] = []
    alternative_candidates: list[dict[str, Any]] = []
    complete_config_candidates: list[dict[str, Any]] = []
    negative_examples: list[dict[str, Any]] = []
    layout_candidates: list[dict[str, Any]] = []
    excluded_records: list[dict[str, Any]] = []
    for records, filename, sha256, label in (
        (json_records, json_meta["filename"], json_meta["sha256"], "json"),
        (zip_records, zip_meta["filename"], zip_meta["sha256"], "zip"),
    ):
        processed = process_external_examples(records, filename, sha256, label, supported_ids)
        chart_candidates.extend(processed[0])
        alternative_candidates.extend(processed[1])
        complete_config_candidates.extend(processed[2])
        negative_examples.extend(processed[3])
        layout_candidates.extend(processed[4])
        excluded_records.extend(processed[5])

    pbix_chart, pbix_config, pbix_layout, pbix_excluded = process_powerbi(extraction, supported_ids, power_bi_mapping)
    chart_candidates.extend(pbix_chart)
    layout_candidates.extend(pbix_layout)
    excluded_records.extend(pbix_excluded)
    all_config_candidates: list[dict[str, Any]] = pbix_config

    decisions = load_decisions(decisions_path)
    all_candidates = (
        chart_candidates
        + alternative_candidates
        + complete_config_candidates
        + all_config_candidates
        + layout_candidates
        + negative_examples
        + excluded_records
    )
    seen_candidate_ids: set[str] = set()
    for candidate in all_candidates:
        candidate["content_hash"] = content_hash(candidate)
        candidate_id = candidate["record_id"]
        if candidate_id in seen_candidate_ids:
            candidate["record_id"] = f"{candidate_id}:{candidate['content_hash'][:16]}"
            candidate["content_hash"] = content_hash(candidate)
        seen_candidate_ids.add(candidate["record_id"])
    candidate_by_id = {candidate["record_id"]: candidate for candidate in all_candidates}
    unknown_decisions = sorted(set(decisions) - set(candidate_by_id))
    if unknown_decisions:
        raise ValueError(f"Decision references unknown candidates: {', '.join(unknown_decisions)}")
    approved_candidates: list[dict[str, Any]] = []
    rejected_or_excluded: list[dict[str, Any]] = []
    for candidate_id, decision in decisions.items():
        candidate = candidate_by_id[candidate_id]
        if candidate["content_hash"] != decision["candidate_content_hash"]:
            raise ValueError(f"Stale decision for candidate {candidate_id}")
        if decision["decision"] == "approved":
            candidate["review_status"] = "approved"
            candidate["approval_status"] = "approved"
            candidate["reviewer_identifier"] = decision["reviewer_identifier"]
            candidate["review_timestamp"] = decision["review_timestamp"]
            candidate["reviewer_notes"] = decision["reviewer_notes"]
            approved_candidates.append(candidate)
        elif decision["decision"] == "rejected":
            candidate["review_status"] = "rejected"
            candidate["approval_status"] = "not_approved"
            rejected_or_excluded.append(candidate)
        else:
            candidate["review_status"] = "excluded"
            candidate["approval_status"] = "not_approved"
            rejected_or_excluded.append(candidate)

    split_groups: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "evaluation": []}
    unresolved_conflicts = [
        {
            "conflict_id": "marketing_funnel_metric_conflict",
            "status": "unresolved",
            "training_action": "exclude_affected_positive_target_until_human_resolution",
        },
        {
            "conflict_id": "customer_churn_customer_subscription_grain",
            "status": "unresolved",
            "training_action": "separate_customer_and_subscription_metrics_before_approval",
        },
    ]
    if approved_candidates:
        unresolved_conflicts.append(
            {
                "conflict_id": "split_assignment_required",
                "status": "blocked",
                "training_action": (
                    "No approved candidate was assigned to a leakage-safe train, validation, or evaluation split"
                ),
            }
        )

    staging = Path(tempfile.mkdtemp(prefix=".training-export-", dir=destination))
    try:
        bundle_root = staging / ROOT_NAME
        (bundle_root / "candidates").mkdir(parents=True)
        (bundle_root / "review").mkdir()
        (bundle_root / "training").mkdir()
        (bundle_root / "schemas").mkdir()
        (bundle_root / "kaggle").mkdir()

        write_jsonl(
            bundle_root / "candidates" / "chart_selection.jsonl",
            sorted(chart_candidates, key=lambda item: item["record_id"]),
        )
        write_jsonl(
            bundle_root / "candidates" / "chart_alternatives.jsonl",
            sorted(alternative_candidates, key=lambda item: item["record_id"]),
        )
        write_jsonl(
            bundle_root / "candidates" / "complete_configuration.jsonl",
            sorted(complete_config_candidates, key=lambda item: item["record_id"]),
        )
        write_jsonl(
            bundle_root / "candidates" / "visualization_configuration.jsonl",
            sorted(all_config_candidates, key=lambda item: item["record_id"]),
        )
        write_jsonl(
            bundle_root / "candidates" / "dashboard_layout.jsonl",
            sorted(layout_candidates, key=lambda item: item["record_id"]),
        )
        write_jsonl(
            bundle_root / "candidates" / "negative_examples.jsonl",
            sorted(negative_examples, key=lambda item: item["record_id"]),
        )
        write_jsonl(
            bundle_root / "review" / "excluded_records.jsonl",
            sorted(
                [candidate for candidate in all_candidates if candidate["review_status"] == "excluded"],
                key=lambda item: item["record_id"],
            ),
        )
        write_jsonl(bundle_root / "review" / "unresolved_conflicts.jsonl", unresolved_conflicts)

        fieldnames = [
            "Candidate ID",
            "Source",
            "Project",
            "Question",
            "Task Category",
            "Proposed Target",
            "Evidence Summary",
            "Missing Information",
            "Validation Findings",
            "Decision",
            "Reviewer Identifier",
            "Review Timestamp",
            "Reviewer Notes",
        ]
        with (bundle_root / "review" / "review_queue.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames + ["Candidate Content Hash"])
            writer.writeheader()
            for candidate in sorted(
                [item for item in all_candidates if item["review_status"] == "pending_review"],
                key=lambda item: item["record_id"],
            ):
                writer.writerow(
                    {
                        "Candidate ID": candidate["record_id"],
                        "Candidate Content Hash": candidate["content_hash"],
                        "Source": candidate["source_filename"],
                        "Project": candidate["source_project"] or "",
                        "Question": candidate["business_question"] or "",
                        "Task Category": candidate["task_category"],
                        "Proposed Target": candidate["visualization_id"] or "",
                        "Evidence Summary": json.dumps(candidate["evidence_references"], sort_keys=True),
                        "Missing Information": json.dumps(
                            candidate.get("missing_information") or candidate["exclusion_reason"] or [],
                            sort_keys=True,
                        ),
                        "Validation Findings": candidate["exclusion_reason"] or "Pending human review",
                        "Decision": candidate["review_status"],
                        "Reviewer Identifier": "",
                        "Review Timestamp": "",
                        "Reviewer Notes": "",
                    }
                )
        shutil.copyfile(
            bundle_root / "review" / "review_queue.csv",
            bundle_root / "review" / "pending_review.csv",
        )
        with (bundle_root / "review" / "excluded_records.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames + ["Candidate Content Hash"])
            writer.writeheader()
            for candidate in sorted(
                [item for item in all_candidates if item["review_status"] == "excluded"],
                key=lambda item: item["record_id"],
            ):
                writer.writerow(
                    {
                        "Candidate ID": candidate["record_id"],
                        "Candidate Content Hash": candidate["content_hash"],
                        "Source": candidate["source_filename"],
                        "Project": candidate["source_project"] or "",
                        "Question": candidate["business_question"] or "",
                        "Task Category": candidate["task_category"],
                        "Proposed Target": candidate["visualization_id"] or "",
                        "Evidence Summary": json.dumps(candidate["evidence_references"], sort_keys=True),
                        "Missing Information": json.dumps(
                            candidate.get("missing_information") or candidate["exclusion_reason"] or [],
                            sort_keys=True,
                        ),
                        "Validation Findings": candidate["exclusion_reason"] or "",
                        "Decision": "excluded",
                        "Reviewer Identifier": "",
                        "Review Timestamp": "",
                        "Reviewer Notes": "",
                    }
                )
        with (bundle_root / "review" / "review_decisions.template.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "candidate_id",
                    "candidate_content_hash",
                    "decision",
                    "reviewer_identifier",
                    "review_timestamp",
                    "rationale",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "candidate_id": "",
                    "candidate_content_hash": "",
                    "decision": "",
                    "reviewer_identifier": "",
                    "review_timestamp": "",
                    "rationale": "",
                }
            )

        for split_name in ("train", "validation", "evaluation"):
            write_jsonl(bundle_root / "training" / f"{split_name}.jsonl", split_groups[split_name])

        canonical_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "InsightOS canonical training export record",
            "type": "object",
            "additionalProperties": True,
            "required": [
                "record_id",
                "source_filename",
                "source_sha256",
                "source_record_id",
                "task_category",
                "review_status",
                "approval_status",
                "evidence_references",
                "content_hash",
            ],
            "properties": {
                "record_id": {"type": "string", "minLength": 1},
                "source_filename": {"type": "string", "minLength": 1},
                "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "source_record_id": {"type": ["string", "number", "integer"]},
                "task_category": {
                    "type": "string",
                    "enum": [
                        "chart_selection",
                        "chart_alternatives",
                        "complete_configuration",
                        "incomplete_review",
                        "visualization_configuration",
                        "incomplete_visualization_configuration",
                        "dashboard_layout",
                        "negative_examples",
                    ],
                },
                "review_status": {"type": "string", "enum": ["pending_review", "approved", "rejected", "excluded"]},
                "approval_status": {"type": "string", "enum": ["approved", "not_approved"]},
                "evidence_references": {
                    "type": "array",
                    "items": {"type": "object", "minProperties": 1},
                },
                "content_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "business_question": {"type": ["string", "null"], "minLength": 1},
            },
        }
        qwen_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "InsightOS Qwen conversational training example",
            "type": "object",
            "required": ["messages"],
            "additionalProperties": True,
            "properties": {
                "messages": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "required": ["role", "content"],
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                    "prefixItems": [
                        {
                            "type": "object",
                            "properties": {"role": {"const": "system"}, "content": {"type": "string", "minLength": 1}},
                            "required": ["role", "content"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {"role": {"const": "user"}, "content": {"type": "string", "minLength": 1}},
                            "required": ["role", "content"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "role": {"const": "assistant"},
                                "content": {"type": "string", "minLength": 1},
                            },
                            "required": ["role", "content"],
                            "additionalProperties": False,
                        },
                    ],
                }
            },
        }
        write_json(bundle_root / "schemas" / "canonical_record.schema.json", canonical_schema)
        write_json(bundle_root / "schemas" / "training_example.schema.json", qwen_schema)
        write_json(bundle_root / "source_inventory.json", inventory)
        write_json(
            bundle_root / "training" / "dataset_manifest.json",
            {
                "manifest_version": 1,
                "exporter_version": EXPORTER_VERSION,
                "approved_only": True,
                "split_counts": {name: len(rows) for name, rows in split_groups.items()},
                "approved_candidate_count": len(approved_candidates),
                "leakage_status": "not_applicable_zero_approved"
                if not approved_candidates
                else "blocked_missing_split_assignment",
            },
        )

        category_counts = Counter(candidate["task_category"] for candidate in all_candidates)
        review_counts = Counter(candidate["review_status"] for candidate in all_candidates)
        statistics = {
            "exporter_version": EXPORTER_VERSION,
            "source_counts": {
                "json_actual": json_meta["actual_count"],
                "zip_actual": zip_meta["actual_count"],
                "zip_declared": zip_meta["declared_count"],
                "pbix_visual_objects": json.loads(
                    (extraction / "manifests" / "extraction_manifest.json").read_text()
                ).get("visual_count"),
            },
            "candidate_counts_by_category": dict(sorted(category_counts.items())),
            "review_counts": dict(sorted(review_counts.items())),
            "approved_training_count": len(split_groups["train"]),
            "approved_validation_count": len(split_groups["validation"]),
            "approved_evaluation_count": len(split_groups["evaluation"]),
            "overlap": overlaps,
            "missing_field_mapping_exclusions": sum(
                candidate["exclusion_reason"] == "Missing field mappings" for candidate in excluded_records
            ),
        }
        write_json(bundle_root / "dataset_statistics.json", statistics)
        readme = """# InsightOS Portable Visualization Training Bundle

This bundle is review-gated and contains no unapproved positive training splits.

No model training is executed here. When no approved records are present, train, validation,
and evaluation JSONL files are intentionally empty. Human decisions must be explicitly reviewed
and imported before approved exports are rebuilt.
"""
        (bundle_root / "README.md").write_text(readme, encoding="utf-8")
        kaggle_readme = """# Kaggle Loading Guide

1. Verify the external ZIP SHA-256 using the adjacent `.zip.sha256` file or:
   `sha256sum insightos_qwen3_0_6b_training_bundle.zip`
2. Upload the ZIP to a private Kaggle dataset.
3. Add the dataset to the notebook.
4. Run `python load_dataset.py --bundle insightos_qwen3_0_6b_training_bundle.zip`.
   The loader verifies internal manifest hashes, validates split conversations, and refuses
   blocked zero-approved exports. For an already extracted bundle, omit `--bundle`.
5. Do not upload source PBIX, PDF, or other confidential original documents.
6. Do not start training until the bundle readiness status is ready.
"""
        (bundle_root / "kaggle" / "README.md").write_text(kaggle_readme, encoding="utf-8")
        (bundle_root / "kaggle" / "load_dataset.py").write_text(
            """import json
import argparse
import hashlib
import tempfile
import zipfile
from pathlib import Path

ROOT_NAME = "insightos_qwen3_0_6b_training_bundle"

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load_bundle(bundle=None):
    extracted = tempfile.TemporaryDirectory(prefix="insightos-kaggle-")
    if bundle:
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(extracted.name)
        root = Path(extracted.name) / ROOT_NAME
    else:
        root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["file_sha256"].items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            raise ValueError("Manifest or internal file hash verification failed: " + relative)
    if manifest["readiness_status"] != "READY_FOR_EXPORT":
        raise ValueError("Bundle training readiness is blocked: " + manifest["readiness_status"])
    datasets = {}
    for split in ("train", "validation", "evaluation"):
        rows = []
        path = root / "training" / (split + ".jsonl")
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            messages = row.get("messages")
            if not isinstance(messages, list) or len(messages) != 3:
                raise ValueError("Invalid conversation structure at %s:%d" % (split, line_number))
            roles = [
                message.get("role")
                for message in messages
                if isinstance(message, dict)
            ]
            if roles != ["system", "user", "assistant"]:
                raise ValueError("Invalid conversation roles at %s:%d" % (split, line_number))
            invalid_content = any(
                not isinstance(message, dict)
                or not isinstance(message.get("content"), str)
                or not message["content"].strip()
                for message in messages
            )
            if invalid_content:
                raise ValueError("Invalid conversation content at %s:%d" % (split, line_number))
            if row.get("approval_status") != "approved":
                raise ValueError("Unapproved record in split at %s:%d" % (split, line_number))
            try:
                json.loads(messages[2]["content"])
            except json.JSONDecodeError as error:
                raise ValueError("Assistant output is not valid JSON at %s:%d" % (split, line_number)) from error
            rows.append(row)
        datasets[split] = rows
    return datasets, extracted

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    try:
        datasets, extracted = load_bundle(args.bundle)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise SystemExit("ERROR: " + str(error))
    try:
        for split, rows in datasets.items():
            print(split, len(rows))
    finally:
        extracted.cleanup()
""",
            encoding="utf-8",
        )

        file_hashes: dict[str, str] = {}
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(bundle_root).as_posix()
                file_hashes[relative] = sha256_file(path)
        readiness = "READY_FOR_EXPORT" if not approved_candidates else "APPROVED_RECORDS_REQUIRE_LEAKAGE_SAFE_SPLITS"
        if not approved_candidates:
            readiness = "TRAINING_BLOCKED_PENDING_DATA_APPROVAL"
        manifest = {
            "manifest_version": 1,
            "exporter_version": EXPORTER_VERSION,
            "created_at": utc_now(),
            "bundle_name": ROOT_NAME,
            "source_inventory": "source_inventory.json",
            "file_sha256": dict(sorted(file_hashes.items())),
            "approved_training_count": len(split_groups["train"]),
            "approved_validation_count": len(split_groups["validation"]),
            "approved_evaluation_count": len(split_groups["evaluation"]),
            "readiness_status": readiness,
            "training_executed": False,
        }
        write_json(bundle_root / "manifest.json", manifest)

        archive_path = staging / BUNDLE_NAME
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(bundle_root.rglob("*")):
                if path.is_file():
                    archive.write(path, f"{ROOT_NAME}/{path.relative_to(bundle_root).as_posix()}")
        output_path = destination / BUNDLE_NAME
        temporary_archive = staging / BUNDLE_NAME
        os.replace(temporary_archive, output_path)
        bundle_sha256 = sha256_file(output_path)
        (output_path.parent / f"{output_path.name}.sha256").write_text(
            f"{bundle_sha256}  {output_path.name}\n",
            encoding="ascii",
        )
        return {
            "bundle_path": str(output_path),
            "bundle_sha256": bundle_sha256,
            "statistics": statistics,
            "readiness_status": readiness,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify_training_bundle(bundle_path: Path) -> dict[str, Any]:
    bundle_path = bundle_path.expanduser().resolve(strict=True)
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="insightos-verify-") as temporary:
        staging = Path(temporary)
        try:
            with zipfile.ZipFile(bundle_path) as archive:
                if archive.testzip() is not None:
                    errors.append("ZIP integrity test failed")
                for info in archive.infolist():
                    name = info.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in Path(name).parts:
                        errors.append(f"ZIP traversal path rejected: {info.filename}")
                    mode = info.external_attr >> 16
                    if mode & 0o170000 == 0o120000:
                        errors.append(f"ZIP symlink rejected: {info.filename}")
                if not errors:
                    archive.extractall(staging)
        except (zipfile.BadZipFile, OSError) as error:
            errors.append(f"ZIP cannot be opened: {error}")

        bundle_root = staging / ROOT_NAME
        if not bundle_root.is_dir():
            errors.append("Required bundle root is missing")
            return {"valid": False, "status": "FAILED_VERIFICATION", "errors": errors}

        required_files = [
            "README.md",
            "manifest.json",
            "source_inventory.json",
            "dataset_statistics.json",
            "candidates/chart_selection.jsonl",
            "candidates/chart_alternatives.jsonl",
            "candidates/complete_configuration.jsonl",
            "candidates/visualization_configuration.jsonl",
            "candidates/dashboard_layout.jsonl",
            "candidates/negative_examples.jsonl",
            "review/review_queue.csv",
            "review/pending_review.csv",
            "review/excluded_records.csv",
            "review/review_decisions.template.csv",
            "review/excluded_records.jsonl",
            "review/unresolved_conflicts.jsonl",
            "training/train.jsonl",
            "training/validation.jsonl",
            "training/evaluation.jsonl",
            "training/dataset_manifest.json",
            "schemas/canonical_record.schema.json",
            "schemas/training_example.schema.json",
            "kaggle/load_dataset.py",
            "kaggle/README.md",
        ]
        for relative in required_files:
            if not (bundle_root / relative).is_file():
                errors.append(f"Required file missing: {relative}")

        manifest: dict[str, Any] | None = None
        if (bundle_root / "manifest.json").is_file():
            manifest = json.loads((bundle_root / "manifest.json").read_text(encoding="utf-8"))
            for relative, expected in manifest.get("file_sha256", {}).items():
                path = bundle_root / relative
                if not path.is_file():
                    errors.append(f"Manifest-listed file missing: {relative}")
                elif sha256_file(path) != expected:
                    errors.append(f"Manifest file checksum mismatch: {relative}")

        inventory = json.loads((bundle_root / "source_inventory.json").read_text(encoding="utf-8"))
        for source in inventory.get("records", []):
            source_path = Path(source["source_path"])
            if source_path.is_file() and not source_path.is_symlink():
                if sha256_file(source_path) != source["sha256"]:
                    errors.append(f"Original source checksum mismatch: {source['filename']}")

        for relative in (
            "candidates/chart_selection.jsonl",
            "candidates/chart_alternatives.jsonl",
            "candidates/complete_configuration.jsonl",
            "candidates/visualization_configuration.jsonl",
            "candidates/dashboard_layout.jsonl",
            "candidates/negative_examples.jsonl",
            "review/excluded_records.jsonl",
            "review/unresolved_conflicts.jsonl",
            "training/train.jsonl",
            "training/validation.jsonl",
            "training/evaluation.jsonl",
        ):
            path = bundle_root / relative
            if path.is_file():
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.strip():
                        try:
                            json.loads(line)
                        except json.JSONDecodeError as error:
                            errors.append(f"Invalid JSONL at {relative}:{line_number}: {error}")

        split_counts: dict[str, int] = {}
        for split in ("train", "validation", "evaluation"):
            path = bundle_root / "training" / f"{split}.jsonl"
            rows = read_jsonl(path)
            split_counts[split] = len(rows)
            for row in rows:
                if row.get("approval_status") != "approved":
                    errors.append(f"Unapproved record found in training split: {split}")
                messages = row.get("messages")
                if not isinstance(messages, list) or len(messages) != 3:
                    errors.append(f"Split record must contain exactly three messages: {split}")
                    continue
                if [message.get("role") for message in messages] != ["system", "user", "assistant"]:
                    errors.append(f"Split record has invalid message roles: {split}")
                if any(
                    not isinstance(message.get("content"), str) or not message["content"].strip()
                    for message in messages
                ):
                    errors.append(f"Split record has invalid message content: {split}")

        if manifest is not None:
            if manifest.get("approved_training_count", -1) != split_counts["train"]:
                errors.append("Training manifest count differs from train.jsonl")
            if manifest.get("approved_validation_count", -1) != split_counts["validation"]:
                errors.append("Validation manifest count differs from validation.jsonl")
            if manifest.get("approved_evaluation_count", -1) != split_counts["evaluation"]:
                errors.append("Evaluation manifest count differs from evaluation.jsonl")
            if split_counts["train"] == 0 and split_counts["validation"] == 0 and split_counts["evaluation"] == 0:
                if manifest.get("readiness_status") != "TRAINING_BLOCKED_PENDING_DATA_APPROVAL":
                    errors.append("Zero-approved bundle does not report the fail-closed readiness status")
            if sum(split_counts.values()) == 0 and manifest.get("training_executed") is not False:
                errors.append("Training must remain disabled for a zero-approved bundle")

        candidate_records: list[dict[str, Any]] = []
        for relative in (
            "candidates/chart_selection.jsonl",
            "candidates/chart_alternatives.jsonl",
            "candidates/complete_configuration.jsonl",
            "candidates/visualization_configuration.jsonl",
            "candidates/dashboard_layout.jsonl",
            "candidates/negative_examples.jsonl",
        ):
            candidate_records.extend(read_jsonl(bundle_root / relative))
        candidate_ids = [candidate.get("record_id") for candidate in candidate_records]
        if len(candidate_ids) != len(set(candidate_ids)):
            errors.append("Duplicate canonical candidate IDs found")
        for candidate in candidate_records:
            if not candidate.get("record_id"):
                errors.append("Canonical candidate missing record_id")
            if not candidate.get("source_filename") or not candidate.get("source_sha256"):
                errors.append(f"Candidate missing provenance: {candidate.get('record_id')}")
            if not candidate.get("content_hash"):
                errors.append(f"Candidate missing content hash: {candidate.get('record_id')}")
            elif content_hash(candidate) != candidate["content_hash"]:
                errors.append(f"Candidate content hash mismatch: {candidate.get('record_id')}")
            canonical_schema = json.loads(
                (bundle_root / "schemas" / "canonical_record.schema.json").read_text(encoding="utf-8")
            )
            schema_errors = sorted(
                Draft202012Validator(canonical_schema).iter_errors(candidate), key=lambda error: list(error.path)
            )
            for error in schema_errors:
                errors.append(f"Canonical schema validation failed for {candidate.get('record_id')}: {error.message}")

        category_files = {
            "chart_selection": bundle_root / "candidates" / "chart_selection.jsonl",
            "chart_alternatives": bundle_root / "candidates" / "chart_alternatives.jsonl",
            "complete_configuration": bundle_root / "candidates" / "complete_configuration.jsonl",
            "incomplete_visualization_configuration": bundle_root / "candidates" / "visualization_configuration.jsonl",
            "dashboard_layout": bundle_root / "candidates" / "dashboard_layout.jsonl",
            "negative_examples": bundle_root / "candidates" / "negative_examples.jsonl",
        }
        for category, path in category_files.items():
            for candidate in read_jsonl(path):
                if candidate.get("task_category") != category:
                    errors.append(
                        f"Misclassified candidate {candidate.get('record_id')} in {path.name}; expected {category}"
                    )

    return {
        "valid": not errors,
        "status": "VERIFIED" if not errors else "FAILED_VERIFICATION",
        "errors": errors,
        "bundle_sha256": sha256_file(bundle_path),
    }
