from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_extraction(root: Path) -> Path:
    candidates = sorted(
        (path for path in root.expanduser().glob("extraction-*") if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No Power BI extraction found under {root}")
    return candidates[0]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def archive_jsonl(bundle: Path, member_suffix: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(bundle) as archive:
        matches = [
            name for name in archive.namelist()
            if name.endswith(member_suffix)
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected one {member_suffix} member, found {len(matches)}")
        return [
            json.loads(line)
            for line in archive.read(matches[0]).decode("utf-8").splitlines()
            if line.strip()
        ]


def build_enrichment(
    archive: Path,
    extraction_root: Path,
    destination: Path,
) -> dict[str, Any]:
    archive = archive.expanduser().resolve(strict=True)
    extraction = latest_extraction(extraction_root)
    candidate_dir = extraction / "candidates"

    source_inventory = json.loads(
        (extraction / "inventory" / "source_inventory.json").read_text(encoding="utf-8")
    )
    selections = read_jsonl(candidate_dir / "chart_selection.jsonl")
    configurations = read_jsonl(candidate_dir / "chart_configuration.jsonl")
    layouts = read_jsonl(candidate_dir / "dashboard_layout.jsonl")
    bundle_selections = archive_jsonl(archive, "/candidates/chart_selection.jsonl")
    bundle_complete_configurations = archive_jsonl(
        archive,
        "/candidates/complete_configuration.jsonl",
    )

    source_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in source_inventory["records"]:
        source_by_project[str(record["project_id"])].append(record)

    project_names = {
        "adventureworks": "AdventureWorks Business Performance",
        "stock_market": "Stock Market Analytics",
        "superstore": "Superstore Sales Performance",
        "customer_churn": "Customer Churn and Retention",
        "marketing_funnel": "Marketing Funnel and Conversion",
        "cryptocurrency": "Cryptocurrency Dashboard",
        "crypto_forecasting": "Cryptocurrency Time-Series Forecasting",
    }
    projects: list[dict[str, Any]] = []
    derived_prompts: list[dict[str, Any]] = []

    for project_id, display_name in project_names.items():
        project_sources = source_by_project.get(project_id, [])
        project_selections = [row for row in selections if row["project_id"] == project_id]
        project_configs = [row for row in configurations if row["project_id"] == project_id]
        project_layouts = [row for row in layouts if row["project_id"] == project_id]
        visual_types = Counter(
            row["observed_visual_type"]
            for row in project_selections
            if row.get("observed_visual_type")
        )

        projects.append({
            "project_id": project_id,
            "display_name": display_name,
            "source_authority": "first_party_owner_authored",
            "author_attestation": "user_confirmed",
            "owner_estimated_accuracy": 0.95,
            "accuracy_estimate_basis": "owner_attestation",
            "independent_verification": "not_established",
            "source_records": [
                {
                    "filename": row["original_filename"],
                    "sha256": row["sha256"],
                    "document_type": row["document_type"],
                    "project_id": row["project_id"],
                }
                for row in project_sources
            ],
            "selection_candidate_count": len(project_selections),
            "configuration_candidate_count": len(project_configs),
            "dashboard_layout_page_count": len(project_layouts),
            "observed_visual_types": dict(sorted(visual_types.items())),
            "extraction_version": "insightos-powerbi-reference-v1",
        })

        if project_layouts:
            derived_prompt = (
                f"Design an executive presentation for the {display_name} project "
                "that communicates the report's available KPI hierarchy, visual sections, "
                "comparisons, trends, and layout patterns without assuming unavailable data fields."
            )
            evidence_references = [
                {
                    "source_pbix_hash": row["source_pbix_hash"],
                    "report_page_id": row["report_page_id"],
                    "visual_ids": list(row["visual_ids"]),
                }
                for row in project_layouts
            ]
            derived_prompts.append({
                "record_id": f"derived:{project_id}",
                "original_business_question": None,
                "derived_training_prompt": derived_prompt,
                "source_business_context": display_name,
                "source_authority": "first_party_owner_authored",
                "evidence_references": evidence_references,
                "prompt_classification": "derived_with_evidence",
                "semantic_correctness": "inferred_needs_review",
                "training_target_completeness": "incomplete_visual_metadata",
                "future_model_destination": "qwen3_0_6b_visualization_presentation",
                "human_review_status": "not_approved",
            })

    recoverability: list[dict[str, Any]] = []
    for row in configurations:
        mappings = row.get("mappings") or []
        missing = set(row.get("missing_information") or [])
        has_mapping = bool(mappings)
        if has_mapping:
            class_name = "partially_recoverable_visual_configuration"
        else:
            class_name = "recoverable_metadata_only"
        recoverability.append({
            "candidate_id": row["candidate_id"],
            "project_id": row["project_id"],
            "visual_id": row["visual_id"],
            "visual_type": row["visual_type"],
            "report_page_id": row["report_page_id"],
            "source_pbix_hash": row["source_pbix_hash"],
            "recoverability_class": class_name,
            "recoverable_metadata": {
                "position": row.get("position"),
                "dimensions": row.get("dimensions"),
                "formatting": row.get("formatting"),
            },
            "missing_semantic_mappings": not has_mapping,
            "missing_aggregation": "aggregation_confirmation" in missing,
            "missing_filters": "filter_context" in missing or "filters" in missing,
            "missing_field_semantic_types": not has_mapping,
            "missing_complete_visual_configuration": True,
            "quality_dimensions": {
                "source_authority": "owner_attested_source",
                "evidence_completeness": (
                    "directly_observed" if has_mapping else "partially_observed"
                ),
                "semantic_correctness": (
                    "cross_source_supported" if has_mapping else "inferred_needs_review"
                ),
                "training_target_completeness": "unsupported",
                "human_review_status": "not_approved",
            },
            "future_model_destination": "qwen3_0_6b_visualization_presentation",
        })

    extraction_rows = read_jsonl(candidate_dir / "chart_selection.jsonl")
    selection_reconciliation: list[dict[str, Any]] = []
    reconciliation_report = None
    for parent in Path(__file__).resolve().parents:
        candidate_report = (
            parent
            / "docs/training_export/d2.5.2_reconciliation/record_level_reconciliation.json"
        )
        if candidate_report.exists():
            reconciliation_report = candidate_report
            break

    if reconciliation_report is not None:
        prior_report = json.loads(
            reconciliation_report.read_text(encoding="utf-8")
        )
        for row in prior_report.get("chart_selection_records", []):
            selection_reconciliation.append({
                "candidate_id": str(row["record_id"]),
                "source_types": [row["source_type"]],
                "source_record_id": row["source_record_id"],
                "task_type": "chart_selection",
                "future_model_destination": "qwen3_0_6b_visualization_presentation",
                "evidence_available": True,
                "source_pbix_hash": None,
                "project_id": None,
                "human_review_status": "not_approved",
            })
    else:
        for row in bundle_selections:
            candidate_id = row.get("candidate_id") or row.get("record_id")
            selection_reconciliation.append({
                "candidate_id": str(candidate_id),
                "source_types": ["json_or_zip"],
                "source_record_id": candidate_id,
                "task_type": "chart_selection",
                "future_model_destination": "qwen3_0_6b_visualization_presentation",
                "evidence_available": True,
                "source_pbix_hash": row.get("source_pbix_hash"),
                "project_id": row.get("project_id"),
                "human_review_status": row.get("review_status", "not_approved"),
            })
        for row in bundle_complete_configurations:
            candidate_id = row.get("candidate_id") or row.get("record_id")
            selection_reconciliation.append({
                "candidate_id": str(candidate_id),
                "source_types": ["json_or_zip"],
                "source_record_id": row.get("record_id") or candidate_id,
                "task_type": "incomplete_complete_configuration",
                "future_model_destination": "qwen3_0_6b_visualization_presentation",
                "evidence_available": True,
                "source_pbix_hash": row.get("source_pbix_hash"),
                "project_id": row.get("project_id"),
                "human_review_status": row.get("review_status", "not_approved"),
            })
        for row in extraction_rows:
            candidate_id = row.get("candidate_id") or row.get("record_id")
            selection_reconciliation.append({
                "candidate_id": str(candidate_id),
                "source_types": ["pbix_metadata"],
                "source_record_id": candidate_id,
                "task_type": "chart_selection",
                "future_model_destination": "qwen3_0_6b_visualization_presentation",
                "evidence_available": bool(row.get("provenance")),
                "source_pbix_hash": row.get("source_pbix_hash"),
                "project_id": row.get("project_id"),
                "human_review_status": row.get("review_status", "not_approved"),
            })

    extraction_unique_visual_ids = {
        row.get("candidate_id") for row in configurations
    }
    reconciliation = {
        "archive_selection_record_count": len(bundle_selections),
        "extraction_selection_record_count": len(extraction_rows),
        "pbix_unique_visual_identity_count": len(extraction_unique_visual_ids),
        "difference_explanation": (
            "The 647 archive selection records include chart-selection records sourced "
            "from JSON, ZIP, and Power BI extraction. The 609 Power BI identities are "
            "deduplicated visual records; they are a subset of the broader selection corpus."
        ),
        "pbix_configuration_count": len(configurations),
        "archive_selection_by_project": dict(sorted(Counter(
            row.get("project_id") or "unknown"
            for row in selection_reconciliation
        ).items())),
    }

    status_report = {
        "original_d2_5_1_pending_count": 93,
        "original_d2_5_1_excluded_count": 1054,
        "current_excluded_count": 1251,
        "numeric_difference": 197,
        "current_pending_count": 93,
        "record_level_status": "unresolved_missing_authoritative_pre_hardening_archive",
        "record_ids_reconstructed": 11,
        "exact_ids_explained": False,
        "why_not": (
            "A record-level comparison requires the pre-D2.5.1 candidate register and "
            "candidate hashes. Neither has been preserved in the current cache."
        ),
        "evidence_safe_classification": (
            "The 197 figure can currently be described only as aggregate register growth "
            "from 1,054 to 1,251 exclusions; it is not evidence of 197 named status transitions."
        ),
    }

    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    output_dir = destination / "insightos_qwen3_0_6b_storytelling_enriched"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    (output_dir / "storytelling_enrichment").mkdir(parents=True)
    (output_dir / "model_destinations").mkdir()
    (output_dir / "training").mkdir()

    canonical_records = {
        "source_authority": "first_party_owner_authored",
        "author_attestation": "user_confirmed",
        "owner_estimated_accuracy": 0.95,
        "accuracy_estimate_basis": "owner_attestation",
        "independent_verification": "not_established",
        "extraction_version": extraction.name,
        "projects": projects,
        "selection_reconciliation": reconciliation,
        "status_reconciliation": status_report,
    }

    json_write(output_dir / "storytelling_enrichment" / "canonical_knowledge.json", canonical_records)
    jsonl_write(output_dir / "storytelling_enrichment" / "derived_training_prompts.jsonl", derived_prompts)
    jsonl_write(output_dir / "storytelling_enrichment" / "configuration_recoverability.jsonl", recoverability)
    jsonl_write(output_dir / "storytelling_enrichment" / "selection_reconciliation.jsonl", selection_reconciliation)

    model_destinations = []
    for row in recoverability:
        model_destinations.append({
            "candidate_id": row["candidate_id"],
            "future_model_destination": "qwen3_0_6b_visualization_presentation",
            "knowledge_domain": "visualization_intelligence",
        })
    for row in derived_prompts:
        model_destinations.append({
            "candidate_id": row["record_id"],
            "future_model_destination": "qwen3_0_6b_visualization_presentation",
            "knowledge_domain": "data_storytelling_and_dashboard_design",
        })
    jsonl_write(output_dir / "model_destinations" / "model_destination_mapping.jsonl", model_destinations)

    csv_path = output_dir / "storytelling_enrichment" / "review_queue.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "candidate_id",
            "candidate_content_hash",
            "human_reviewer",
            "timestamp",
            "decision",
            "rationale",
        ])
        for row in derived_prompts + [{**item, "record_id": item["candidate_id"]} for item in recoverability]:
            record_id = row.get("record_id") or row.get("candidate_id")
            content = json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
            writer.writerow([
                record_id,
                hashlib.sha256(content).hexdigest(),
                "",
                "",
                "pending",
                "owner source provenance established; derived metadata requires human review",
            ])

    for split in ("train.jsonl", "validation.jsonl", "evaluation.jsonl"):
        (output_dir / "training" / split).write_text("", encoding="utf-8")

    enrichment_manifest = {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_archive": str(archive),
        "base_archive_sha256": sha256(archive),
        "readiness_status": "TRAINING_BLOCKED_PENDING_DATA_APPROVAL",
        "approved_training_count": 0,
        "approved_validation_count": 0,
        "approved_evaluation_count": 0,
        "training_executed": False,
        "source_authority": "first_party_owner_authored",
        "file_sha256": {},
    }
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            relative = path.relative_to(output_dir).as_posix()
            enrichment_manifest["file_sha256"][relative] = sha256(path)

    json_write(output_dir / "storytelling_enrichment" / "manifest.json", enrichment_manifest)

    bundle_name = "insightos_qwen3_0_6b_storytelling_enriched_bundle.zip"
    bundle_path = destination / bundle_name
    build_zip(archive, output_dir, bundle_path)
    checksum = sha256(bundle_path)
    (Path(str(bundle_path) + ".sha256")).write_text(
        f"{checksum}  {bundle_path.name}\n",
        encoding="utf-8",
    )

    return {
        "extraction": str(extraction),
        "archive": str(bundle_path),
        "sha256": checksum,
        "size": bundle_path.stat().st_size,
        "project_count": len(projects),
        "derived_prompt_count": len(derived_prompts),
        "configuration_count": len(recoverability),
        "selection_count": len(selection_reconciliation),
    }


def json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def jsonl_write(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def build_zip(base_archive: Path, enrichment_dir: Path, bundle_path: Path) -> None:
    with zipfile.ZipFile(base_archive) as source:
        base_root = source.namelist()[0].split("/", 1)[0]
        new_root = "insightos_qwen3_0_6b_storytelling_enriched_bundle"
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
            seen: set[str] = set()
            for member in source.infolist():
                data = source.read(member.filename)
                suffix = member.filename[len(base_root):].lstrip("/")
                if not suffix or suffix in seen:
                    continue
                if suffix in {
                    "training/train.jsonl",
                    "training/validation.jsonl",
                    "training/evaluation.jsonl",
                }:
                    continue
                target.writestr(f"{new_root}/{suffix}", data)
                seen.add(suffix)
            for path in sorted(enrichment_dir.rglob("*")):
                if path.is_file():
                    relative = path.relative_to(enrichment_dir).as_posix()
                    target.write(path, f"{new_root}/{relative}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a separate first-party Power BI storytelling enrichment bundle."
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--extraction-root",
        type=Path,
        default=Path.home() / ".cache/insightos/powerbi_reference",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path.home() / ".cache/insightos/training_exports",
    )
    args = parser.parse_args()
    result = build_enrichment(args.archive, args.extraction_root, args.destination)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
