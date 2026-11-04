from __future__ import annotations

import re
from typing import Any

EXPECTED_MODEL = "unsloth/Qwen3-0.6B-unsloth-bnb-4bit"


def validate_exchange_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("model", {}).get("identifier") != EXPECTED_MODEL:
        raise ValueError("Incorrect adapter base model identity")
    model_revision = manifest["model"].get("revision")
    if model_revision is None and manifest["model"].get("revision_status") != "not_recorded":
        raise ValueError("Unknown model revision must use revision_status=not_recorded")
    synthetic = manifest["dataset"]["synthetic"]
    approved = manifest["dataset"]["approved"]
    if synthetic and approved:
        raise ValueError("Synthetic data cannot be marked approved")
    if not synthetic and not approved:
        raise ValueError("Real datasets require approved status")
    digest = manifest["dataset"].get("sha256")
    if digest is None:
        if not synthetic or approved:
            raise ValueError("Missing dataset hash forbidden for real or approved data")
        if manifest["dataset"].get("hash_status") != "not_recorded":
            raise ValueError("Unknown synthetic hash must use hash_status=not_recorded")
    elif not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("dataset.sha256 must be a lowercase SHA-256")
    if manifest.get("production_ready") is not False:
        raise ValueError("Imported synthetic adapter must not be production-ready")
    return {"valid": True, "production_ready": False}
