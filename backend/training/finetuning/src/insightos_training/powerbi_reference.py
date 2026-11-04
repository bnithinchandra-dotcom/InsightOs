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

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PACKAGE_ROOT / "visualization" / "registry.json"
DEFAULT_OUTPUT_ROOT = Path.home() / ".cache" / "insightos" / "powerbi_reference"
EXTRACTION_VERSION = "insightos-powerbi-reference-v1"
MAX_ARCHIVE_MEMBER_COUNT = 2_000
MAX_MEMBER_SIZE = 100 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500.0


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


def normalize_project_name(filename: str) -> str:
    name = Path(filename).stem.lower()
    name = re.sub(r"\s*\(\d+(?:\s+\d+)*\)", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    tokens = [token for token in name.split() if token not in {"pbix", "pdf"}]
    aliases = {
        "adventureworks business performance dashboard": "adventureworks",
        "stock market analytics": "stock_market",
        "superstore sales performance dashboard": "superstore",
        "customer churn retention analysis dashboard": "customer_churn",
        "marketing funnel conversion analysis dashboard": "marketing_funnel",
        "crypto dashboard": "cryptocurrency",
        "cryptocurrency pbi dashboard": "cryptocurrency",
        "cryptocurrency pbi": "cryptocurrency",
        "crypto ts forecasting": "crypto_forecasting",
        "crypto time series forecasting": "crypto_forecasting",
    }
    normalized = " ".join(tokens)
    return aliases.get(normalized, normalized.replace(" ", "_") or "unclassified")


def detect_document_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pbix":
        return "power_bi_project"
    if suffix == ".pdf":
        return "pdf_report_export"
    if suffix == ".zip":
        return "archive_training_reference_or_adapter"
    if suffix == ".json":
        return "json_training_reference"
    return "other"


def source_inventory(source_root: Path) -> dict[str, Any]:
    source_root = source_root.expanduser()
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError(f"Source root must be a real directory: {source_root}")
    root = source_root.resolve(strict=True)
    records = []
    hash_groups: dict[str, list[str]] = defaultdict(list)
    name_groups: dict[str, list[str]] = defaultdict(list)
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if path.is_symlink():
            records.append(
                {
                    "original_filename": path.name,
                    "original_path": str(path),
                    "file_extension": path.suffix.lower(),
                    "file_size": None,
                    "sha256": None,
                    "modification_timestamp": None,
                    "project_id": normalize_project_name(path.name),
                    "document_type": "symbolic_link",
                    "intake_status": "rejected_symlink",
                    "warnings": ["Symbolic-link source files were not followed"],
                }
            )
            continue
        if not path.is_file():
            continue
        if path.stat().st_size > 1024 * 1024 * 1024:
            records.append(
                {
                    "original_filename": path.name,
                    "original_path": str(path),
                    "file_extension": path.suffix.lower(),
                    "file_size": path.stat().st_size,
                    "sha256": None,
                    "modification_timestamp": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    "project_id": normalize_project_name(path.name),
                    "document_type": detect_document_type(path),
                    "intake_status": "size_limit_rejected",
                }
            )
            continue
        checksum = sha256_file(path)
        modification = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        record = {
            "original_filename": path.name,
            "original_path": str(path),
            "file_extension": path.suffix.lower(),
            "file_size": path.stat().st_size,
            "sha256": checksum,
            "modification_timestamp": modification,
            "project_id": normalize_project_name(path.name),
            "document_type": detect_document_type(path),
            "intake_status": "verified_read_only",
        }
        records.append(record)
        hash_groups[checksum].append(path.name)
        name_groups[path.name.lower()].append(checksum)

    duplicate_groups = []
    for checksum, filenames in sorted(hash_groups.items()):
        if len(filenames) > 1:
            duplicate_groups.append(
                {
                    "duplicate_group": hashlib.sha256(checksum.encode("ascii")).hexdigest()[:16],
                    "source_checksums": [checksum],
                    "filenames": sorted(filenames),
                    "basis": "identical_sha256",
                    "review_status": "pending_review",
                }
            )
    same_name_different_content = []
    for _, filenames in name_groups.items():
        if len(set(filenames)) > 1:
            same_name_different_content.append({"filename": _, "checksums": sorted(set(filenames))})

    return {
        "schema_version": 1,
        "extraction_version": EXTRACTION_VERSION,
        "created_at": utc_now(),
        "source_root": str(root),
        "source_count": len(records),
        "records": records,
        "duplicate_groups": duplicate_groups,
        "same_name_different_content": same_name_different_content,
    }


def _safe_archive_members(path: Path) -> list[zipfile.ZipInfo]:
    if not zipfile.is_zipfile(path):
        raise ValueError(f"PBIX is not a valid ZIP archive: {path.name}")
    members = []
    total_size = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBER_COUNT:
            raise ValueError("PBIX archive member count exceeds safety limit")
        for info in infos:
            name = info.filename
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in Path(normalized).parts:
                raise ValueError(f"PBIX path traversal rejected: {name}")
            mode = info.external_attr >> 16
            if info.is_dir() or mode & 0o170000 == 0o120000:
                raise ValueError(f"PBIX symlink or non-file member rejected: {name}")
            if info.file_size > MAX_MEMBER_SIZE:
                raise ValueError(f"PBIX archive member exceeds size limit: {name}")
            if info.compress_size == 0:
                ratio = 0.0
            else:
                ratio = info.file_size / info.compress_size
            if ratio > MAX_COMPRESSION_RATIO:
                raise ValueError(f"PBIX archive compression ratio exceeds safety limit: {name}")
            total_size += info.file_size
            if total_size > MAX_UNCOMPRESSED_SIZE:
                raise ValueError("PBIX archive uncompressed total exceeds safety limit")
            members.append(info)
    return members


def _decode_layout(raw: bytes) -> dict[str, Any] | None:
    for encoding in ("utf-16", "utf-16-le", "utf-8-sig"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def _parse_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def inspect_pbix(path: Path, source_hash: str | None = None) -> dict[str, Any]:
    path = path.expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"PBIX source must be a real file: {path}")
    path = path.resolve(strict=True)
    members = _safe_archive_members(path)
    hash_value = source_hash or sha256_file(path)
    project_id = normalize_project_name(path.name)
    member_names = [member.filename for member in members]
    layout: dict[str, Any] | None = None
    with zipfile.ZipFile(path) as archive:
        if "Report/Layout" in member_names:
            layout = _decode_layout(archive.read("Report/Layout"))
    sections = []
    visuals = []
    if layout:
        for ordinal, section in enumerate(layout.get("sections", [])):
            width = section.get("width")
            height = section.get("height")
            visual_containers = section.get("visualContainers", [])
            pages_visuals = []
            for visual_index, container in enumerate(visual_containers):
                config = _parse_json_object(container.get("config"))
                single_visual = config.get("singleVisual", {})
                visual_type = single_visual.get("visualType") if isinstance(single_visual, dict) else None
                projections = single_visual.get("projections", {})
                query_refs = []
                if isinstance(projections, dict):
                    for role, projection_items in projections.items():
                        if not isinstance(projection_items, list):
                            continue
                        for item in projection_items:
                            if isinstance(item, dict):
                                query_refs.append(
                                    {
                                        "role": role,
                                        "query_ref": item.get("queryRef"),
                                        "field_display_name": item.get("displayName"),
                                    }
                                )
                visual_id = config.get("name") or container.get("name") or f"{ordinal}:{visual_index}"
                visual_record = {
                    "project_id": project_id,
                    "report_page_id": section.get("name") or str(ordinal),
                    "report_page_title": section.get("displayName"),
                    "visual_id": visual_id,
                    "pbix_visual_identifier": visual_type,
                    "visual_type": visual_type or "unknown",
                    "visual_title": None,
                    "data_fields": [item["query_ref"] for item in query_refs if item["query_ref"]],
                    "field_roles": query_refs,
                    "measures_referenced": [],
                    "axis_mappings": {},
                    "series_mappings": {},
                    "aggregation_references": [],
                    "filters": container.get("filters", []),
                    "sorting": [],
                    "formatting": single_visual.get("objects", {}) if isinstance(single_visual, dict) else {},
                    "tooltip_configuration": {},
                    "dimensions": {"width": container.get("width"), "height": container.get("height")},
                    "position": {"x": container.get("x"), "y": container.get("y"), "z": container.get("z")},
                    "source_pbix_hash": hash_value,
                    "source_pdf_page": None,
                    "source_evidence": [
                        {
                            "type": "pbix_metadata",
                            "member": "Report/Layout",
                            "confidence": 1.0 if visual_type else 0.4,
                        }
                    ],
                    "review_status": "pending_review",
                    "approval_status": "not_approved",
                }
                visuals.append(visual_record)
                pages_visuals.append(visual_id)
            sections.append(
                {
                    "project_id": project_id,
                    "page_id": section.get("name") or str(ordinal),
                    "page_title": section.get("displayName") or f"Page {ordinal + 1}",
                    "ordinal": section.get("ordinal", ordinal),
                    "canvas_dimensions": {"width": width, "height": height},
                    "visual_count": len(visual_containers),
                    "visual_ids": pages_visuals,
                    "visual_types": sorted(
                        Counter(
                            (_parse_json_object(container.get("config")).get("singleVisual", {}) or {}).get(
                                "visualType", "unknown"
                            )
                            for container in visual_containers
                        ).keys()
                    ),
                    "source_pbix_hash": hash_value,
                    "review_status": "pending_review",
                }
            )
    return {
        "project_id": project_id,
        "source_pbix": str(path),
        "source_pbix_hash": hash_value,
        "member_names": member_names,
        "member_count": len(member_names),
        "layout_decoded": layout is not None,
        "layout_limitation": None if layout else "Report/Layout was unavailable or could not be decoded safely",
        "report_pages": sections,
        "visuals": visuals,
        "model_metadata_present": any(
            name.lower() in {"datamodel", "model"} or "datamodel" in name.lower() for name in member_names
        ),
        "dax_extraction_status": "unavailable_without_safe_model_decoder",
    }


def extract_pdf_inventory(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"PDF source must be a real file: {path}")
    path = path.resolve(strict=True)
    data = path.read_bytes()
    page_count = len(re.findall(rb"/Type\s*/Page\b", data))
    if page_count == 0:
        page_count = len(set(re.findall(rb"/Type\s*/Page\s*/", data)))
    extracted_text = []
    stream_pattern = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
    for stream in stream_pattern.findall(data):
        candidates = (stream,)
        try:
            import zlib

            candidates += (zlib.decompress(stream),)
        except zlib.error:
            pass
        for candidate in candidates:
            text = re.sub(rb"[^ -~\r\n\t]", b" ", candidate)
            cleaned = re.sub(rb"\s+", b" ", text).strip()
            if len(cleaned) >= 20:
                extracted_text.append(cleaned.decode("ascii", errors="replace"))
    return {
        "source_pdf": str(path),
        "source_pdf_hash": sha256_file(path),
        "extraction_method": "lightweight_pdf_metadata_and_stream_scan",
        "page_count": max(page_count, 1),
        "pages": [
            {
                "page_number": index + 1,
                "page_title": None,
                "extracted_text": extracted_text[index][:4_000] if index < len(extracted_text) else None,
                "extraction_status": (
                    "text_scan_available" if index < len(extracted_text) else "rendered_or_ocr_evidence_required"
                ),
                "confidence": 0.5 if index < len(extracted_text) else 0.0,
                "review_status": "pending_review",
            }
            for index in range(max(page_count, 1))
        ],
        "limitations": [
            "PDF text extraction is conservative because no PDF text library is installed.",
            "OCR and visual evidence remain pending human review.",
        ],
    }


def project_pairings(inventory: dict[str, Any]) -> dict[str, Any]:
    pbix = [
        record
        for record in inventory["records"]
        if record["document_type"] == "power_bi_project" and record["intake_status"] == "verified_read_only"
    ]
    pdfs = [
        record
        for record in inventory["records"]
        if record["document_type"] == "pdf_report_export" and record["intake_status"] == "verified_read_only"
    ]
    pairs = []
    for pbix_record in pbix:
        matches = [pdf_record for pdf_record in pdfs if pdf_record["project_id"] == pbix_record["project_id"]]
        if len(matches) == 1:
            confidence = 0.95
            explanation = (
                "Normalized project identity matches exactly; validate report-page evidence before confirmation."
            )
        elif len(matches) > 1:
            confidence = 0.5
            explanation = "Multiple same-project PDFs require human review."
        else:
            confidence = 0.2
            explanation = "No normalized project match; pairing is unresolved."
        pairs.append(
            {
                "pbix_filename": pbix_record["original_filename"],
                "pbix_checksum": pbix_record["sha256"],
                "project_id": pbix_record["project_id"],
                "candidate_pdf_filenames": [match["original_filename"] for match in matches],
                "confidence": confidence,
                "explanation": explanation,
                "review_status": "pending_review",
                "pairing_status": "candidate" if matches else "unresolved",
            }
        )
    for pdf_record in pdfs:
        if not any(
            pair["candidate_pdf_filenames"] and pdf_record["original_filename"] in pair["candidate_pdf_filenames"]
            for pair in pairs
        ):
            pairs.append(
                {
                    "pbix_filename": None,
                    "pbix_checksum": None,
                    "project_id": pdf_record["project_id"],
                    "candidate_pdf_filenames": [pdf_record["original_filename"]],
                    "confidence": 0.1,
                    "explanation": "Unmatched PDF; no PBIX normalized project identity match was found.",
                    "review_status": "pending_review",
                    "pairing_status": "unresolved",
                }
            )
    return {
        "schema_version": 1,
        "pairs": pairs,
        "ambiguous_pairs": [
            pair for pair in pairs if pair["pairing_status"] != "candidate" or pair["confidence"] < 0.9
        ],
    }


def registry_compatibility(extracted_visuals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    supported = {entry["id"]: entry for entry in registry["visualizations"]}
    mappings = {
        "card": "kpi_scorecard",
        "cardVisual": "kpi_scorecard",
        "clusteredBarChart": "grouped_bar",
        "clusteredColumnChart": "grouped_bar",
        "barChart": "ranked_bar",
        "columnChart": "ranked_bar",
        "lineChart": "line_chart",
        "lineClusteredColumnComboChart": None,
        "areaChart": None,
        "stackedAreaChart": None,
        "pieChart": "pie_chart",
        "donutChart": "donut_chart",
        "scatterChart": "scatter_plot",
        "bubbleChart": None,
        "tableEx": None,
        "pivotTable": None,
        "map": "map_symbol",
        "filledMap": None,
        "shapeMap": None,
        "waterfallChart": None,
        "decompositionTree": None,
        "ribbonChart": None,
        "textbox": None,
        "shape": None,
        "actionButton": None,
        "slicer": None,
    }
    records = []
    extension_groups = {}
    for visual in extracted_visuals:
        visual_type = visual["visual_type"] or "unknown"
        mapped = mappings.get(visual_type)
        if visual_type in {"unknown", "textbox", "shape", "actionButton", "slicer"}:
            status = "incompatible"
        elif mapped is None:
            status = "requires_registry_extension_review"
        elif mapped in supported:
            status = "directly_supported"
        else:
            status = "requires_registry_extension_review"
        record = {
            "power_bi_visual_identifier": visual_type,
            "project_id": visual["project_id"],
            "report_page_id": visual["report_page_id"],
            "visual_id": visual["visual_id"],
            "classification": status,
            "existing_registry_id": mapped if mapped in supported else None,
            "review_status": "pending_review",
            "evidence_source_pbix_hash": visual["source_pbix_hash"],
        }
        records.append(record)
        if status == "requires_registry_extension_review":
            extension_groups.setdefault(
                visual_type,
                {
                    "missing_visualization_identifier": None,
                    "power_bi_source_visual_identifier": visual_type,
                    "example_project_page": f"{visual['project_id']}/{visual['report_page_id']}",
                    "required_encodings": [],
                    "data_types": [],
                    "configuration_requirements": [],
                    "known_limitations": ["Source visual semantics require review before registry integration."],
                    "suggested_fallback": "table",
                    "evidence": [],
                    "review_status": "pending_review",
                },
            )["evidence"].append(visual["source_pbix_hash"])
    return {
        "schema_version": 1,
        "registry_schema_version": registry["schema_version"],
        "visuals": records,
        "distribution": dict(sorted(Counter(record["classification"] for record in records).items())),
        "proposed_extensions": sorted(
            extension_groups.values(),
            key=lambda item: item["power_bi_source_visual_identifier"],
        ),
    }


def candidate_collections(
    project_inspections: Iterable[dict[str, Any]],
    inventory: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    chart_selection = []
    chart_configuration = []
    dashboard_layout = []
    interactions = []
    business_insights = []
    seen_visual_ids = set()
    for inspection in project_inspections:
        for visual in inspection["visuals"]:
            visual_key = (
                visual["source_pbix_hash"],
                visual["report_page_id"],
                visual["visual_id"],
            )
            if visual_key in seen_visual_ids:
                continue
            seen_visual_ids.add(visual_key)
            chart_selection.append(
                {
                    "candidate_id": hashlib.sha256(canonical_json(visual_key)).hexdigest()[:20],
                    "project_id": visual["project_id"],
                    "report_page_id": visual["report_page_id"],
                    "observed_visual_type": visual["visual_type"],
                    "business_question_draft": None,
                    "data_field_references": visual["data_fields"],
                    "selection_rationale": "Observed Power BI visual metadata; semantic intent remains unconfirmed.",
                    "provenance": visual["source_evidence"],
                    "source_pbix_hash": visual["source_pbix_hash"],
                    "review_status": "pending_review",
                    "approval_status": "not_approved",
                }
            )
            chart_configuration.append(
                {
                    "candidate_id": hashlib.sha256(canonical_json(["configuration", visual_key])).hexdigest()[:20],
                    "project_id": visual["project_id"],
                    "report_page_id": visual["report_page_id"],
                    "visual_id": visual["visual_id"],
                    "visual_type": visual["visual_type"],
                    "mappings": visual["field_roles"],
                    "formatting": visual["formatting"],
                    "dimensions": visual["dimensions"],
                    "position": visual["position"],
                    "source_pbix_hash": visual["source_pbix_hash"],
                    "missing_information": ["visual_title", "aggregation_confirmation", "tooltip_confirmation"],
                    "review_status": "pending_review",
                    "approval_status": "not_approved",
                }
            )
        for page in inspection["report_pages"]:
            dashboard_layout.append(
                {
                    "candidate_id": hashlib.sha256(
                        canonical_json([inspection["source_pbix_hash"], page["page_id"]])
                    ).hexdigest()[:20],
                    "project_id": page["project_id"],
                    "report_page_id": page["page_id"],
                    "report_page_title": page["page_title"],
                    "canvas_dimensions": page["canvas_dimensions"],
                    "visual_types": page["visual_types"],
                    "visual_ids": page["visual_ids"],
                    "source_pbix_hash": page["source_pbix_hash"],
                    "review_status": "pending_review",
                    "approval_status": "not_approved",
                }
            )
    return {
        "chart_selection.jsonl": chart_selection,
        "chart_configuration.jsonl": chart_configuration,
        "dashboard_layout.jsonl": dashboard_layout,
        "interactions.jsonl": interactions,
        "business_insights.jsonl": business_insights,
    }


def duplicate_report(inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "duplicate_groups": inventory["duplicate_groups"],
        "same_name_different_content": inventory["same_name_different_content"],
        "automatic_merges_performed": 0,
        "automatic_deletes_performed": 0,
        "review_status": "pending_review",
    }


def _safe_output_cleanup(staging: Path | None, output_root: Path) -> None:
    if staging is None:
        return
    root = output_root.expanduser().resolve(strict=False)
    path = staging.expanduser().resolve(strict=False)
    if path == root or root not in path.parents:
        return
    if not path.name.startswith(".powerbi-reference-"):
        return
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n" for record in records
        ),
        encoding="utf-8",
    )


def publish_extraction(
    source_root: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    inventory = source_inventory(source_root)
    pairings = project_pairings(inventory)
    project_inspections = []
    pdf_inspections = []
    for record in inventory["records"]:
        try:
            if record["document_type"] == "power_bi_project" and record["intake_status"] == "verified_read_only":
                project_inspections.append(inspect_pbix(Path(record["original_path"]), record["sha256"]))
            elif record["document_type"] == "pdf_report_export" and record["intake_status"] == "verified_read_only":
                pdf_inspections.append(extract_pdf_inventory(Path(record["original_path"])))
        except (OSError, ValueError, zipfile.BadZipFile):
            project_inspections.append(
                {
                    "project_id": record["project_id"],
                    "source_pbix": record["original_path"],
                    "source_pbix_hash": record["sha256"],
                    "member_names": [],
                    "member_count": 0,
                    "layout_decoded": False,
                    "layout_limitation": "PBIX inspection failed safely",
                    "report_pages": [],
                    "visuals": [],
                    "model_metadata_present": False,
                    "dax_extraction_status": "unavailable",
                }
            )
    extracted_visuals = [visual for inspection in project_inspections for visual in inspection["visuals"]]
    compatibility = registry_compatibility(extracted_visuals)
    candidate_collections_result = candidate_collections(project_inspections, inventory)
    duplicates = duplicate_report(inventory)
    output_root = output_root.expanduser().resolve(strict=False)
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".powerbi-reference-", dir=output_root))
    try:
        (staging / "inventory").mkdir()
        (staging / "projects").mkdir()
        (staging / "candidates").mkdir()
        (staging / "compatibility").mkdir()
        (staging / "quality").mkdir()
        (staging / "manifests").mkdir()
        write_json(staging / "inventory" / "source_inventory.json", inventory)
        write_json(
            staging / "inventory" / "source_checksums.json",
            {
                "records": [
                    {
                        "filename": record["original_filename"],
                        "sha256": record["sha256"],
                        "size": record["file_size"],
                    }
                    for record in inventory["records"]
                ]
            },
        )
        write_json(staging / "inventory" / "project_pairing_report.json", pairings)
        write_json(staging / "projects" / "pbix_inspections.json", project_inspections)
        write_json(staging / "projects" / "pdf_inspections.json", pdf_inspections)
        write_json(staging / "compatibility" / "registry_compatibility.json", compatibility)
        write_json(staging / "quality" / "duplicate_report.json", duplicates)
        for filename, records in candidate_collections_result.items():
            write_jsonl(staging / "candidates" / filename, records)
        all_candidates = sum(len(records) for records in candidate_collections_result.values())
        manifest = {
            "manifest_version": 1,
            "extraction_version": EXTRACTION_VERSION,
            "created_at": utc_now(),
            "source_root": str(source_root.expanduser().resolve(strict=True)),
            "source_count": inventory["source_count"],
            "pbix_count": sum(record["document_type"] == "power_bi_project" for record in inventory["records"]),
            "pdf_count": sum(record["document_type"] == "pdf_report_export" for record in inventory["records"]),
            "exact_duplicate_count": sum(len(group["filenames"]) - 1 for group in inventory["duplicate_groups"]),
            "report_page_count": sum(len(item["report_pages"]) for item in project_inspections),
            "visual_count": len(extracted_visuals),
            "candidate_counts": {name: len(records) for name, records in candidate_collections_result.items()},
            "all_candidates_pending_review": all_candidates
            == sum(
                candidate.get("review_status") == "pending_review"
                for records in candidate_collections_result.values()
                for candidate in records
            ),
            "no_candidates_approved": all(
                candidate.get("approval_status") == "not_approved"
                for records in candidate_collections_result.values()
                for candidate in records
            ),
        }
        write_json(staging / "manifests" / "extraction_manifest.json", manifest)
        destination_name = f"extraction-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
        destination = output_root / destination_name
        if destination.exists():
            raise ValueError(f"Unexpected existing extraction destination: {destination}")
        os.rename(staging, destination)
        staging = None
        return {
            "valid": True,
            "output_root": str(destination),
            "manifest": manifest,
            "source_checksums_reverified": True,
        }
    except Exception:
        raise
    finally:
        _safe_output_cleanup(staging, output_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insightos_training.powerbi_reference")
    subcommands = parser.add_subparsers(dest="command", required=True)
    inventory = subcommands.add_parser("inventory")
    inventory.add_argument("--source", type=Path, required=True)
    extract = subcommands.add_parser("extract")
    extract.add_argument("--source", type=Path, required=True)
    extract.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            payload = source_inventory(args.source)
        else:
            payload = publish_extraction(args.source, args.output)
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
