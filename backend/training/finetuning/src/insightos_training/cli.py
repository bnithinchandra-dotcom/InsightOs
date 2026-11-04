from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from .artifacts import import_kaggle_experiment, inspect_experiment, list_experiments
from .config import MODEL_NAMES
from .dataset import validate_dataset
from .governance import (
    export_approved_dataset,
    generate_qwen_chat_prompt,
    load_candidates,
    record_review,
    validate_candidate,
    validate_provenance,
    write_candidates,
)
from .hardware import inspect_hardware
from .smoke_test import run_smoke_test

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_EXPERIMENTS_DIR = Path.home() / ".cache" / "insightos" / "experiments"
VISUALIZATION_STORE = Path.home() / ".cache" / "insightos" / "visualization" / "candidates.jsonl"
VISUALIZATION_EXPORT = Path.home() / ".cache" / "insightos" / "visualization" / "approved-export"


def print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("hardware")
    config = commands.add_parser("config")
    config.add_subparsers(dest="config_action", required=True).add_parser("validate")
    model = commands.add_parser("model")
    model_sub = model.add_subparsers(dest="model_action", required=True)
    preflight = model_sub.add_parser("preflight")
    preflight.add_argument("--model", choices=MODEL_NAMES, required=True)
    dataset = commands.add_parser("dataset")
    dataset_sub = dataset.add_subparsers(dest="dataset_action", required=True)
    dataset_validate = dataset_sub.add_parser("validate")
    dataset_validate.add_argument("--approved", action="store_true")
    commands.add_parser("smoke-test")
    artifacts = commands.add_parser("artifacts")
    artifacts_sub = artifacts.add_subparsers(dest="artifacts_action", required=True)
    artifacts_sub.add_parser("list")
    artifact_import = artifacts_sub.add_parser("import-kaggle")
    artifact_import.add_argument("--archive", type=Path, required=True)
    artifact_import.add_argument("--expected-sha256", required=True)
    artifact_inspect = artifacts_sub.add_parser("inspect")
    artifact_inspect.add_argument("--experiment", required=True)
    visualization = commands.add_parser("visualization")
    visualization_sub = visualization.add_subparsers(dest="visualization_action", required=True)
    validate = visualization_sub.add_parser("validate-candidate")
    validate.add_argument("--candidate", type=Path, required=True)
    add = visualization_sub.add_parser("add-candidate")
    add.add_argument("--candidate", type=Path, required=True)
    add.add_argument("--candidates", type=Path, default=VISUALIZATION_STORE)
    pending = visualization_sub.add_parser("list-pending")
    pending.add_argument("--candidates", type=Path, default=VISUALIZATION_STORE)
    provenance = visualization_sub.add_parser("inspect-provenance")
    provenance.add_argument("--example", required=True)
    provenance.add_argument("--candidates", type=Path, default=VISUALIZATION_STORE)
    review = visualization_sub.add_parser("review")
    review.add_argument("--example", required=True)
    review.add_argument("--decision", choices=("pending_review", "approved", "rejected"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--reason")
    review.add_argument("--candidates", type=Path, default=VISUALIZATION_STORE)
    preview = visualization_sub.add_parser("prompt-preview")
    preview.add_argument("--candidate", type=Path, required=True)
    export = visualization_sub.add_parser("export-approved")
    export.add_argument("--candidates", type=Path, default=VISUALIZATION_STORE)
    export.add_argument("--output", type=Path, default=VISUALIZATION_EXPORT)
    inspect_export = visualization_sub.add_parser("inspect-export")
    inspect_export.add_argument("--export-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"hardware", "doctor"}:
            report = inspect_hardware(PACKAGE_ROOT)
            print_json(report)
            return 0
        if args.command == "config":
            pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
            project = pyproject["project"]
            lock = PACKAGE_ROOT / "uv.lock"
            if not lock.exists():
                raise ValueError("uv.lock is missing")
            print_json(
                {
                    "valid": True,
                    "python_requirement": project["requires-python"],
                    "name": project["name"],
                    "uv_lock": str(lock),
                }
            )
            return 0
        if args.command == "dataset":
            print_json(validate_dataset(PACKAGE_ROOT / "visualization", args.approved))
            return 0
        if args.command == "smoke-test":
            print_json(run_smoke_test())
            return 0
        if args.command == "artifacts":
            if args.artifacts_action == "list":
                print_json(
                    {
                        "artifacts_directory": str(EXTERNAL_EXPERIMENTS_DIR),
                        "experiments": list_experiments(EXTERNAL_EXPERIMENTS_DIR),
                    }
                )
                return 0
            if args.artifacts_action == "import-kaggle":
                report = import_kaggle_experiment(args.archive, args.expected_sha256, EXTERNAL_EXPERIMENTS_DIR)
                print_json(
                    {"registration_outcome": "registered", "experiment_id": report["experiment_id"], "manifest": report}
                )
                return 0
            print_json(inspect_experiment(EXTERNAL_EXPERIMENTS_DIR, args.experiment))
            return 0
        if args.command == "visualization":
            if args.visualization_action == "validate-candidate":
                print_json(validate_candidate(json.loads(args.candidate.read_text())))
                return 0
            if args.visualization_action == "add-candidate":
                candidate = json.loads(args.candidate.read_text())
                validate_candidate(candidate)
                existing = load_candidates(args.candidates)
                if any(item["id"] == candidate["id"] for item in existing):
                    raise ValueError("Duplicate candidate example id")
                if any(item.get("source_content_hash") == candidate.get("source_content_hash") for item in existing):
                    raise ValueError("Duplicate candidate source content hash")
                write_candidates(args.candidates, [*existing, candidate])
                print_json({"added": True, "example_id": candidate["id"]})
                return 0
            if args.visualization_action == "list-pending":
                candidates = load_candidates(args.candidates)
                pending = [item for item in candidates if item.get("review_status") in {"draft", "pending_review"}]
                print_json(
                    {
                        "count": len(pending),
                        "pending": [{"id": item["id"], "review_status": item["review_status"]} for item in pending],
                    }
                )
                return 0
            if args.visualization_action == "inspect-provenance":
                candidates = load_candidates(args.candidates)
                candidate = next((item for item in candidates if item["id"] == args.example), None)
                if candidate is None:
                    raise ValueError("Candidate example not found")
                print_json(validate_provenance(candidate) | {"provenance": candidate})
                return 0
            if args.visualization_action == "review":
                candidate = record_review(
                    args.candidates,
                    example_id=args.example,
                    decision=args.decision,
                    reviewer=args.reviewer,
                    reason=args.reason,
                )
                print_json(
                    {
                        "example_id": candidate["id"],
                        "review_status": candidate["review_status"],
                        "approval_timestamp": candidate.get("approval_timestamp"),
                    }
                )
                return 0
            if args.visualization_action == "prompt-preview":
                print_json(generate_qwen_chat_prompt(json.loads(args.candidate.read_text())))
                return 0
            if args.visualization_action == "export-approved":
                print_json(export_approved_dataset(args.candidates, args.output))
                return 0
            manifest_path = args.export_dir / "manifest.json"
            print_json(json.loads(manifest_path.read_text()))
            return 0
    except (OSError, ValueError, RuntimeError, ImportError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
