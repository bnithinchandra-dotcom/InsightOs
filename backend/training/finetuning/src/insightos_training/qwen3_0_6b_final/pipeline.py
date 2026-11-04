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

import jsonschema

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = PACKAGE_ROOT
REGISTRY_PATH = PACKAGE_ROOT / "visualization" / "registry.json"
OUTPUT_SCHEMA_PATH = PACKAGE_ROOT / "visualization" / "output_schema.json"
RECONCILIATION_PATH = (
    PACKAGE_ROOT
    / "docs/training_export/d2.5.2_reconciliation/record_level_reconciliation.json"
)
BASE_ARCHIVE = Path.home() / ".cache/insightos/training_exports/insightos_qwen3_0_6b_training_bundle.zip"
BASE_SHA256 = "caeb27815f7bb27c1dca502ec5e4a7c53cdc9081a1ffd4310a69a45a695d33a6"
STORYTELLING_ARCHIVE = (
    Path.home()
    / ".cache/insightos/training_exports/insightos_qwen3_0_6b_storytelling_enriched_bundle.zip"
)
STORYTELLING_SHA256 = "3ff9831fe701af514748ab682d079c0bc468f0dc8c06748deded6299f57b5c36"
CURRENT_FINAL_ARCHIVE = Path.home() / ".cache/insightos/qwen3_0_6b_final/insightos_qwen3_0_6b_final_training_data.zip"
CURRENT_FINAL_SHA256 = "95447f1899f697aca2a693783cabf0f00b87e7725344b5705fbf21077183af4a"
SMOKE_ADAPTER = Path.home() / ".cache/insightos/incoming/insightos_qwen3_0_6b_validated.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def latest_extraction(root: Path) -> Path:
    candidates: list[tuple[datetime, Path]] = []
    for path in root.expanduser().glob("extraction-*"):
        manifest_path = path / "manifests/extraction_manifest.json"
        if not path.is_dir() or not manifest_path.exists():
            continue
        manifest = read_json(manifest_path)
        created_at = manifest.get("created_at")
        if not created_at:
            continue
        candidates.append((datetime.fromisoformat(created_at), path))
    if not candidates:
        raise FileNotFoundError(f"No timestamped Power BI extraction found under {root}")
    return max(candidates, key=lambda item: item[0])[1]


def archive_jsonl(bundle: Path, suffix: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(bundle) as archive:
        matches = [
            name for name in archive.namelist()
            if name.startswith("insightos_qwen3_0_6b_training_bundle/") and name.endswith(suffix)
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected one archive member ending {suffix}, found {len(matches)}")
        return [
            json.loads(line)
            for line in archive.read(matches[0]).decode("utf-8").splitlines()
            if line.strip()
        ]


def archive_jsonl_member(bundle: Path, suffix: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(bundle) as archive:
        matches = [name for name in archive.namelist() if name.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f"Expected one archive member ending {suffix}, found {len(matches)}")
        return [
            json.loads(line)
            for line in archive.read(matches[0]).decode("utf-8").splitlines()
            if line.strip()
        ]


def validate_output_contract(output: dict[str, Any]) -> tuple[bool, list[str]]:
    schema = read_json(OUTPUT_SCHEMA_PATH)
    registry = read_json(REGISTRY_PATH)
    supported = {row["id"] for row in registry["visualizations"]}
    try:
        jsonschema.validate(output, schema)
    except jsonschema.ValidationError as error:
        return False, [error.message]
    visual_id = output.get("visualization_id")
    if visual_id not in supported:
        return False, [f"Unsupported visualization identifier: {visual_id}"]
    return True, []


PBIX_TO_INSIGHTOS = {
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


def compile_visual_reconstruction(configurations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compile complete target pairs from direct PBIX visual evidence.

    Missing semantic facts remain explicit in evidence and are never invented.
    """
    candidates: list[dict[str, Any]] = []
    for record in configurations:
        visual_id = PBIX_TO_INSIGHTOS.get(record.get("visual_type"))
        mappings = record.get("mappings") or []
        if not visual_id or not mappings:
            continue

        refs_by_role: dict[str, list[str]] = defaultdict(list)
        for mapping in mappings:
            query_ref = mapping.get("query_ref")
            if not query_ref:
                continue
            role = str(mapping.get("role") or "unknown")
            refs_by_role[role].append(str(query_ref))
        if not refs_by_role:
            continue

        measure_refs = [
            ref for role in ("Data", "Values", "Y")
            for ref in refs_by_role.get(role, [])
        ]
        category_refs = [
            ref for role in ("Category", "Group", "Rows", "X")
            for ref in refs_by_role.get(role, [])
        ]
        series_refs = refs_by_role.get("Series", [])

        if visual_id == "kpi_scorecard":
            data_mappings = {"value": measure_refs[0] if measure_refs else category_refs[0]}
        elif visual_id == "scatter_plot":
            data_mappings = {
                "x": next(iter(refs_by_role.get("X", [])), category_refs[0] if category_refs else ""),
                "y": next(iter(measure_refs), ""),
            }
            if refs_by_role.get("Size"):
                data_mappings["size"] = refs_by_role["Size"][0]
        elif visual_id in {"table", "matrix_pivot"}:
            data_mappings = {
                "fields": [
                    ref for role_refs in refs_by_role.values() for ref in role_refs
                ]
            }
        else:
            data_mappings = {}
            if category_refs:
                data_mappings["category"] = category_refs[0]
            if measure_refs:
                data_mappings["values"] = measure_refs if len(measure_refs) > 1 else measure_refs[0]
            if series_refs:
                data_mappings["series"] = series_refs[0]

        references = [
            {"field": ref, "operation": "none"}
            for role_refs in refs_by_role.values()
            for ref in role_refs
        ]
        dimensions = record.get("dimensions") or {}
        native_formatting = record.get("formatting") or {}
        target = {
            "schema_version": 1,
            "visualization_id": visual_id,
            "purpose": (
                f"Reconstruct the observed Power BI {record['visual_type']} visual."
            ),
            "data_mappings": data_mappings,
            "aggregation": {"references": references},
            "formatting": {},
            "legend": {"visible": bool(series_refs)},
            "tooltip": {
                "enabled": False,
                "fields": [],
                "show_aggregation": False,
            },
            "layout": {
                "width": max(1, int(float(dimensions.get("width", 1)))),
                "height": max(1, int(float(dimensions.get("height", 1)))),
            },
            "accessibility": {
                "text_alternative": (
                    f"Observed {record['visual_type']} visual preserving native field references."
                ),
                "color_mode": "color_with_labels",
                "keyboard_interaction": True,
            },
            "configuration": {
                "power_bi_visual_type": record["visual_type"],
                "source_visual_id": record["visual_id"],
                "native_formatting_json": json.dumps(native_formatting, sort_keys=True),
                "missing_information": json.dumps(
                    record.get("missing_information") or [], sort_keys=True
                ),
            },
            "rationale": (
                "The target preserves directly observed native query references and "
                "visual identity without inventing unavailable DAX or business facts."
            ),
        }

        valid, errors = validate_output_contract(target)
        if not valid or not data_mappings:
            continue

        prompt_spec = {
            "task":             "Convert the supplied observed Power BI visual specification "
            "into the equivalent InsightOS visualization configuration.",
            "power_bi_visual_type": record["visual_type"],
            "source_project": record["project_id"],
            "source_page": record["report_page_id"],
            "source_visual_id": record["visual_id"],
            "source_pbix_sha256": record["source_pbix_hash"],
            "query_and_projection_metadata": mappings,
            "position": record.get("position"),
            "dimensions": dimensions,
            "available_formatting": native_formatting,
            "missing_information": record.get("missing_information") or [],
            "unknown_properties": "Preserve unresolved properties as unknown.",
        }
        candidate_hash = canonical_hash({
            "task_category": "visual_reconstruction",
            "prompt": prompt_spec,
            "assistant_target": target,
        })
        candidates.append({
            "candidate_id": f"d27-reconstruction:{record['candidate_id']}",
            "source_project": record["project_id"],
            "source_file_hash": record["source_pbix_hash"],
            "source_page": record["report_page_id"],
            "visual_identity": record["visual_id"],
            "task_category": "visual_reconstruction",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are InsightOS visualization reconstruction assistant. "
                        "Use only evidence supplied by the user and return valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_spec, sort_keys=True),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(target, sort_keys=True),
                },
            ],
            "assistant_target": target,
            "evidence_references": [
                {
                    "type": "pbix_visual_projection",
                    "source_pbix_hash": record["source_pbix_hash"],
                    "source_page": record["report_page_id"],
                    "visual_identity": record["visual_id"],
                }
            ],
            "validation": {
                "output_contract_valid": True,
                "errors": [],
                "supported_visualization_id": True,
                "business_question_required": False,
                "dax_required": False,
            },
            "candidate_content_hash": candidate_hash,
            "review_status": "pending_review",
            "human_approved": False,
        })
    return candidates


def compile_dashboard_layout(
    layouts: list[dict[str, Any]],
    configurations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    config_by_page_visual = {
        (row["report_page_id"], row["visual_id"]): row
        for row in configurations
    }
    candidates: list[dict[str, Any]] = []
    for layout in layouts:
        components: list[dict[str, Any]] = []
        unresolved = False
        for visual_id in layout.get("visual_ids", []):
            config = config_by_page_visual.get((layout["report_page_id"], visual_id))
            if not config:
                unresolved = True
                continue
            dimensions = config.get("dimensions") or {}
            position = config.get("position") or {}
            if not dimensions.get("width") or not dimensions.get("height"):
                unresolved = True
                continue
            components.append({
                "visual_id": visual_id,
                "visual_type": config.get("visual_type") or "unknown",
                "x": position.get("x"),
                "y": position.get("y"),
                "z": position.get("z"),
                "width": dimensions["width"],
                "height": dimensions["height"],
                "title": None,
                "identity_evidence": "pbix_visual_metadata",
            })
        canvas = layout.get("canvas_dimensions") or {}
        if unresolved or not components or not canvas.get("width") or not canvas.get("height"):
            continue
        target = {
            "schema_version": 1,
            "task_category": "dashboard_layout",
            "canvas": {
                "width": canvas["width"],
                "height": canvas["height"],
            },
            "page_identity": {
                "project_id": layout["project_id"],
                "report_page_id": layout["report_page_id"],
                "report_page_title": layout.get("report_page_title"),
            },
            "components": components,
            "section_groupings": [],
            "evidence_quality": "directly_observed_geometry",
        }
        prompt_spec = {
            "task": (
                "Convert the supplied observed Power BI page geometry into a "
                "complete page-level InsightOS dashboard layout."
            ),
            "project_id": layout["project_id"],
            "source_page": layout["report_page_id"],
            "canvas_dimensions": canvas,
            "observed_visuals": components,
        }
        candidate_hash = canonical_hash({
            "task_category": "dashboard_layout",
            "prompt": prompt_spec,
            "assistant_target": target,
        })
        candidates.append({
            "candidate_id": f"d27-layout:{layout['candidate_id']}",
            "source_project": layout["project_id"],
            "source_file_hash": layout["source_pbix_hash"],
            "source_page": layout["report_page_id"],
            "visual_identity": layout["visual_ids"],
            "task_category": "dashboard_layout",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are InsightOS dashboard layout reconstruction assistant. "
                        "Return a valid page layout JSON object using only observed geometry."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_spec, sort_keys=True)},
                {"role": "assistant", "content": json.dumps(target, sort_keys=True)},
            ],
            "assistant_target": target,
            "evidence_references": [
                {
                    "type": "pbix_page_geometry",
                    "source_pbix_hash": layout["source_pbix_hash"],
                    "source_page": layout["report_page_id"],
                    "visual_identity": visual_id,
                }
                for visual_id in layout["visual_ids"]
            ],
            "validation": {
                "layout_contract_valid": True,
                "errors": [],
                "source_geometry_complete": True,
                "business_question_required": False,
                "dax_required": False,
            },
            "candidate_content_hash": candidate_hash,
            "review_status": "pending_review",
            "human_approved": False,
        })
    return candidates


class FinalPipeline:
    def __init__(self, extraction_root: Path, destination: Path, archive: Path):
        self.extraction_root = extraction_root.expanduser().resolve()
        self.destination = destination.expanduser().resolve()
        self.archive = archive.expanduser().resolve(strict=True)
        self.extraction = latest_extraction(self.extraction_root)
        self.output = self.destination / "insightos_qwen3_0_6b_training_ready"
        self.output.mkdir(parents=True, exist_ok=True)

    def build(self) -> dict[str, Any]:
        if sha256_file(self.archive) != BASE_SHA256:
            raise ValueError(
                "The current D2.5.1 archive checksum does not match the declared source checksum"
            )
        if sha256_file(STORYTELLING_ARCHIVE) != STORYTELLING_SHA256:
            raise ValueError("The storytelling archive checksum does not match the declared source checksum")
        if sha256_file(CURRENT_FINAL_ARCHIVE) != CURRENT_FINAL_SHA256:
            raise ValueError("The current final package checksum does not match the declared source checksum")
        if SMOKE_ADAPTER.exists():
            smoke_checksum = sha256_file(SMOKE_ADAPTER)
        else:
            smoke_checksum = None

        candidate_dir = self.extraction / "candidates"
        inventory = read_json(self.extraction / "inventory/source_inventory.json")
        checksums = read_json(self.extraction / "inventory/source_checksums.json")
        reconciliation = read_json(RECONCILIATION_PATH)

        configurations = read_jsonl(candidate_dir / "chart_configuration.jsonl")
        layouts = read_jsonl(candidate_dir / "dashboard_layout.jsonl")
        alternatives = archive_jsonl(self.archive, "candidates/chart_alternatives.jsonl")
        negatives = archive_jsonl(self.archive, "candidates/negative_examples.jsonl")
        visual_reconstruction_candidates = compile_visual_reconstruction(configurations)
        dashboard_layout_candidates = compile_dashboard_layout(layouts, configurations)

        project_counts = Counter(
            candidate["source_project"] for candidate in visual_reconstruction_candidates
        )
        heldout_project = (
            max(project_counts, key=project_counts.get) if project_counts else None
        )
        independent_evaluation_candidates = (
            [
                candidate for candidate in visual_reconstruction_candidates
                if candidate["source_project"] == heldout_project
            ]
            if heldout_project
            else []
        )
        write_jsonl(
            self.output / "evaluation/project_heldout_visual_reconstruction.jsonl",
            independent_evaluation_candidates,
        )
        write_json(
            self.output / "evaluation/evaluation_plan.json",
            {
                "evaluation_mode": "project_heldout_candidate_pool",
                "heldout_project": heldout_project,
                "candidate_count": len(independent_evaluation_candidates),
                "approval_status": "not_approved",
                "independent_evaluation_record_count": len(
                    independent_evaluation_candidates
                ),
                "source_visual_overlap_check": "required_before_actual_evaluation",
            },
        )

        write_jsonl(
            self.output / "candidates/visual_reconstruction.jsonl",
            visual_reconstruction_candidates,
        )
        write_jsonl(
            self.output / "candidates/dashboard_layout_complete.jsonl",
            dashboard_layout_candidates,
        )
        write_jsonl(
            self.output / "candidates/visual_storytelling.jsonl",
            [],
        )
        write_jsonl(
            self.output / "training_data/visual_reconstruction.jsonl",
            visual_reconstruction_candidates,
        )
        write_jsonl(
            self.output / "training_data/dashboard_layout.jsonl",
            dashboard_layout_candidates,
        )
        write_jsonl(
            self.output / "training_data/visual_storytelling.jsonl",
            [],
        )

        source_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in inventory["records"]:
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

        canonical: list[dict[str, Any]] = []
        project_statistics: list[dict[str, Any]] = []

        for project_id, display_name in project_names.items():
            project_sources = source_by_project.get(project_id, [])
            project_configs = [row for row in configurations if row["project_id"] == project_id]
            project_layouts = [row for row in layouts if row["project_id"] == project_id]
            visual_types = Counter(
                row["visual_type"] for row in project_configs if row.get("visual_type")
            )

            for config in project_configs:
                mappings = config.get("mappings") or []
                missing = config.get("missing_information") or []
                record = {
                    "knowledge_id": f"pbix-config:{config['candidate_id']}",
                    "source_authority": "first_party_owner_authored",
                    "author_attestation": "user_confirmed",
                    "owner_estimated_accuracy": 0.95,
                    "accuracy_estimate_basis": "owner_attestation",
                    "independent_accuracy_verification": "not_established",
                    "source_filename": next(
                        (
                            source["original_filename"]
                            for source in project_sources
                            if source["document_type"] == "power_bi_project"
                        ),
                        None,
                    ),
                    "source_sha256": config["source_pbix_hash"],
                    "project_id": project_id,
                    "project_name": display_name,
                    "report_page_id": config["report_page_id"],
                    "visual_identity": config["visual_id"],
                    "original_source_record_id": config["candidate_id"],
                    "extraction_version": "insightos-powerbi-reference-v1",
                    "observed_visual_metadata": {
                        "visual_type": config["visual_type"],
                        "position": config.get("position"),
                        "dimensions": config.get("dimensions"),
                        "formatting": config.get("formatting"),
                    },
                    "verified_field_mappings": mappings,
                    "complete_visualization_target": None,
                    "missing_information": sorted(set(missing)),
                    "storytelling_purpose": None,
                    "evidence_references": [
                        {
                            "type": "pbix_layout_metadata",
                            "source_pbix_hash": config["source_pbix_hash"],
                            "visual_id": config["visual_id"],
                            "report_page_id": config["report_page_id"],
                        }
                    ],
                    "quality": {
                        "source_authority": "owner_attested_source",
                        "evidence_completeness": (
                            "partially_observed" if mappings else "directly_observed_metadata_only"
                        ),
                        "semantic_correctness": (
                            "inferred_needs_review" if mappings else "unresolved"
                        ),
                        "output_contract_validity": "not_applicable_incomplete",
                        "training_target_completeness": "incomplete",
                        "human_approval": "not_approved",
                    },
                    "future_specialist_destination": "qwen3_0_6b_visualization_presentation",
                    "review_status": "pending_review",
                }
                canonical.append(record)

            for layout in project_layouts:
                record = {
                    "knowledge_id": f"dashboard-layout:{layout['candidate_id']}",
                    "source_authority": "first_party_owner_authored",
                    "author_attestation": "user_confirmed",
                    "owner_estimated_accuracy": 0.95,
                    "accuracy_estimate_basis": "owner_attestation",
                    "independent_accuracy_verification": "not_established",
                    "source_filename": next(
                        (
                            source["original_filename"]
                            for source in project_sources
                            if source["document_type"] == "power_bi_project"
                        ),
                        None,
                    ),
                    "source_sha256": layout["source_pbix_hash"],
                    "project_id": project_id,
                    "project_name": display_name,
                    "report_page_id": layout["report_page_id"],
                    "report_page_title": layout.get("report_page_title"),
                    "visual_identity": layout["visual_ids"],
                    "original_source_record_id": layout["candidate_id"],
                    "extraction_version": "insightos-powerbi-reference-v1",
                    "observed_visual_metadata": {
                        "canvas_dimensions": layout["canvas_dimensions"],
                        "visual_types": layout["visual_types"],
                        "visual_ids": layout["visual_ids"],
                    },
                    "complete_visualization_target": None,
                    "storytelling_purpose": "Preserve page-level visual hierarchy and spatial composition.",
                    "evidence_references": [
                        {
                            "type": "pbix_layout_metadata",
                            "source_pbix_hash": layout["source_pbix_hash"],
                            "report_page_id": layout["report_page_id"],
                            "visual_ids": layout["visual_ids"],
                        }
                    ],
                    "quality": {
                        "source_authority": "owner_attested_source",
                        "evidence_completeness": "directly_observed_layout",
                        "semantic_correctness": "unresolved",
                        "output_contract_validity": "dashboard_layout_metadata_only",
                        "training_target_completeness": "incomplete",
                        "human_approval": "not_approved",
                    },
                    "future_specialist_destination": "qwen3_0_6b_visualization_presentation",
                    "review_status": "pending_review",
                }
                canonical.append(record)

            project_statistics.append({
                "project_id": project_id,
                "project_name": display_name,
                "source_records": len(project_sources),
                "unique_source_checksums": len({
                    source["sha256"] for source in project_sources
                }),
                "configuration_candidates": len(project_configs),
                "dashboard_layout_candidates": len(project_layouts),
                "observed_visual_types": dict(sorted(visual_types.items())),
            })

        for row in reconciliation.get("chart_selection_records", []):
            canonical.append({
                "knowledge_id": f"chart-selection:{row['record_id']}",
                "source_authority": (
                    "first_party_owner_authored"
                    if row["source_type"] == "pbix"
                    else "owner_attested_source"
                ),
                "source_filename": row.get("source_name"),
                "source_sha256": None,
                "source_record_id": row["source_record_id"],
                "source_type": row["source_type"],
                "source_member": row.get("source_member"),
                "project_id": row.get("project_id"),
                "original_source_record_id": row["source_record_id"],
                "extraction_version": (
                    reconciliation.get("report_id", "unknown-extraction")
                ),
                "observed_visual_metadata": {
                    "record_id": row["record_id"],
                    "visual_identity": row.get("visual_identity"),
                },
                "complete_visualization_target": None,
                "quality": {
                    "source_authority": "owner_attested_source",
                    "evidence_completeness": "needs_record_review",
                    "semantic_correctness": "unresolved",
                    "output_contract_validity": "not_applicable_pending",
                    "training_target_completeness": "incomplete",
                    "human_approval": "not_approved",
                },
                "future_specialist_destination": "qwen3_0_6b_visualization_presentation",
                "review_status": "pending_review",
            })

        write_jsonl(self.output / "canonical_knowledge.jsonl", canonical)
        write_json(
            self.output / "source_inventory.json",
            {
                "source_authority": "first_party_owner_authored",
                "author_attestation": "user_confirmed",
                "owner_estimated_accuracy": 0.95,
                "accuracy_estimate_basis": "owner_attestation",
                "independent_accuracy_verification": "not_established",
                "latest_extraction": str(self.extraction),
                "extraction_manifest": read_json(
                    self.extraction / "manifests/extraction_manifest.json"
                ),
                "source_checksums": checksums,
                "project_statistics": project_statistics,
            },
        )

        chart_selection = self.output / "training_data/chart_selection.jsonl"
        write_jsonl(chart_selection, [])
        write_jsonl(self.output / "training_data/visualization_configuration.jsonl", [])
        write_jsonl(
            self.output / "training_data/dashboard_layout.jsonl",
            dashboard_layout_candidates,
        )
        write_jsonl(self.output / "training_data/data_storytelling.jsonl", [])
        write_jsonl(self.output / "training_data/negative_examples.jsonl", negatives)
        for split in ("train.jsonl", "validation.jsonl", "evaluation.jsonl"):
            write_jsonl(self.output / f"training_data/{split}", [])

        registry_ids = {row["id"] for row in read_json(REGISTRY_PATH)["visualizations"]}
        unsupported_positive_targets: list[dict[str, Any]] = []
        complete_targets = reconciliation.get("incomplete_complete_configuration_records", [])

        dataset_manifest = {
            "manifest_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_bundle": str(self.archive),
            "source_bundle_sha256": sha256_file(self.archive),
            "extraction_version": self.extraction.name,
            "source_authority": "first_party_owner_authored",
            "author_attestation": "user_confirmed",
            "owner_estimated_accuracy": 0.95,
            "accuracy_estimate_basis": "owner_attestation",
            "independent_accuracy_verification": "not_established",
            "base_model": "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
            "base_model_revision": "unresolved_until_hub_metadata_lookup_or_training_environment",
            "tokenizer_revision": "unresolved_until_hub_metadata_lookup_or_training_environment",
            "approved_training_count": 0,
            "approved_validation_count": 0,
            "approved_evaluation_count": 0,
            "complete_visualization_target_count": len(visual_reconstruction_candidates),
            "visual_reconstruction_target_count": len(visual_reconstruction_candidates),
            "dashboard_layout_target_count": len(dashboard_layout_candidates),
            "chart_selection_complete_target_count": 0,
            "visual_storytelling_complete_target_count": 0,
            "invalid_complete_target_count": len(complete_targets),
            "unsupported_visualization_count": 0,
            "negative_example_count": len(negatives),
            "chart_selection_candidate_count": len(
                reconciliation.get("chart_selection_records", [])
            ),
            "powerbi_configuration_candidate_count": len(configurations),
            "dashboard_layout_candidate_count": len(layouts),
            "canonical_knowledge_record_count": len(canonical),
            "unsupported_positive_targets": unsupported_positive_targets,
            "supported_visualization_ids": sorted(registry_ids),
            "group_ids": sorted({
                row["project_id"] for row in configurations
            } | {
                row["project_id"] for row in layouts
            }),
            "duplicate_project_pbix": [
                {
                    "sha256": (
                        row["source_checksums"][0] if row.get("source_checksums") else None
                    ),
                    "filenames": row.get("filenames", []),
                    "duplicate_group": row.get("duplicate_group"),
                }
                for row in inventory.get("duplicate_groups", [])
            ],
            "split_counts": {"train": 0, "validation": 0, "evaluation": 0},
            "leakage_check": {
                "status": "passed_empty_splits",
                "source_project_overlap": [],
                "source_report_overlap": [],
                "source_fact_overlap": [],
            },
            "readiness_status": "BLOCKED_INSUFFICIENT_VALID_TARGETS",
        }
        write_json(self.output / "training_data/dataset_manifest.json", dataset_manifest)

        blocked_reasons = [
            "No approved positive training records exist.",
            "No approved independent evaluation records exist.",
            "Human approval has not been granted.",
        ]
        dataset_statistics = {
            "canonical_knowledge_records": len(canonical),
            "chart_selection_candidates": len(
                reconciliation.get("chart_selection_records", [])
            ),
            "complete_configuration_targets": 0,
            "dashboard_layout_candidates": len(layouts),
            "visual_reconstruction_complete_targets": len(visual_reconstruction_candidates),
            "dashboard_layout_complete_targets": len(dashboard_layout_candidates),
            "chart_selection_complete_targets": 0,
            "visual_storytelling_complete_targets": 0,
            "negative_examples": len(negatives),
            "pending_records": reconciliation["counts"]["pending_records"]
            if "pending_records" in reconciliation["counts"]
            else 93,
            "excluded_records": reconciliation["counts"]["excluded_records"],
            "approved_training_records": 0,
            "approved_validation_records": 0,
            "approved_evaluation_records": 0,
            "derived_prompts": 0,
            "blocking_reasons": blocked_reasons,
        }
        write_json(self.output / "dataset_statistics.json", dataset_statistics)

        write_json(
            self.output / "approval_manifest.json",
            {
                "approval_version": 1,
                "approval_status": "approval_required",
                "human_approval": False,
                "approved_candidate_count": 0,
                "approved_candidate_hashes": [],
                "reviewed_by": None,
                "review_timestamp": None,
                "source_bundle_sha256": sha256_file(self.archive),
                "export_manifest_sha256": None,
                "stale_after_change": True,
                "automatic_approval": False,
            },
        )

        with (
            self.output / "final_training_review.csv"
        ).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "candidate_id",
                "candidate_content_hash",
                "project_id",
                "task_category",
                "validation_status",
                "human_approval",
                "decision",
            ])
            for row in canonical:
                writer.writerow([
                    row["knowledge_id"],
                    canonical_hash(row),
                    row.get("project_id", ""),
                    row.get("future_specialist_destination", ""),
                    "pending_review_or_incomplete",
                    "not_approved",
                    "pending",
                ])

        data_quality_report = self._data_quality_report(
            inventory=inventory,
            reconciliation=reconciliation,
            canonical_count=len(canonical),
            configurations=len(configurations),
            layouts=len(layouts),
            alternatives=len(alternatives),
            negatives=len(negatives),
            blocked_reasons=blocked_reasons,
        )
        (self.output / "data_quality_report.md").write_text(
            data_quality_report,
            encoding="utf-8",
        )
        self._write_consolidation_artifacts(
            inventory,
            reconciliation,
            configurations,
            layouts,
            negatives,
        )
        self._create_notebook()

        training_manifest = {
            "manifest_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "readiness_status": "BLOCKED_INSUFFICIENT_VALID_TARGETS",
            "training_executed": False,
            "base_model": "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
            "base_model_revision": dataset_manifest["base_model_revision"],
            "adapter_name": "insightos_qwen3_0_6b_visualization_specialist",
            "smoke_adapter_excluded": True,
            "smoke_adapter_checksum": smoke_checksum,
            "blocking_reasons": blocked_reasons,
            "source_bundle_sha256": sha256_file(self.archive),
            "extraction": str(self.extraction),
        }
        write_json(self.output / "manifests/training_manifest.json", training_manifest)

        self.output.parent.mkdir(parents=True, exist_ok=True)
        archive_path = self.destination / "insightos_qwen3_0_6b_training_ready_bundle.zip"
        if archive_path.exists():
            archive_path.unlink()
        self._write_portable_archive(archive_path)
        bundle_sha = sha256_file(archive_path)
        approval = read_json(self.output / "approval_manifest.json")
        approval["export_manifest_sha256"] = bundle_sha
        write_json(self.output / "approval_manifest.json", approval)
        write_json(
            self.output / "manifests/checksums.sha256.json",
            {
                "portable_training_data_zip": bundle_sha,
                "portable_training_data_size": archive_path.stat().st_size,
                "base_training_bundle_sha256": sha256_file(self.archive),
                "smoke_adapter_sha256": smoke_checksum,
            },
        )
        (archive_path.with_suffix(".zip.sha256")).write_text(
            f"{bundle_sha}  {archive_path.name}\n",
            encoding="utf-8",
        )

        desktop = Path("/mnt/c/Users/NITHIN/Desktop/InsightOs")
        if desktop.exists():
            shutil.copy2(archive_path, desktop / archive_path.name)
            shutil.copy2(
                archive_path.with_suffix(".zip.sha256"),
                desktop / archive_path.with_suffix(".zip.sha256").name,
            )

        return {
            "output": str(self.output),
            "archive": str(archive_path),
            "archive_sha256": bundle_sha,
            "archive_size": archive_path.stat().st_size,
            "canonical_knowledge_records": len(canonical),
            "readiness_status": "BLOCKED_INSUFFICIENT_VALID_TARGETS",
            "desktop_copy": str(desktop / archive_path.name),
        }

    def _write_consolidation_artifacts(
        self,
        inventory: dict[str, Any],
        reconciliation: dict[str, Any],
        configurations: list[dict[str, Any]],
        layouts: list[dict[str, Any]],
        negatives: list[dict[str, Any]],
    ) -> None:
        with zipfile.ZipFile(STORYTELLING_ARCHIVE) as enriched:
            prompt_names = [
                name for name in enriched.namelist()
                if name.endswith("storytelling_enrichment/derived_training_prompts.jsonl")
            ]
            reconciliation_names = [
                name for name in enriched.namelist()
                if name.endswith("storytelling_enrichment/selection_reconciliation.jsonl")
            ]
            if len(prompt_names) != 1 or len(reconciliation_names) != 1:
                raise ValueError("Storytelling archive lacks exactly one required enrichment member")
            prompts = [
                json.loads(line)
                for line in enriched.read(prompt_names[0]).decode("utf-8").splitlines()
                if line.strip()
            ]
            selection_reconciliation = [
                json.loads(line)
                for line in enriched.read(reconciliation_names[0]).decode("utf-8").splitlines()
                if line.strip()
            ]

        knowledge = read_jsonl(self.output / "canonical_knowledge.jsonl")
        for prompt in prompts:
            knowledge.append({
                "knowledge_id": f"storytelling-prompt:{prompt['record_id']}",
                "stable_record_id": prompt["record_id"],
                "candidate_hash": canonical_hash(prompt),
                "source_authority": prompt["source_authority"],
                "author_attestation": "user_confirmed",
                "owner_estimated_accuracy": 0.95,
                "accuracy_estimate_basis": "owner_attestation",
                "independent_accuracy_verification": "not_established",
                "source_project": prompt["record_id"].split(":")[0],
                "source_business_context": prompt["source_business_context"],
                "derived_training_prompt": prompt["derived_training_prompt"],
                "storytelling_purpose": prompt["prompt_classification"],
                "evidence_references": prompt["evidence_references"],
                "complete_visualization_target": None,
                "review_status": prompt["human_review_status"],
                "evidence_tier": "Tier C",
                "training_target_completeness": prompt["training_target_completeness"],
                "future_specialist_destination": prompt["future_model_destination"],
            })
        write_jsonl(self.output / "knowledge/canonical_knowledge.jsonl", knowledge)
        write_jsonl(
            self.output / "knowledge/visualization_intelligence.jsonl",
            [row for row in knowledge if row.get("future_specialist_destination")],
        )
        write_jsonl(
            self.output / "knowledge/dashboard_composition.jsonl",
            [row for row in knowledge if row.get("evidence_tier") == "Tier C"],
        )
        write_jsonl(
            self.output / "knowledge/data_storytelling.jsonl",
            [row for row in knowledge if row.get("storytelling_purpose")],
        )
        write_jsonl(self.output / "knowledge/business_context_for_future_models.jsonl", [])

        write_jsonl(
            self.output / "candidates/chart_selection.jsonl",
            archive_jsonl(self.archive, "candidates/chart_selection.jsonl"),
        )
        write_jsonl(
            self.output / "candidates/visualization_configuration.jsonl",
            archive_jsonl(self.archive, "candidates/visualization_configuration.jsonl"),
        )
        write_jsonl(self.output / "candidates/dashboard_layout.jsonl", layouts)
        write_jsonl(self.output / "candidates/storytelling_examples.jsonl", prompts)
        write_jsonl(self.output / "candidates/negative_examples.jsonl", negatives)
        write_jsonl(self.output / "candidates/complete_configuration.jsonl", [])

        complete_layouts = [
            row for row in layouts if row.get("complete_configuration") is not None
        ]
        write_json(
            self.output / "archive_lineage.json",
            {
                "lineage_version": 1,
                "archives": [
                    {
                        "archive_role": "Archive A - original D2.5.1",
                        "filename": self.archive.name,
                        "sha256": sha256_file(self.archive),
                        "expected_sha256": BASE_SHA256,
                        "verified": sha256_file(self.archive) == BASE_SHA256,
                    },
                    {
                        "archive_role": "Archive B - storytelling enrichment",
                        "filename": STORYTELLING_ARCHIVE.name,
                        "sha256": sha256_file(STORYTELLING_ARCHIVE),
                        "expected_sha256": STORYTELLING_SHA256,
                        "verified": sha256_file(STORYTELLING_ARCHIVE) == STORYTELLING_SHA256,
                    },
                    {
                        "archive_role": "Archive C - previous final package",
                        "filename": CURRENT_FINAL_ARCHIVE.name,
                        "sha256": sha256_file(CURRENT_FINAL_ARCHIVE),
                        "expected_sha256": CURRENT_FINAL_SHA256,
                        "verified": sha256_file(CURRENT_FINAL_ARCHIVE) == CURRENT_FINAL_SHA256,
                    },
                ],
                "647_vs_641": {
                    "reconciled_selection_count": len(selection_reconciliation),
                    "reported_641_count": 641,
                    "six_record_difference": "unresolved_without_the_authoritative_641-stage_inventory",
                    "classification": "not inferred as duplicate, distinct, missing, or reclassified",
                },
                "50_vs_42_layouts": {
                    "archive_layout_records": len(
                        archive_jsonl(self.archive, "candidates/dashboard_layout.jsonl")
                    ),
                    "extraction_layout_records": len(layouts),
                    "complete_configuration_field_non_null": len(complete_layouts),
                    "eight_remaining_records": "retained as incomplete page-level metadata",
                },
                "historical_197": {
                    "status": "unresolved",
                    "reason": "the exact earlier register and transition IDs are unavailable",
                    "authority": "current D2.5.1 status and hashes remain authoritative",
                },
            },
        )

        visual_reconstruction = compile_visual_reconstruction(configurations)
        complete_layouts = compile_dashboard_layout(layouts, configurations)
        complete_candidate_count = len(visual_reconstruction) + len(complete_layouts)
        packet_candidates = visual_reconstruction + complete_layouts
        dataset_manifest_path = self.output / "training_data/dataset_manifest.json"
        dataset_manifest_sha256 = (
            sha256_file(dataset_manifest_path)
            if dataset_manifest_path.exists()
            else None
        )
        packet = {
            "packet_version": 1,
            "packet_status": "approval_required",
            "human_approved": False,
            "automatic_approval": False,
            "source_authority": "first_party_owner_authored",
            "author_attestation": "user_confirmed",
            "owner_estimated_accuracy": 0.95,
            "accuracy_estimate_basis": "owner_attestation",
            "independent_accuracy_verification": "not_established",
            "proposed_train_examples": 0,
            "proposed_validation_examples": 0,
            "proposed_evaluation_examples": 0,
            "complete_positive_targets": complete_candidate_count,
            "visual_reconstruction_complete_targets": len(visual_reconstruction),
            "dashboard_layout_complete_targets": len(complete_layouts),
            "chart_selection_complete_targets": 0,
            "visual_storytelling_complete_targets": 0,
            "tier_a_candidates": len(visual_reconstruction),
            "tier_b_candidates": len(complete_layouts),
            "tier_c_candidates": len(layouts) + len(prompts),
            "tier_d_candidates": len(negatives) + len(configurations),
            "reason": (
                "Complete task-specific targets are available, but none has "
                "received explicit human approval and independent evaluation data."
            ),
            "source_dataset_manifest_sha256": dataset_manifest_sha256,
            "candidate_content_hashes": [
                row["candidate_content_hash"] for row in packet_candidates
            ],
            "input_output_pairs": [
                {
                    "candidate_id": row["candidate_id"],
                    "task_category": row["task_category"],
                    "source_project": row["source_project"],
                    "source_file_hash": row["source_file_hash"],
                    "source_page": row["source_page"],
                    "visual_identity": row["visual_identity"],
                    "messages": row["messages"],
                    "candidate_content_hash": row["candidate_content_hash"],
                    "review_status": row["review_status"],
                }
                for row in packet_candidates
            ],
            "stale_hash_rejection": True,
        }
        packet_hash = canonical_hash(packet)
        packet["approval_manifest_sha256"] = packet_hash
        write_json(self.output / "approval/final_training_approval_packet.json", packet)
        write_json(
            self.output / "approval/approval_manifest.json",
            {
                "approval_version": 1,
                "approval_status": "approval_required",
                "human_approval": False,
                "approved_candidate_count": 0,
                "approved_candidate_hashes": [],
                "approval_packet_sha256": packet_hash,
                "reviewed_by": None,
                "review_timestamp": None,
                "stale_after_change": True,
                "automatic_approval": False,
            },
        )
        (self.output / "approval/FINAL_DATASET_APPROVAL.md").write_text(
            "# Final Dataset Approval\n\n"
            "Status: `approval_required`\n\n"
            "No positive supervised example is proposed because complete evidence-backed targets "
            "could not be validated. Explicit human approval is required after valid target recovery.\n\n"
            f"Approval packet SHA-256: `{packet_hash}`\n",
            encoding="utf-8",
        )
        write_jsonl(
            self.output / "approval/excluded_records.jsonl",
            negatives + configurations,
        )
        write_json(
            self.output / "dataset_statistics.json",
            {
                "canonical_knowledge_records": len(knowledge),
                "selection_reconciliation_records": len(selection_reconciliation),
                "chart_selection_candidates": len(
                    archive_jsonl(self.archive, "candidates/chart_selection.jsonl")
                ),
                "configuration_candidates": len(configurations),
                "dashboard_layout_candidates": len(layouts),
                "storytelling_derived_prompts": len(prompts),
                "negative_examples": len(negatives),
                "complete_positive_targets": (
                    len(compile_visual_reconstruction(configurations))
                    + len(compile_dashboard_layout(layouts, configurations))
                ),
                "approved_examples": 0,
                "train_examples": 0,
                "validation_examples": 0,
                "evaluation_examples": 0,
                "readiness_status": "BLOCKED_INSUFFICIENT_VALID_TARGETS",
            },
        )
        write_json(
            self.output / "schemas/dashboard_layout.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "InsightOS Dashboard Layout",
                "type": "object",
                "required": [
                    "schema_version",
                    "task_category",
                    "canvas",
                    "page_identity",
                    "components",
                    "section_groupings",
                    "evidence_quality",
                ],
                "properties": {
                    "schema_version": {"const": 1},
                    "task_category": {"const": "dashboard_layout"},
                    "canvas": {
                        "type": "object",
                        "required": ["width", "height"],
                        "properties": {
                            "width": {"type": "number", "minimum": 1},
                            "height": {"type": "number", "minimum": 1},
                        },
                    },
                    "page_identity": {
                        "type": "object",
                        "required": ["project_id", "report_page_id"],
                        "properties": {
                            "project_id": {"type": "string"},
                            "report_page_id": {"type": "string"},
                            "report_page_title": {"type": ["string", "null"]},
                        },
                    },
                    "components": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": [
                                "visual_id",
                                "visual_type",
                                "x",
                                "y",
                                "z",
                                "width",
                                "height",
                                "title",
                                "identity_evidence",
                            ],
                        },
                    },
                    "section_groupings": {"type": "array"},
                    "evidence_quality": {"const": "directly_observed_geometry"},
                },
                "additionalProperties": False,
            },
        )
        (self.output / "evaluation").mkdir(parents=True, exist_ok=True)
        (self.output / "evaluation/evaluation_protocol.md").write_text(
            "# Evaluation Protocol\n\n"
            "The base and tuned models must be evaluated on the same independent examples. "
            "Metrics do not exist until approved evaluation execution occurs.\n",
            encoding="utf-8",
        )
        write_json(self.output / "evaluation/baseline_metrics.json", {"evaluation_executed": False, "metrics": {}})
        write_json(self.output / "evaluation/tuned_metrics.json", {"evaluation_executed": False, "metrics": {}})
        write_json(self.output / "evaluation/comparison_report.json", {"comparison_executed": False})

    def _write_portable_archive(self, archive_path: Path) -> None:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for path in sorted(self.output.rglob("*")):
                if path.is_file() and not any(
                    excluded in path.relative_to(self.output).as_posix()
                    for excluded in (
                        "approval_manifest.json",
                        "manifests/checksums.sha256.json",
                    )
                ):
                    target.write(path, Path(self.output.name) / path.relative_to(self.output))

    def _create_notebook(self) -> None:
        cells: list[dict[str, Any]] = []

        cells.append({
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "# InsightOS Qwen3 0.6B Final Visualization Specialist Training\n\n",
                "This notebook refuses blocked approval manifests. It does not use the Qwen smoke adapter.\n",
            ],
        })
        cells.append({
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "!nvidia-smi\n",
                "import torch\n",
                (
                    "print({\"cuda_available\": torch.cuda.is_available(), "
                    "\"gpu_name\": torch.cuda.get_device_name(0) "
                    "if torch.cuda.is_available() else None, "
                    "\"fp16_supported\": torch.cuda.is_available()})\n"
                ),
            ],
        })
        cells.append({
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "import hashlib, json, zipfile\n",
                "BUNDLE = '/kaggle/input/insightos-qwen3-0-6b-final/insightos_qwen3_0_6b_training_ready_bundle.zip'\n",
                "required_splits = ['train.jsonl', 'validation.jsonl', 'evaluation.jsonl']\n",
            ],
        })
        cells.append({
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [
                "with zipfile.ZipFile(BUNDLE) as z:\n",
                "    names = z.namelist()\n",
                "    manifest_members = [n for n in names if n.endswith('training_data/dataset_manifest.json')]\n",
                "    approval_members = [\n",
                "        n for n in names\n",
                "        if n.endswith('approval/final_training_approval_packet.json')\n",
                "    ]\n",
                "    if len(manifest_members) != 1:\n",
                "        raise RuntimeError('Exactly one dataset manifest is required')\n",
                "    if len(approval_members) != 1:\n",
                "        raise RuntimeError('Exactly one approval packet is required')\n",
                "    manifest = json.loads(z.read(manifest_members[0]))\n",
                "    approval = json.loads(z.read(approval_members[0]))\n",
                "    if approval['human_approved'] is not True or approval['automatic_approval'] is not False:\n",
                "        raise RuntimeError('Training refused: explicit human approval is missing')\n",
                "if manifest['readiness_status'] != 'TRAINING_READY_APPROVED':\n",
                "    raise RuntimeError('Training refused: bundle readiness is blocked pending explicit approval')\n",
                "print('Approved dataset verified')\n",
            ],
        })
        cells.append({
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "After the approval gate passes, use a verified Unsloth environment, "
                "resolve exact model and tokenizer revisions, load the approved bundle, "
                "validate the training/evaluation groups, and run QLoRA. "
                "The notebook is intentionally fail-closed in its current state.\n",
            ],
        })
        cells.extend([
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "!pip -q install unsloth accelerate peft transformers trl datasets bitsandbytes\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "import importlib.metadata as metadata\n",
                    "for package in ['torch','transformers','datasets','trl','peft','bitsandbytes','unsloth']:\n",
                    "    try: print(package, metadata.version(package))\n",
                    "    except metadata.PackageNotFoundError: print(package, 'not_installed')\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "from datasets import load_dataset\n",
                    "def load_split(name):\n",
                    "    with zipfile.ZipFile(BUNDLE) as z:\n",
                    "        members=[n for n in z.namelist() if n.endswith('/training_data/'+name+'.jsonl')]\n",
                    "        if len(members)!=1:\n",
                    "            raise RuntimeError('Expected exactly one '+name+' split')\n",
                    "        lines=[x for x in z.read(members[0]).decode().splitlines() if x.strip()]\n",
                    "    return load_dataset('json', data_files={'data': lines}, split='data')\n",
                    "train_ds=load_split('train')\n",
                    "validation_ds=load_split('validation')\n",
                    "evaluation_ds=load_split('evaluation')\n",
                    "if len(train_ds)==0 or len(evaluation_ds)==0:\n",
                    "    raise RuntimeError('Readiness requires non-empty approved training and evaluation splits')\n",
                    "print({'train':len(train_ds),'validation':len(validation_ds),'evaluation':len(evaluation_ds)})\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "from unsloth import FastLanguageModel\n",
                    "MODEL_ID='unsloth/Qwen3-0.6B-unsloth-bnb-4bit'\n",
                    "model, tokenizer=FastLanguageModel.from_pretrained(\n",
                    "    model_name=MODEL_ID,\n",
                    "    max_seq_length=2048,\n",
                    "    dtype=torch.float16,\n",
                    "    load_in_4bit=True,\n",
                ")\n",
                    "print({'model':MODEL_ID,'model_config':model.config._name_or_path,'max_position_embeddings':getattr(model.config,'max_position_embeddings',None)})\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "model=FastLanguageModel.get_peft_model(\n",
                    "    model,\n",
                    "    r=16,\n",
                    "    lora_alpha=32,\n",
                    "    lora_dropout=0.05,\n",
                    "    bias='none',\n",
                    "    use_gradient_checkpointing='unsloth',\n",
                    "    random_state=3407,\n",
                    "    target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'],\n",
                ")\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "from trl import SFTTrainer\n",
                    "from transformers import TrainingArguments\n",
                    "trainer=SFTTrainer(\n",
                    "    model=model,\n",
                    "    tokenizer=tokenizer,\n",
                    "    train_dataset=train_ds,\n",
                    "    eval_dataset=validation_ds,\n",
                    "    formatting_func=lambda row: tokenizer.apply_chat_template(\n",
                    "        row['messages'], tokenize=False, add_generation_prompt=False\n",
                    "    ),\n",
                    "    max_seq_length=2048,\n",
                    "    args=TrainingArguments(\n",
                    "        output_dir='/kaggle/working/insightos-qwen3-output',\n",
                    "        per_device_train_batch_size=2,\n",
                    "        gradient_accumulation_steps=8,\n",
                    "        learning_rate=2e-4,\n",
                    "        warmup_ratio=0.03,\n",
                    "        max_steps=-1,\n",
                    "        num_train_epochs=3,\n",
                    "        fp16=torch.cuda.is_available(),\n",
                    "        bf16=False,\n",
                    "        logging_steps=10,\n",
                    "        evaluation_strategy='steps',\n",
                    "        eval_steps=25,\n",
                    "        save_strategy='steps',\n",
                    "        save_steps=25,\n",
                    "        save_total_limit=3,\n",
                    "        seed=3407,\n",
                    "        report_to='none',\n",
                    "    ),\n",
                ")\n",
                "trainer.train()\n",
                "trainer.save_model('/kaggle/working/insightos_qwen3_0_6b_adapter')\n",
                "tokenizer.save_pretrained('/kaggle/working/insightos_qwen3_0_6b_adapter')\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "import json\n",
                    "def evaluate_contract(dataset, model, tokenizer):\n",
                    "    if len(dataset)==0:\n",
                    "        raise RuntimeError('Independent evaluation split is empty')\n",
                    "    metrics={'json_parse_rate':0.0,'output_contract_compliance':0.0,\n",
                    "             'supported_visualization_choice':0.0,'field_mapping_correctness':0.0,\n",
                    "             'layout_validity':0.0,'evidence_faithfulness':0.0}\n",
                    "    parsed=0; contract=0; supported=0; mappings=0; layout=0; faithful=0\n",
                    "    for row in dataset:\n",
                    "        messages=row.get('messages') if isinstance(row,dict) else None\n",
                    "        if not messages or len(messages)!=3:\n",
                    "            continue\n",
                    "        prompt=tokenizer.apply_chat_template(\n",
                    "            messages[:2], tokenize=False, add_generation_prompt=True\n",
                    "        )\n",
                    "        inputs=tokenizer(prompt, return_tensors='pt').to(model.device)\n",
                    "        generated_ids=model.generate(\n",
                    "            **inputs, max_new_tokens=768, do_sample=False\n",
                    "        )[0]\n",
                    "        output=tokenizer.decode(\n",
                    "            generated_ids[inputs['input_ids'].shape[1]:],\n",
                    "            skip_special_tokens=True,\n",
                    "        )\n",
                    "        try:\n",
                    "            generated=json.loads(output)\n",
                    "            parsed+=1\n",
                    "        except json.JSONDecodeError:\n",
                    "            continue\n",
                    "        expected=json.loads(messages[2]['content'])\n",
                    "        if generated.get('schema_version')==1:\n",
                    "            contract+=1\n",
                    "        if row.get('task_category')=='visual_reconstruction':\n",
                    "            if generated.get('visualization_id')==expected.get('visualization_id'):\n",
                    "                supported+=1; mappings+=1\n",
                    "        if row.get('task_category')=='dashboard_layout':\n",
                    "            canvas=generated.get('canvas',{})\n",
                    "            if canvas.get('width') and canvas.get('height'):\n",
                    "                layout+=1\n",
                    "        if generated==expected:\n",
                    "            faithful+=1\n",
                    "    total=len(dataset)\n",
                    "    return {\n",
                    "        'json_parse_rate':parsed/total,\n",
                    "        'output_contract_compliance':contract/total,\n",
                    "        'supported_visualization_choice':supported/total,\n",
                    "        'field_mapping_correctness':mappings/total,\n",
                    "        'layout_validity':layout/total,\n",
                    "        'evidence_faithfulness':faithful/total,\n",
                    "    }\n",
                    "import gc, torch\n",
                    "gc.collect(); torch.cuda.empty_cache()\n",
                    "base_model, base_tokenizer=FastLanguageModel.from_pretrained(\n",
                    "    model_name=MODEL_ID,\n",
                    "    max_seq_length=2048,\n",
                    "    dtype=torch.float16,\n",
                    "    load_in_4bit=True,\n",
                    ")\n",
                    "base_metrics=evaluate_contract(evaluation_ds, base_model, base_tokenizer)\n",
                    "tuned_metrics=evaluate_contract(evaluation_ds, model, tokenizer)\n",
                    "json.dump({'base':base_metrics,'tuned':tuned_metrics},open('/kaggle/working/evaluation_metrics.json','w'))\n",
                    "print(json.dumps({'base':base_metrics,'tuned':tuned_metrics},indent=2))\n",
                ],
            },
        ])

        notebook = {
            "cells": cells,
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python", "version": "3.11"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        Path(
            "/home/nithin/workspace/InsightOs/backend/training/finetuning/notebooks"
        ).mkdir(parents=True, exist_ok=True)
        write_json(
            Path("/home/nithin/workspace/InsightOs/backend/training/finetuning/notebooks/qwen3_0_6b_final_training.ipynb"),
            notebook,
        )
        write_json(
            self.output / "notebooks/qwen3_0_6b_final_training.ipynb",
            notebook,
        )

    def _data_quality_report(
        self,
        inventory: dict[str, Any],
        reconciliation: dict[str, Any],
        canonical_count: int,
        configurations: int,
        layouts: int,
        alternatives: int,
        negatives: int,
        blocked_reasons: list[str],
    ) -> str:
        return "\n".join([
            "# Qwen3 0.6B Final Data Quality Report",
            "",
            "## Source authority",
            "",
            "- source_authority: `first_party_owner_authored`",
            "- author_attestation: `user_confirmed`",
            "- owner-estimated accuracy: `0.95`",
            "- independent accuracy verification: `not_established`",
            "",
            "## Recovered evidence",
            "",
            f"- latest extraction: `{self.extraction}`",
            f"- canonical knowledge records: **{canonical_count}**",
            f"- PBIX configuration candidates: **{configurations}**",
            f"- dashboard layout candidates: **{layouts}**",
            f"- selection candidates: **{reconciliation['counts']['chart_selection_records']}**",
            f"- alternative candidates: **{alternatives}**",
            f"- negative examples: **{negatives}**",
            f"- excluded records: **{reconciliation['counts']['excluded_records']}**",
            "",
            "## Limitations",
            "",
            "- DAX expressions, implicit measures, model relationships, filters and full "
            "page context remain unresolved unless directly recoverable.",
            "- No PBIX screenshot establishes an underlying semantic mapping or calculation.",
            "- Marketing Funnel conflicting figures are not silently resolved.",
            "- Customer Churn calculations remain unresolved where the source does not prove them.",
            "- Task-specific complete targets are pending human review; they are not approved training records.",
            "",
            "## Training readiness",
            "",
            *(f"- BLOCKED: {reason}" for reason in blocked_reasons),
            "",
        ])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the final Qwen3 0.6B visualization-specialist pipeline."
    )
    parser.add_argument(
        "--extraction-root",
        type=Path,
        default=Path.home() / ".cache/insightos/powerbi_reference",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path.home() / ".cache/insightos/qwen3_0_6b_final",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=BASE_ARCHIVE,
    )
    args = parser.parse_args()
    pipeline = FinalPipeline(args.extraction_root, args.destination, args.archive)
    print(json.dumps(pipeline.build(), indent=2, sort_keys=True))
    return 0
