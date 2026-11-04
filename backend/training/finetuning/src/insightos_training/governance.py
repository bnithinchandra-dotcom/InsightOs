from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .visualization import load_output_schema, load_registry, validate_registry, validate_visualization_spec

PROMPT_TEMPLATE_VERSION = "insightos-bi-visualization-qwen-v1"
APPROVAL_STATES = {"draft", "pending_review", "approved", "rejected"}
EXPORT_SPLITS = ("train", "validation", "evaluation")


def _cleanup_staging(staging: Path | None, staging_root: Path) -> None:
    if staging is None:
        return
    root = staging_root.expanduser().resolve(strict=False)
    path = staging.expanduser().resolve(strict=False)
    if path == root or root not in path.parents:
        return
    if not path.name.startswith(".visualization-export-"):
        return
    if path.exists():
        shutil.rmtree(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def source_content_hash(source_content: Any) -> str:
    return hashlib.sha256(canonical_json(source_content)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _string(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Candidate field {field} must be a non-empty string")
    return value


def validate_provenance(candidate: dict[str, Any]) -> dict[str, Any]:
    required = (
        "id",
        "source_dataset_id",
        "source_record_id",
        "source_content_hash",
        "scenario_group",
        "template_group",
        "visualization_category",
        "registry_version",
        "output_schema_version",
        "creation_timestamp",
        "review_status",
    )
    missing = sorted(field for field in required if field not in candidate)
    if missing:
        raise ValueError(f"Candidate missing provenance fields: {', '.join(missing)}")
    for field in (
        "id",
        "source_dataset_id",
        "source_record_id",
        "source_content_hash",
        "scenario_group",
        "template_group",
        "visualization_category",
        "creation_timestamp",
    ):
        _string(candidate, field)
    status = candidate["review_status"]
    if status not in APPROVAL_STATES:
        raise ValueError(f"Unsupported review_status: {status}")
    reviewer = candidate.get("reviewer_identity") or candidate.get("reviewer_reference")
    if status == "approved":
        if not reviewer or not candidate.get("approval_timestamp"):
            raise ValueError("Approved candidates require reviewer identity/reference and approval_timestamp")
    if status == "rejected":
        if not reviewer or not candidate.get("rejection_reason"):
            raise ValueError("Rejected candidates require reviewer identity/reference and rejection_reason")
    digest = candidate["source_content_hash"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("source_content_hash must be a lowercase SHA-256 digest")
    if candidate.get("source_content") is not None and source_content_hash(candidate["source_content"]) != digest:
        raise ValueError("Source content hash mismatch")
    return {"valid": True, "example_id": candidate["id"]}


def verify_source_hash(candidate: dict[str, Any], source_content: Any | None = None) -> dict[str, Any]:
    validate_provenance(candidate)
    source_content = candidate.get("source_content") if source_content is None else source_content
    if source_content is None:
        raise ValueError(f"Source content is unavailable for provenance verification: {candidate['id']}")
    actual = source_content_hash(source_content)
    if actual != candidate["source_content_hash"]:
        raise ValueError(f"Source hash mismatch for {candidate['id']}")
    return {"valid": True, "source_content_hash": actual}


def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    validate_provenance(candidate)
    required = ("business_question", "available_fields", "visualization_profile", "output")
    missing = sorted(field for field in required if field not in candidate)
    if missing:
        raise ValueError(f"Candidate missing required fields: {', '.join(missing)}")
    fields = candidate["available_fields"]
    if not isinstance(fields, list):
        raise ValueError("available_fields must be a list")
    names: set[str] = set()
    types = {"string", "category", "boolean", "integer", "number", "date", "datetime", "geography"}
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str):
            raise ValueError("Each available field must be an object with a name")
        if field["name"] in names:
            raise ValueError(f"Duplicate available field: {field['name']}")
        if field.get("type") not in types:
            raise ValueError(f"Unsupported available field type: {field.get('type')}")
        names.add(field["name"])
    registry = load_registry()
    validate_registry(registry)
    if candidate["registry_version"] != registry["schema_version"]:
        raise ValueError("Candidate registry version mismatch")
    schema = load_output_schema()
    if candidate["output_schema_version"] != 1 or not schema["$id"].endswith("-v1.json"):
        raise ValueError("Candidate output schema version mismatch")
    spec = candidate["output"]
    field_types = {field["name"]: field["type"] for field in fields}
    validate_visualization_spec(spec, available_fields=field_types)
    entry = next((item for item in registry["visualizations"] if item["id"] == spec["visualization_id"]), None)
    if entry is None:
        raise ValueError(f"Unsupported registry visualization: {spec['visualization_id']}")
    if candidate["visualization_category"] != entry["category"]:
        raise ValueError("Candidate visualization_category does not match the registry category")
    if candidate.get("split") not in EXPORT_SPLITS:
        raise ValueError("Candidate split must be train, validation, or evaluation")
    if candidate.get("record_type", "positive") not in {"positive", "negative"}:
        raise ValueError("record_type must be positive or negative")
    if candidate.get("record_type", "positive") == "negative" and not candidate.get("negative_reason"):
        raise ValueError("Negative candidates require negative_reason")
    return {"valid": True, "example_id": candidate["id"], "visualization_id": spec["visualization_id"]}


def generate_qwen_chat_prompt(candidate: dict[str, Any]) -> dict[str, Any]:
    validate_candidate(candidate)
    user_context = {
        "business_question": candidate["business_question"],
        "available_fields": [{"name": f["name"], "type": f["type"]} for f in candidate["available_fields"]],
        "analytical_profile": candidate["visualization_profile"],
    }
    return {
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "example_id": candidate["id"],
        "registry_version": candidate["registry_version"],
        "output_schema_version": candidate["output_schema_version"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the InsightOS BI Visualization and Presentation Specialist. "
                    "Use only supplied registry identifiers and fields. Never invent columns, "
                    "metrics, or unsupported configuration. Output only the versioned JSON "
                    "visualization specification."
                ),
            },
            {"role": "user", "content": json.dumps(user_context, sort_keys=True, separators=(",", ":"))},
            {"role": "assistant", "content": json.dumps(candidate["output"], sort_keys=True, separators=(",", ":"))},
        ],
    }


def load_candidates(path: Path) -> list[dict[str, Any]]:
    path = path.expanduser()
    if not path.is_file():
        return []
    candidates = []
    ids, hashes = set(), set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"{path.name}:{line_number}: blank lines are not allowed")
        candidate = json.loads(line)
        example_id = candidate.get("id")
        if example_id in ids:
            raise ValueError(f"Duplicate candidate example id: {example_id}")
        ids.add(example_id)
        digest = candidate.get("source_content_hash")
        if digest in hashes:
            raise ValueError("Duplicate candidate source content hash")
        if isinstance(digest, str):
            hashes.add(digest)
        candidates.append(candidate)
    return candidates


def write_candidates(path: Path, candidates: Iterable[dict[str, Any]]) -> Path:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(candidates, key=lambda item: item["id"])
    fd, staging_name = tempfile.mkstemp(prefix=".candidates-", suffix=".tmp", dir=path.parent)
    staging = Path(staging_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in ordered))
        os.replace(staging, path)
        staging = None
    finally:
        if staging is not None and staging.exists():
            root = path.parent.expanduser().resolve(strict=False)
            resolved = staging.expanduser().resolve(strict=False)
            if resolved != root and root in resolved.parents and resolved.name.startswith(".candidates-"):
                staging.unlink()
    return path


def record_review(
    candidates_path: Path,
    *,
    example_id: str,
    decision: str,
    reviewer: str,
    reason: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    candidates = load_candidates(candidates_path)
    allowed = {
        "draft": {"pending_review"},
        "pending_review": {"approved", "rejected"},
        "approved": set(),
        "rejected": set(),
    }
    for candidate in candidates:
        if candidate["id"] == example_id:
            if decision not in allowed.get(candidate["review_status"], set()):
                raise ValueError(f"Invalid approval-state transition: {candidate['review_status']} -> {decision}")
            candidate["review_status"] = decision
            candidate["reviewer_identity"] = reviewer
            candidate["review_timestamp"] = timestamp or utc_now()
            if decision == "approved":
                candidate["approval_timestamp"] = timestamp or utc_now()
                candidate.pop("rejection_reason", None)
            elif decision == "rejected":
                candidate["rejection_reason"] = reason or "Rejected during human review"
                candidate.pop("approval_timestamp", None)
            validate_candidate(candidate)
            write_candidates(candidates_path, candidates)
            return candidate
    raise ValueError(f"Candidate example not found: {example_id}")


def export_approved_dataset(candidates_path: Path, output_dir: Path) -> dict[str, Any]:
    candidates = load_candidates(candidates_path)
    approved = []
    for candidate in candidates:
        validate_candidate(candidate)
        if candidate["review_status"] == "approved":
            approved.append(candidate)
    if not approved:
        raise ValueError("Approved visualization export refused: zero explicitly approved examples")
    split_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in EXPORT_SPLITS}
    templates, sources, ids, output_hashes = {}, {}, set(), set()
    distributions: dict[str, int] = {}
    for candidate in approved:
        verify_source_hash(candidate)
        if candidate["id"] in ids:
            raise ValueError(f"Duplicate approved example id: {candidate['id']}")
        ids.add(candidate["id"])
        content_hash = hashlib.sha256(canonical_json(candidate["output"])).hexdigest()
        if content_hash in output_hashes:
            raise ValueError("Duplicate approved example content hash")
        output_hashes.add(content_hash)
        template, source = candidate["template_group"], candidate["source_dataset_id"]
        split = candidate["split"]
        if template in templates and templates[template] != split:
            raise ValueError(f"Template leakage across splits: {template}")
        if source in sources and sources[source] != split:
            raise ValueError(f"Source dataset leakage across splits: {source}")
        templates[template], sources[source] = split, split
        split_rows[split].append(candidate)
        category = candidate["visualization_category"]
        distributions[category] = distributions.get(category, 0) + 1
    if not all(split_rows.values()):
        raise ValueError("Approved export requires train, validation, and evaluation splits")
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise ValueError(f"Export directory already exists and cannot be overwritten: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".visualization-export-", dir=output_dir.parent))
    try:
        dataset_hashes, split_counts = {}, {}
        for split in EXPORT_SPLITS:
            rows = [generate_qwen_chat_prompt(item) for item in sorted(split_rows[split], key=lambda item: item["id"])]
            path = staging / f"{split}.jsonl"
            path.write_text(
                "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8"
            )
            dataset_hashes[f"{split}.jsonl"] = sha256_file(path)
            split_counts[split] = len(rows)
        manifest = {
            "schema_version": 1,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "registry_version": load_registry()["schema_version"],
            "output_schema_version": 1,
            "export_timestamp": utc_now(),
            "approved_only": True,
            "example_count": len(approved),
            "category_distribution": dict(sorted(distributions.items())),
            "split_counts": split_counts,
            "dataset_sha256": dict(sorted(dataset_hashes.items())),
            "template_groups": dict(sorted(templates.items())),
            "source_datasets": dict(sorted(sources.items())),
            "example_ids": sorted(ids),
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest["manifest_sha256"] = sha256_file(manifest_path)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.rename(staging, output_dir)
        staging = None
        return manifest
    finally:
        _cleanup_staging(staging, output_dir.parent)
