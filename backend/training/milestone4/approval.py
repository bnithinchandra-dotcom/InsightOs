from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_REVIEWER_ROLES = {
    "project_owner",
    "senior_business_analyst",
}


def canonical_dataset_sha256(
    dataset_version: str,
    file_hashes: dict[str, str],
) -> str:
    if not dataset_version:
        raise ValueError("Dataset version must not be empty")
    if not file_hashes:
        raise ValueError("At least one dataset artifact is required")
    for name, digest in file_hashes.items():
        if (
            not name
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or not _SHA256.fullmatch(digest)
        ):
            raise ValueError(f"Invalid dataset artifact hash entry: {name!r}")
    payload = {
        "dataset_version": dataset_version,
        "files": dict(sorted(file_hashes.items())),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_files(
    dataset_dir: Path,
    filenames: tuple[str, ...],
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for filename in filenames:
        if (
            Path(filename).name != filename
            or "/" in filename
            or "\\" in filename
        ):
            raise ValueError(f"Unsafe dataset artifact name: {filename!r}")
        artifact = dataset_dir / filename
        if not artifact.is_file() or artifact.is_symlink():
            raise FileNotFoundError(f"Dataset artifact is missing or unsafe: {artifact}")
        hashes[filename] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return hashes


def validate_dataset_attestation(
    manifest_path: Path,
    *,
    dataset_id: str,
    dataset_version: str,
    expected_dataset_sha256: str,
    expected_files: dict[str, str],
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != "insightos-dataset-validation-v1":
        raise ValueError("Unsupported dataset validation manifest version")
    matches = [
        item
        for item in manifest.get("datasets", [])
        if isinstance(item, dict)
        and item.get("dataset_id") == dataset_id
        and item.get("dataset_version") == dataset_version
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one dataset validation record")
    record = matches[0]
    if record.get("dataset_sha256") != expected_dataset_sha256:
        raise ValueError("Dataset validation record is bound to another dataset hash")
    if record.get("file_sha256") != expected_files:
        raise ValueError("Dataset validation record file hashes do not match")
    expected_hash = canonical_dataset_sha256(dataset_version, expected_files)
    if expected_hash != expected_dataset_sha256:
        raise ValueError("Expected dataset hash is not canonical for its file hashes")

    human_validation = record.get("human_validation")
    if (
        not isinstance(human_validation, dict)
        or human_validation.get("status")
        not in {
            "owner_reported_dataset_level",
            "partial_owner_reported_dataset_level",
        }
    ):
        raise ValueError("Human validation provenance is missing")
    attestations = human_validation.get("attestations")
    if not isinstance(attestations, list) or len(attestations) != len(
        _REQUIRED_REVIEWER_ROLES
    ):
        raise ValueError("Exactly one attestation per reviewer role is required")
    roles = {item.get("reviewer_role") for item in attestations if isinstance(item, dict)}
    if roles != _REQUIRED_REVIEWER_ROLES:
        raise ValueError(
            "Reviewer roles must include one project owner and one senior Business Analyst"
        )
    for item in attestations:
        if (
            not isinstance(item, dict)
            or item.get("dataset_version") != dataset_version
            or item.get("dataset_sha256") != expected_dataset_sha256
            or item.get("approval_state") not in {
                "owner_reported_human_validated",
                "owner_reported_for_source_scope_only",
            }
            or not isinstance(item.get("review_scope"), str)
            or not item["review_scope"].strip()
            or not isinstance(item.get("attestation_source"), str)
            or not item["attestation_source"].strip()
            or item.get("reviewer_identity") is not None
            or item.get("historical_review_date") is not None
            or not isinstance(item.get("attestation_recorded_on"), str)
        ):
            raise ValueError("Human validation provenance is incomplete or fabricated")

    training = record.get("training_authorization")
    if (
        not isinstance(training, dict)
        or training.get("authorized") is not False
        or not isinstance(training.get("reason"), str)
        or not training["reason"].strip()
    ):
        raise ValueError("Dataset validation must not implicitly authorize training")
    return {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "dataset_sha256": expected_dataset_sha256,
        "human_validation_status": human_validation.get("status"),
        "reviewer_roles": sorted(roles),
        "training_authorized": False,
    }
