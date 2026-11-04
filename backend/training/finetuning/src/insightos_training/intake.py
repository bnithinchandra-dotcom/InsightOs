from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
VISUALIZATION_DIR = PACKAGE_ROOT / "visualization"
DEFAULT_INTAKE_ROOT = Path.home() / ".cache" / "insightos" / "incoming" / "visualization"
MANIFEST_SCHEMA_VERSION = 1
INTAKE_MODULE_VERSION = "insightos-visualization-intake-v1"


def utc_now() -> str:
    return datetime.now().astimezone().isoformat()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_output_bundle_name(source: Path, source_hash: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip(".-_") or "source"
    return f"{stem}-{source_hash[:12]}"


def _resolve_source(source: Path) -> Path:
    source = source.expanduser()
    if source.is_symlink():
        raise ValueError("Source path cannot be a symbolic link")
    if not source.exists():
        raise ValueError(f"Source does not exist: {source}")
    if not source.is_file():
        raise ValueError(f"Source is not a regular file: {source}")
    return source.resolve(strict=True)


def _safe_cleanup_staging(staging: Path | None, output_root: Path) -> None:
    if staging is None:
        return
    root = output_root.expanduser().resolve(strict=False)
    path = staging.expanduser().resolve(strict=False)
    if path == root or root not in path.parents:
        return
    if not path.name.startswith(".visualization-intake-"):
        return
    if path.exists():
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "na", "n/a", "null", "none", "nan"}
    return False


def _value_family(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if not isinstance(value, str):
        return type(value).__name__
    text = value.strip()
    if not text:
        return "missing"
    if re.fullmatch(r"[+-]?\d+", text):
        return "integer"
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?", text):
        return "number"
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:[Tt ].*)?$", text):
        return "date_like"
    return "string"


def infer_column_type(values: Iterable[Any], name: str = "") -> tuple[str, int]:
    usable = [value for value in values if not _is_missing(value)]
    if not usable:
        return "string", len(usable)
    families = Counter(_value_family(value) for value in usable)
    invalid_dates = 0
    if re.search(r"(date|time|month|week|quarter|year)$", name.lower()):
        invalid_dates = sum(1 for value in usable if not _parse_date(value))
        if families and all(_value_family(value) == "date_like" for value in usable):
            is_datetime = any("T" in str(value) or " " in str(value) for value in usable)
            return "datetime" if is_datetime else "date", invalid_dates
    numeric = families["integer"] + families["number"]
    if numeric == len(usable):
        return "number" if families["number"] else "integer", invalid_dates
    family_count = len(families)
    if family_count == 1 and "boolean" in families:
        return "boolean", invalid_dates
    return "category" if family_count <= 2 else "string", invalid_dates


def _parse_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def read_source(source: Path) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    source = _resolve_source(source)
    suffix = source.suffix.lower()
    parser_name = "stdlib"
    duplicate_columns: list[str] = []
    malformed = False
    rows: list[dict[str, Any]] = []

    if suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration:
                headers = []
            counts = Counter(headers)
            duplicate_columns = sorted(name for name, count in counts.items() if name and count > 1)
            if any(not name.strip() for name in headers):
                malformed = True
            rows = []
            for values in reader:
                if len(values) != len(headers):
                    malformed = True
                    continue
                rows.append(
                    {
                        f"{name}__duplicate_{index}" if counts[name] > 1 else name: value
                        for index, (name, value) in enumerate(zip(headers, values), start=1)
                    }
                )
        parser_name = "csv.reader@python3.11"
    elif suffix == ".jsonl":
        with source.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Malformed JSONL at line {line_number}: {error.msg}") from error
                if not isinstance(item, dict):
                    raise ValueError(f"JSONL line {line_number} must contain a JSON object")
                rows.append(item)
        headers = list(dict.fromkeys(key for row in rows for key in row))
        parser_name = "json@python3.11-jsonl"
    elif suffix == ".json":
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Malformed JSON: {error.msg}") from error
        if isinstance(payload, dict):
            rows = [payload]
        elif isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            rows = payload
        else:
            raise ValueError("JSON must contain a JSON object or an array of objects")
        headers = list(dict.fromkeys(key for row in rows for key in row))
        parser_name = "json@python3.11"
    else:
        raise ValueError(
            f"Unsupported input format: {suffix or 'no extension'}; supported formats are CSV, JSON, and JSONL"
        )

    if malformed:
        raise ValueError("Malformed CSV: inconsistent row width")
    return (
        headers,
        rows,
        {
            "parser_name": parser_name,
            "parser_version": "Python stdlib 3.11",
            "duplicate_columns": duplicate_columns,
            "format": suffix.lstrip("."),
            "file_size_bytes": source.stat().st_size,
        },
    )


SENSITIVE_NAME_PATTERNS = (
    re.compile(r"(?:^|[_-])(name|full_name|first_name|last_name)(?:$|[_-])", re.I),
    re.compile(r"(email|e_mail|mail_address)", re.I),
    re.compile(r"(phone|mobile|telephone|contact_number)", re.I),
    re.compile(r"(address|street|postal_code|zip_code)", re.I),
    re.compile(r"(ssn|social_security|national_id|passport|tax_id)", re.I),
    re.compile(r"(account_id|bank_account|credit_card|card_number|patient_id|customer_id)", re.I),
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
CARD_RE = re.compile(r"\b(?:\d[ -]*){13,19}\b")


def mask_value(value: Any) -> str:
    text = str(value)
    if EMAIL_RE.search(text):
        return "[REDACTED_EMAIL]"
    if CARD_RE.search(text):
        return "[REDACTED_CARD_NUMBER]"
    if PHONE_RE.search(text):
        return "[REDACTED_PHONE]"
    if text:
        return "[REDACTED]"
    return ""


def privacy_inspection(headers: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    suspicious: list[dict[str, Any]] = []
    structural_matches: dict[str, list[str]] = {}
    for field in headers:
        reasons = [pattern.pattern for pattern in SENSITIVE_NAME_PATTERNS if pattern.search(field)]
        matches = []
        for row in rows[:100]:
            value = row.get(field)
            if value is None:
                continue
            text = str(value)
            if EMAIL_RE.search(text):
                matches.append("email_pattern")
            if PHONE_RE.search(text):
                matches.append("phone_pattern")
            if CARD_RE.search(text):
                matches.append("financial_identifier_pattern")
        matches = sorted(set(matches))
        if reasons or matches:
            suspicious.append({"field": field, "column_name_reasons": reasons, "structural_matches": matches})
            if matches:
                structural_matches[field] = matches
    return {
        "status": "requires_human_review" if suspicious else "no_suspicious_fields_detected",
        "suspicious_columns": suspicious,
        "raw_sensitive_values_logged": False,
        "masking_available": True,
        "limitations": "Heuristic detection is not perfect; human review is required.",
        "structural_matches": structural_matches,
    }


def profile_dataset(
    source: Path,
    source_id: str | None = None,
    source_location: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = _resolve_source(source)
    source_hash = sha256_file(source)
    headers, rows, parser = read_source(source)
    dataset_id = source_id or f"viz-source-{source_hash[:16]}"

    column_profiles = []
    for index, field in enumerate(headers):
        raw_values = [row.get(field) for row in rows]
        usable = [value for value in raw_values if not _is_missing(value)]
        inferred_type, invalid_dates = infer_column_type(usable, field)
        unique = sorted({str(value) for value in usable}, key=lambda value: value)[:20]
        numeric_values = [float(value) for value in usable if isinstance(value, (int, float))]
        if not numeric_values and usable and all(_value_family(value) in {"integer", "number"} for value in usable):
            numeric_values = [float(value) for value in usable]
        profile = {
            "ordinal": index,
            "original_field_name": field,
            "inferred_data_type": inferred_type,
            "inferred": True,
            "missing_value_count": len(raw_values) - len(usable),
            "unique_value_count": len(set(str(value) for value in usable)),
            "cardinality": len(set(str(value) for value in usable)),
            "date_parsing_issue_count": invalid_dates,
            "numeric_min": min(numeric_values) if numeric_values else None,
            "numeric_max": max(numeric_values) if numeric_values else None,
            "numeric_mean": sum(numeric_values) / len(numeric_values) if numeric_values else None,
            "example_values": unique[:3],
            "constant": len(set(str(value) for value in usable)) <= 1 and bool(usable),
        }
        column_profiles.append(profile)

    canonical_records = Counter(canonical_json(row).decode("utf-8") for row in rows)
    duplicate_records = sum(count - 1 for count in canonical_records.values() if count > 1)
    duplicate_column_names = parser["duplicate_columns"]
    mixed_types = []
    for column in column_profiles:
        field = column["original_field_name"]
        families = sorted(
            set(_value_family(value) for value in (row.get(field) for row in rows) if not _is_missing(value))
        )
        if len(families) > 1:
            mixed_types.append({"field": field, "families": families})

    quality = {
        "duplicate_records": duplicate_records,
        "duplicate_column_names": duplicate_column_names,
        "mixed_data_types": mixed_types,
        "missing_values_total": sum(column["missing_value_count"] for column in column_profiles),
        "constant_columns": [column["original_field_name"] for column in column_profiles if column["constant"]],
        "high_cardinality_columns": [
            {"field": column["original_field_name"], "cardinality": column["cardinality"]}
            for column in column_profiles
            if len(rows) > 0 and column["cardinality"] / len(rows) > 0.9
        ],
        "invalid_dates": [
            {"field": column["original_field_name"], "count": column["date_parsing_issue_count"]}
            for column in column_profiles
            if column["date_parsing_issue_count"] > 0
        ],
    }
    privacy = privacy_inspection(headers, rows)
    sensitive_fields = {item["field"] for item in privacy["suspicious_columns"]}
    for column in column_profiles:
        if column["original_field_name"] in sensitive_fields:
            column["example_values"] = ["[REDACTED]"]
    source_manifest = {
        "manifest_version": MANIFEST_SCHEMA_VERSION,
        "intake_module_version": INTAKE_MODULE_VERSION,
        "source_identifier": dataset_id,
        "original_filename": source.name,
        "file_format": parser["format"],
        "file_size_bytes": parser["file_size_bytes"],
        "sha256": source_hash,
        "source_location": source_location or str(source),
        "intake_timestamp": utc_now(),
        "parser_name": parser["parser_name"],
        "parser_version": parser["parser_version"],
        "processing_status": "profiled",
    }
    profile = {
        "manifest_version": MANIFEST_SCHEMA_VERSION,
        "dataset_identifier": dataset_id,
        "row_count": len(rows),
        "column_count": len(headers),
        "file_format": parser["format"],
        "source_checksum": source_hash,
        "available_sheets": [],
        "columns": column_profiles,
    }
    return source_manifest, profile, quality, privacy, {"headers": headers, "rows": rows}


def generate_candidate_profiles(
    source_manifest: dict[str, Any],
    profile: dict[str, Any],
    privacy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    registry_path = VISUALIZATION_DIR / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry_ids = {entry["id"] for entry in registry["visualizations"]}
    columns = profile["columns"]
    usable_columns = list(columns)
    sensitive_fields = {item["field"] for item in privacy["suspicious_columns"]}
    usable_columns = [column for column in usable_columns if column["original_field_name"] not in sensitive_fields]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for column in usable_columns:
        by_type.setdefault(column["inferred_data_type"], []).append(column)
    numeric = by_type.get("number", []) + by_type.get("integer", [])
    categories = by_type.get("category", []) + by_type.get("string", [])
    dates = by_type.get("date", []) + by_type.get("datetime", [])
    geography = [
        column
        for column in usable_columns
        if re.search(
            r"(country|region|state|province|county|city|location|postal)",
            column["original_field_name"],
            re.I,
        )
    ]
    suggestions = []

    def add_candidate(
        objective: str,
        visualization_id: str,
        field_names: list[str],
        question: str,
        rationale: str,
    ) -> None:
        if visualization_id not in registry_ids:
            return
        candidate_id = hashlib.sha256(
            canonical_json([source_manifest["source_identifier"], objective, sorted(field_names)])
        ).hexdigest()[:20]
        suggestions.append(
            {
                "candidate_id": f"viz-candidate-{candidate_id}",
                "source_dataset_id": source_manifest["source_identifier"],
                "source_checksum": source_manifest["sha256"],
                "dataset_profile_reference": "dataset_profile.json",
                "business_question_draft": question,
                "available_field_references": [
                    {"name": column["original_field_name"], "type": column["inferred_data_type"]}
                    for column in usable_columns
                    if column["original_field_name"] in field_names
                ],
                "suggested_analytical_objective": objective,
                "suggested_visualization_identifier": visualization_id,
                "selection_rationale": rationale,
                "registry_version": registry["schema_version"],
                "output_schema_version": 1,
                "privacy_review_status": "pending_review",
                "annotation_status": "pending_review",
                "review_status": "pending_review",
            }
        )

    if dates and numeric:
        add_candidate(
            "trend",
            "line_chart",
            [dates[0]["original_field_name"], numeric[0]["original_field_name"]],
            f"How did {numeric[0]['original_field_name']} change over {dates[0]['original_field_name']}?",
            "Ordered time and numeric data support trend analysis.",
        )
    if geography and numeric:
        add_candidate(
            "geographic_comparison",
            "map_symbol",
            [geography[0]["original_field_name"], numeric[0]["original_field_name"]],
            f"Where is {numeric[0]['original_field_name']} distributed geographically?",
            "A geographic field and numeric measure can support a symbol map.",
        )
    if len(numeric) >= 2:
        add_candidate(
            "relationship",
            "scatter_plot",
            [numeric[0]["original_field_name"], numeric[1]["original_field_name"]],
            f"How are {numeric[0]['original_field_name']} and {numeric[1]['original_field_name']} related?",
            "Two numeric fields support relationship analysis.",
        )
    if categories and numeric:
        category = categories[0]
        cardinality = category["cardinality"]
        if 2 <= cardinality <= 30:
            add_candidate(
                "ranking",
                "ranked_bar",
                [category["original_field_name"], numeric[0]["original_field_name"]],
                f"Which {category['original_field_name']} has the highest {numeric[0]['original_field_name']}?",
                "The available category and numeric measure support a ranked comparison.",
            )
    if numeric:
        add_candidate(
            "distribution",
            "histogram",
            [numeric[0]["original_field_name"]],
            f"What is the distribution of {numeric[0]['original_field_name']}?",
            "A numeric measure supports distribution profiling.",
        )
    if not suggestions:
        add_candidate(
            "detail",
            "table",
            [column["original_field_name"] for column in usable_columns[:50]],
            "What record-level detail is available in this dataset?",
            "No safer visualization hypothesis is supported by the available profile; the table is a fallback.",
        )
    if suggestions:
        identities = [item["candidate_id"] for item in suggestions]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate generated candidate identifiers")
    coverage = {
        "suggested_visualization_families": sorted(
            set(item["suggested_visualization_identifier"] for item in suggestions)
        ),
        "suggested_analytical_objectives": sorted(set(item["suggested_analytical_objective"] for item in suggestions)),
        "privacy_review_status": privacy["status"],
        "annotation_status": "pending_review",
        "unassigned_split": None,
    }
    return suggestions, coverage


def validate_intake_bundle(bundle: Path, schema_path: Path | None = None) -> dict[str, Any]:
    bundle = bundle.expanduser().resolve(strict=False)
    if not bundle.is_dir():
        raise ValueError(f"Intake bundle does not exist: {bundle}")
    required = (
        "source_manifest.json",
        "dataset_profile.json",
        "quality_report.json",
        "privacy_report.json",
        "visualization_candidates.jsonl",
        "intake_report.json",
    )
    missing = [name for name in required if not (bundle / name).is_file()]
    if missing:
        raise ValueError(f"Intake bundle missing files: {', '.join(missing)}")

    manifest = json.loads((bundle / "source_manifest.json").read_text(encoding="utf-8"))
    profile = json.loads((bundle / "dataset_profile.json").read_text(encoding="utf-8"))
    intake_report = json.loads((bundle / "intake_report.json").read_text(encoding="utf-8"))
    candidate_ids = set()
    candidate_hashes = set()
    with (bundle / "visualization_candidates.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            candidate = json.loads(line)
            if candidate["candidate_id"] in candidate_ids:
                raise ValueError(f"Duplicate candidate ID: {candidate['candidate_id']}")
            candidate_ids.add(candidate["candidate_id"])
            if candidate["source_checksum"] != manifest["sha256"]:
                raise ValueError("Candidate source checksum mismatch")
            if candidate["review_status"] != "pending_review":
                raise ValueError("Generated candidate must remain pending_review")
            digest = hashlib.sha256(canonical_json(candidate)).digest()
            if digest in candidate_hashes:
                raise ValueError("Duplicate candidate content")
            candidate_hashes.add(digest)

    schema_path = schema_path or (VISUALIZATION_DIR / "intake" / "manifest_schema.json")
    if schema_path.exists():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(intake_report), key=lambda error: list(error.path))
        if errors:
            raise ValueError(f"Intake report failed schema validation: {errors[0].message}")
    checksum_errors = []
    for item in intake_report.get("file_checksums", []):
        if item["filename"] == "intake_report.json":
            continue
        file_path = bundle / item["filename"]
        if not file_path.is_file():
            checksum_errors.append(f"missing {item['filename']}")
        elif sha256_file(file_path) != item["sha256"]:
            checksum_errors.append(f"checksum mismatch for {item['filename']}")
    if checksum_errors:
        raise ValueError("Intake integrity validation failed: " + "; ".join(checksum_errors))
    if profile["source_checksum"] != manifest["sha256"]:
        raise ValueError("Dataset profile source checksum mismatch")
    return {"valid": True, "candidate_count": len(candidate_ids), "bundle": str(bundle)}


def inspect_source(source: Path) -> dict[str, Any]:
    source = _resolve_source(source)
    headers, rows, parser = read_source(source)
    return {
        "preview": True,
        "persisted": False,
        "original_filename": source.name,
        "file_format": parser["format"],
        "file_size_bytes": parser["file_size_bytes"],
        "sha256": sha256_file(source),
        "row_count": len(rows),
        "column_count": len(headers),
        "columns": headers,
        "duplicate_columns": parser["duplicate_columns"],
    }


def create_intake_bundle(
    source: Path,
    output_root: Path = DEFAULT_INTAKE_ROOT,
    source_id: str | None = None,
) -> dict[str, Any]:
    source = _resolve_source(source)
    source_hash = sha256_file(source)
    output_root = output_root.expanduser().resolve(strict=False)
    output_root.mkdir(parents=True, exist_ok=True)
    for existing in output_root.iterdir():
        if not existing.is_dir() or existing.name.startswith(".visualization-intake-"):
            continue
        manifest_path = existing / "source_manifest.json"
        if manifest_path.is_file():
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if existing_manifest.get("sha256") == source_hash:
                raise ValueError(
                    f"Duplicate intake prevented: source already exists in bundle {existing}"
                )
    destination = output_root / safe_output_bundle_name(source, source_hash)
    if destination.exists():
        raise ValueError(f"Duplicate intake prevented: bundle already exists: {destination}")

    source_manifest, profile, quality, privacy, parsed = profile_dataset(source, source_id=source_id)
    candidates, coverage = generate_candidate_profiles(source_manifest, profile, privacy)
    staging: Path | None = Path(tempfile.mkdtemp(prefix=".visualization-intake-", dir=output_root))
    try:
        files = {
            "source_manifest.json": source_manifest,
            "dataset_profile.json": profile,
            "quality_report.json": quality,
            "privacy_report.json": privacy,
        }
        for filename, payload in files.items():
            (staging / filename).write_text(
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        candidates_path = staging / "visualization_candidates.jsonl"
        candidates_path.write_text(
            "".join(
                json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
                for item in candidates
            ),
            encoding="utf-8",
        )
        checksums = []
        for filename in (
            "source_manifest.json",
            "dataset_profile.json",
            "quality_report.json",
            "privacy_report.json",
            "visualization_candidates.jsonl",
        ):
            path = staging / filename
            checksums.append({"filename": filename, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
        report = {
            "manifest_version": MANIFEST_SCHEMA_VERSION,
            "intake_module_version": INTAKE_MODULE_VERSION,
            "source_identifier": source_manifest["source_identifier"],
            "source_checksum": source_hash,
            "status": "review_ready",
            "source_integrity_status": "verified",
            "profiling_status": "completed",
            "candidate_status": "pending_review",
            "human_annotation_status": "pending",
            "governance_approval_status": "not_requested",
            "training_export_status": "not_permitted",
            "coverage": coverage,
            "file_checksums": checksums,
            "failure_recovery": {"partial_import": False, "cleanup": "operation-local staging only"},
            "limitations": ["No actual expert labels were generated", "Privacy detection is heuristic"],
        }
        (staging / "intake_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        checksums.append(
            {
                "filename": "intake_report.json",
                "sha256": sha256_file(staging / "intake_report.json"),
                "size_bytes": (staging / "intake_report.json").stat().st_size,
            }
        )
        report["file_checksums"] = checksums
        (staging / "intake_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.rename(staging, destination)
        staging = None
        return validate_intake_bundle(destination)
    except Exception:
        raise
    finally:
        _safe_cleanup_staging(staging, output_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insightos-training intake")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect")
    inspect.add_argument("--source", type=Path, required=True)

    profile = commands.add_parser("profile")
    profile.add_argument("--source", type=Path, required=True)
    profile.add_argument("--output", type=Path, default=DEFAULT_INTAKE_ROOT)

    candidates = commands.add_parser("candidates")
    candidates.add_argument("--source", type=Path, required=True)
    candidates.add_argument("--output", type=Path, default=DEFAULT_INTAKE_ROOT)

    validate = commands.add_parser("validate-bundle")
    validate.add_argument("--bundle", type=Path, required=True)

    report = commands.add_parser("inspect-report")
    report.add_argument("--bundle", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            payload = inspect_source(args.source)
        elif args.command == "validate-bundle":
            payload = validate_intake_bundle(args.bundle)
        elif args.command == "inspect-report":
            payload = json.loads((args.bundle / "intake_report.json").read_text(encoding="utf-8"))
        else:
            payload = create_intake_bundle(args.source, args.output)
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
