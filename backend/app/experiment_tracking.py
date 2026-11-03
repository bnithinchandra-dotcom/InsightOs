from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal


ExperimentKind = Literal["supervised", "forecasting", "llm"]


@lru_cache(maxsize=1)
def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision if len(revision) == 40 else None


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_experiment_record(
    *,
    kind: ExperimentKind,
    model: dict[str, Any],
    dataset: dict[str, Any] | None,
    split: dict[str, Any] | None,
    seed: int | None,
    configuration: dict[str, Any],
    metrics: dict[str, Any],
    artifact_references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create reproducibility metadata without storing source rows or values."""
    inputs = {
        "kind": kind,
        "model": model,
        "dataset": dataset,
        "split": split,
        "seed": seed,
        "configuration": configuration,
    }
    return {
        "schema_version": 1,
        "experiment_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "status": "completed",
        "input_fingerprint": _fingerprint(inputs),
        "model": model,
        "dataset": dataset,
        "split": split,
        "random_seed": seed,
        "configuration": configuration,
        "metrics": metrics,
        "artifact_references": artifact_references or [],
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "git_revision": _git_revision(),
        },
        "promotion": {
            "status": "requires_human_review",
            "automatic_promotion": False,
        },
    }


def persist_experiment_record(
    record: dict[str, Any],
    directory: Path | None = None,
) -> Path:
    experiment_id = record.get("experiment_id")
    try:
        uuid.UUID(experiment_id)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("An experiment record must have a valid UUID.") from None
    destination_directory = directory or (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / "evaluation-results"
        / "experiments"
    )
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination = destination_directory / f"{experiment_id}.json"
    serialized = json.dumps(
        record,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    if destination.exists():
        if destination.read_text(encoding="utf-8") != serialized:
            raise ValueError("The experiment ID already exists with different metadata.")
        return destination
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination_directory,
            prefix=f".{experiment_id}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            if destination.read_text(encoding="utf-8") != serialized:
                raise ValueError(
                    "The experiment ID already exists with different metadata."
                ) from None
        return destination
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def assess_baseline_promotion(
    *,
    candidate_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    metric: str,
    direction: Literal["minimize", "maximize"],
    minimum_sample_count: int,
    sample_count: int,
) -> dict[str, Any]:
    """Return a review recommendation; this function never promotes a model."""
    candidate = candidate_metrics.get(metric)
    baseline = baseline_metrics.get(metric)
    valid = (
        isinstance(candidate, (int, float))
        and not isinstance(candidate, bool)
        and isinstance(baseline, (int, float))
        and not isinstance(baseline, bool)
    )
    if not valid:
        return {
            "status": "not_recommended",
            "reason": f"Comparable finite {metric} metrics are required.",
            "automatic_promotion": False,
        }
    if sample_count < minimum_sample_count:
        return {
            "status": "not_recommended",
            "reason": "The held-out sample count is below the review minimum.",
            "automatic_promotion": False,
        }
    candidate_value = float(candidate)
    baseline_value = float(baseline)
    if not all(map(math.isfinite, (candidate_value, baseline_value))):
        return {
            "status": "not_recommended",
            "reason": f"Comparable finite {metric} metrics are required.",
            "automatic_promotion": False,
        }
    improves = (
        candidate_value < baseline_value
        if direction == "minimize"
        else candidate_value > baseline_value
    )
    return {
        "status": "eligible_for_human_review" if improves else "not_recommended",
        "metric": metric,
        "direction": direction,
        "candidate_value": candidate_value,
        "baseline_value": baseline_value,
        "sample_count": sample_count,
        "minimum_sample_count": minimum_sample_count,
        "automatic_promotion": False,
    }
