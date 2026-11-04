from __future__ import annotations

import argparse
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

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_ROOT = Path("/mnt/c/Users/NITHIN/Desktop/data")
DEFAULT_EXTRACTION_ROOT = Path.home() / ".cache" / "insightos" / "powerbi_reference"
DEFAULT_OUTPUT_ROOT = Path.home() / ".cache" / "insightos" / "reference_quality"
AUDIT_VERSION = "insightos-reference-quality-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_question(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _resolve_real_file(path: Path) -> Path:
    path = path.expanduser()
    if path.is_symlink():
        raise ValueError(f"Source symlinks are not accepted: {path}")
    if not path.is_file():
        raise ValueError(f"Source file does not exist or is not a regular file: {path}")
    return path.resolve(strict=True)


def load_json_references(path: Path) -> dict[str, Any]:
    path = _resolve_real_file(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("examples"), list):
        raise ValueError("Visualization JSON source must contain an examples array")
    examples = payload["examples"]
    return {
        "source_type": "json_visualization_reference",
        "source_filename": path.name,
        "source_path": str(path),
        "source_sha256": sha256_file(path),
        "declared_count": payload.get("total_examples"),
        "actual_count": len(examples),
        "dataset_name": payload.get("dataset_name"),
        "version": payload.get("version"),
        "examples": examples,
    }


def load_external_zip_references(path: Path) -> dict[str, Any]:
    path = _resolve_real_file(path)
    records = []
    manifest = None
    member_summaries = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 2000:
            raise ValueError("Visualization ZIP exceeds the member safety limit")
        total_size = 0
        for info in infos:
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("/") or ".." in Path(normalized).parts:
                raise ValueError(f"Visualization ZIP traversal rejected: {info.filename}")
            if info.is_dir():
                continue
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError(f"Visualization ZIP symlink rejected: {info.filename}")
            if info.file_size > 20 * 1024 * 1024:
                raise ValueError(f"Visualization ZIP member exceeds size limit: {info.filename}")
            if info.compress_size == 0:
                ratio = 0.0
            else:
                ratio = info.file_size / info.compress_size
            if ratio > 500:
                raise ValueError(f"Visualization ZIP compression limit exceeded: {info.filename}")
            total_size += info.file_size
            if total_size > 100 * 1024 * 1024:
                raise ValueError("Visualization ZIP uncompressed total exceeds safety limit")
            member_summaries.append(
                {
                    "filename": info.filename,
                    "uncompressed_size": info.file_size,
                }
            )
            if not info.filename.lower().endswith(".json"):
                continue
            payload = json.loads(archive.read(info))
            if info.filename.endswith("manifest.json") and isinstance(payload, dict):
                manifest = payload
            elif isinstance(payload, dict):
                records.append(payload)
    actual_count = len(records)
    return {
        "source_type": "zip_visualization_reference",
        "source_filename": path.name,
        "source_path": str(path),
        "source_sha256": sha256_file(path),
        "declared_count": manifest.get("total_examples") if manifest else None,
        "actual_count": actual_count,
        "manifest": manifest,
        "member_count": len(infos),
        "members": member_summaries,
        "records": records,
    }


def latest_extraction_root(root: Path) -> Path:
    root = root.expanduser().resolve(strict=False)
    if not root.is_dir():
        raise ValueError(f"Power BI extraction root does not exist: {root}")
    directories = [path for path in root.iterdir() if path.is_dir() and not path.is_symlink()]
    if not directories:
        raise ValueError("No Power BI extraction directories found")
    return max(directories, key=lambda path: path.stat().st_mtime)


def reconcile_extraction_manifest(extraction_root: Path) -> dict[str, Any]:
    root = extraction_root.expanduser().resolve(strict=True)
    manifest_path = root / "manifests" / "extraction_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Extraction manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chart_selection_path = root / "candidates" / "chart_selection.jsonl"
    chart_configuration_path = root / "candidates" / "chart_configuration.jsonl"
    layout_path = root / "candidates" / "dashboard_layout.jsonl"

    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        records = []
        if not path.is_file():
            return records
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        return records

    selections = read_jsonl(chart_selection_path)
    configurations = read_jsonl(chart_configuration_path)
    layouts = read_jsonl(layout_path)
    project_inspections_path = root / "projects" / "pbix_inspections.json"
    project_inspections = json.loads(project_inspections_path.read_text(encoding="utf-8"))
    visual_objects = [visual for inspection in project_inspections for visual in inspection.get("visuals", [])]
    selection_keys = Counter(
        (
            record["source_pbix_hash"],
            record["report_page_id"],
            record.get("candidate_id"),
        )
        for record in selections
    )
    configuration_keys = Counter(
        (
            record["source_pbix_hash"],
            record["report_page_id"],
            record["visual_id"],
        )
        for record in configurations
    )
    underlying_selection_keys = {
        (record["source_pbix_hash"], record["report_page_id"], record["candidate_id"]) for record in selections
    }

    copied_pbix_groups = []
    duplicate_path = root / "quality" / "duplicate_report.json"
    duplicate_report = json.loads(duplicate_path.read_text(encoding="utf-8")) if duplicate_path.is_file() else {}
    for group in duplicate_report.get("duplicate_groups", []):
        copied_pbix_groups.append(group)

    missing_field_mapping = sum(not record.get("data_field_references") for record in selections)
    unsupported_chart_ids = []
    registry = json.loads((PACKAGE_ROOT / "visualization" / "registry.json").read_text(encoding="utf-8"))
    supported_ids = {entry["id"] for entry in registry["visualizations"]}
    powerbi_to_registry = {
        "card": "kpi_scorecard",
        "cardVisual": "kpi_scorecard",
        "clusteredBarChart": "grouped_bar",
        "clusteredColumnChart": "grouped_bar",
        "barChart": "ranked_bar",
        "columnChart": "ranked_bar",
        "lineChart": "line_chart",
        "donutChart": "donut_chart",
        "scatterChart": "scatter_plot",
        "pieChart": "pie_chart",
        "map": "map_symbol",
    }
    for visual in visual_objects:
        visual_type = visual.get("visual_type")
        mapped = powerbi_to_registry.get(visual_type)
        if visual_type not in {"unknown", "textbox", "shape", "actionButton", "slicer"} and mapped not in supported_ids:
            unsupported_chart_ids.append(visual_type)

    reconciliation = {
        "audit_version": AUDIT_VERSION,
        "extraction_root": str(root),
        "manifest": manifest,
        "visual_object_count": len(visual_objects),
        "unique_visual_identity_count": len(
            {(visual["source_pbix_hash"], visual["report_page_id"], visual["visual_id"]) for visual in visual_objects}
        ),
        "selection_candidate_count": len(selections),
        "configuration_candidate_count": len(configurations),
        "layout_candidate_count": len(layouts),
        "selection_minus_visual_difference": len(selections) - len(visual_objects),
        "duplicate_selection_keys": sum(count - 1 for count in selection_keys.values() if count > 1),
        "duplicate_configuration_keys": sum(count - 1 for count in configuration_keys.values() if count > 1),
        "underlying_selection_keys": len(underlying_selection_keys),
        "selection_configuration_identity_match": len(
            {(record["source_pbix_hash"], record["report_page_id"], record["visual_id"]) for record in configurations}
        ),
        "candidates_missing_field_mapping": missing_field_mapping,
        "unsupported_or_extension_chart_identifiers": sorted(set(unsupported_chart_ids)),
        "copied_pbix_groups": copied_pbix_groups,
        "candidate_review_states": dict(
            Counter(
                record.get("review_status", "missing")
                for collection in (selections, configurations, layouts)
                for record in collection
            )
        ),
        "candidate_approval_states": dict(
            Counter(
                record.get("approval_status", "missing")
                for collection in (selections, configurations, layouts)
                for record in collection
            )
        ),
    }
    return reconciliation


def load_reference_examples(json_path: Path, zip_path: Path) -> dict[str, Any]:
    json_source = load_json_references(json_path)
    zip_source = load_external_zip_references(zip_path)
    json_examples = json_source["examples"]
    zip_examples = zip_source["records"]

    overlap = []
    zip_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in zip_examples:
        zip_by_question[normalize_question(record.get("business_question"))].append(record)
    for record in json_examples:
        key = normalize_question(record.get("business_question"))
        matches = zip_by_question.get(key, [])
        if matches:
            overlap.append(
                {
                    "business_question": record.get("business_question"),
                    "normalized_question": key,
                    "json_ids": [record.get("id")] if record.get("id") else [],
                    "zip_ids": [match.get("id") for match in matches if match.get("id")],
                }
            )
    json_ids = [record.get("id") for record in json_examples if record.get("id")]
    zip_ids = [record.get("id") for record in zip_examples if record.get("id")]
    return {
        "json_source": json_source,
        "zip_source": zip_source,
        "exact_normalized_business_question_overlap_count": len(overlap),
        "overlap_groups": overlap,
        "json_duplicate_ids": sorted([example_id for example_id, count in Counter(json_ids).items() if count > 1]),
        "zip_duplicate_ids": sorted([example_id for example_id, count in Counter(zip_ids).items() if count > 1]),
    }


def metric_discrepancy_report() -> dict[str, Any]:
    return {
        "report_version": 1,
        "marketing_funnel": {
            "status": "unresolved",
            "dashboard_claim": {
                "leads": "41000",
                "conversions": "approximately 5000",
                "conversion_rate": "11.27%",
                "evidence_state": "pdf_or_dashboard_observation",
            },
            "written_claim": {
                "leads": "approximately 13800",
                "conversions": "886",
                "conversion_rate": "6.44%",
                "evidence_state": "written_insight",
            },
            "resolution": None,
            "training_action": "exclude_affected_positive_target_until_human_resolution",
            "review_status": "pending_review",
        },
        "customer_churn": {
            "status": "requires_metric_separation",
            "metrics": [
                "customer_count",
                "churned_customer_count",
                "subscription_count",
                "churned_subscription_count",
                "churn_rate",
            ],
            "must_not_conflate_customer_and_subscription": True,
            "training_action": "exclude_or_flag_until_customer_and_subscription_grain_is_verified",
            "review_status": "pending_review",
        },
    }


def training_readiness_gate(
    reconciliation: dict[str, Any],
    references: dict[str, Any],
    approved_training_count: int = 0,
    approved_evaluation_count: int = 0,
) -> dict[str, Any]:
    blockers = []
    if reconciliation["manifest"].get("all_candidates_pending_review") is not True:
        blockers.append("Extracted candidates do not all remain pending review.")
    if reconciliation["manifest"].get("no_candidates_approved") is not True:
        blockers.append("One or more extracted candidates is marked approved.")
    if reconciliation["visual_object_count"] != reconciliation["manifest"].get("visual_count"):
        blockers.append("Power BI visual-object reconciliation differs from the extraction manifest.")
    selection_manifest_count = reconciliation["manifest"].get("candidate_counts", {}).get("chart_selection.jsonl")
    if reconciliation["selection_candidate_count"] != selection_manifest_count:
        blockers.append("Chart-selection candidate count differs from the extraction manifest.")
    configuration_manifest_count = (
        reconciliation["manifest"].get("candidate_counts", {}).get("chart_configuration.jsonl")
    )
    if reconciliation["configuration_candidate_count"] != configuration_manifest_count:
        blockers.append("Chart-configuration candidate count differs from the extraction manifest.")
    if references["json_source"]["actual_count"] != 50:
        blockers.append("JSON visualization reference count must be independently verified as 50.")
    if references["zip_source"]["actual_count"] != 34:
        blockers.append("ZIP visualization reference count must be independently verified as 34.")
    if references["json_source"]["declared_count"] != 50:
        blockers.append("JSON source manifest declaration is inconsistent.")
    if references["zip_source"]["declared_count"] != 33:
        blockers.append("ZIP manifest declaration is not the independently observed 33.")
    if approved_training_count < 1:
        blockers.append("No explicitly approved training records are available.")
    if approved_evaluation_count < 1:
        blockers.append("No independently approved evaluation records are available.")
    status = "READY_FOR_QWEN3_0_6B_FINE_TUNING" if not blockers else "TRAINING_BLOCKED_PENDING_DATA_APPROVAL"
    return {
        "gate_status": status,
        "training_executed": False,
        "approval_source_required": True,
        "blockers": blockers,
        "thresholds": {
            "minimum_approved_training_records": 1,
            "minimum_approved_evaluation_records": 1,
            "minimum_unique_projects": 1,
            "minimum_unique_visual_families": 1,
            "minimum_complete_configurations": 1,
        },
        "approved_training_count": approved_training_count,
        "approved_evaluation_count": approved_evaluation_count,
    }


def audit_all_sources(
    source_root: Path,
    extraction_root: Path,
    output_root: Path | None = None,
) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve(strict=True)
    extraction = latest_extraction_root(extraction_root)
    reconciliation = reconcile_extraction_manifest(extraction)
    references = load_reference_examples(
        source_root / "visualization_training_dataset.json",
        source_root / "viz_training_data.zip",
    )
    discrepancies = metric_discrepancy_report()
    gate = training_readiness_gate(reconciliation, references)
    payload = {
        "audit_version": AUDIT_VERSION,
        "created_at": utc_now(),
        "source_directory": str(source_root),
        "extraction_root": str(extraction),
        "extraction_reconciliation": reconciliation,
        "visualization_reference_reconciliation": references,
        "metric_discrepancies": discrepancies,
        "training_readiness_gate": gate,
        "model_training_executed": False,
        "no_candidates_approved": True,
        "smoke_adapter_modified": False,
    }

    if output_root is not None:
        output_root = output_root.expanduser().resolve(strict=False)
        output_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".reference-quality-", dir=output_root))
        try:
            write_json(staging / "reference_quality_audit.json", payload)
            destination = output_root / f"audit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
            if destination.exists():
                raise ValueError(f"Unexpected existing audit destination: {destination}")
            os.rename(staging, destination)
            staging = None
            payload["external_output_root"] = str(destination)
        finally:
            safe_cleanup(staging, output_root)
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_cleanup(staging: Path | None, output_root: Path) -> None:
    if staging is None:
        return
    root = output_root.expanduser().resolve(strict=False)
    path = staging.expanduser().resolve(strict=False)
    if path == root or root not in path.parents or not path.name.startswith(".reference-quality-"):
        return
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insightos_training.reference_quality.audit")
    subcommands = parser.add_subparsers(dest="command", required=True)
    audit = subcommands.add_parser("audit")
    audit.add_argument("--source", type=Path, default=DEFAULT_SOURCE_ROOT)
    audit.add_argument("--extraction-root", type=Path, default=DEFAULT_EXTRACTION_ROOT)
    audit.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        payload = audit_all_sources(args.source, args.extraction_root, args.output)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"Error: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
