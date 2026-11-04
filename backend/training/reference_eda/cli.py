from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from training.reference_eda.environment import (
    classify_environment,
    inspect_environment,
)
from training.reference_eda.train_lora import (
    CONFIG_DIR,
    _read_config,
    preflight,
    run_training,
)
from training.reference_eda.validate_dataset import validate_dataset

PACKAGE_DIR = Path(__file__).resolve().parent
DATASET_DIR = PACKAGE_DIR / "dataset_v1"


def config_path(model: str) -> Path:
    return CONFIG_DIR / f"{model}.json"


def print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def inspect_adapter(adapter_dir: Path) -> dict[str, object]:
    report: dict[str, object] = {
        "adapter_directory": str(adapter_dir),
        "exists": adapter_dir.is_dir(),
        "adapter_config": (adapter_dir / "adapter_config.json").is_file(),
        "weights": (adapter_dir / "adapter_model.safetensors").is_file()
        or (adapter_dir / "adapter_model.bin").is_file(),
        "tokenizer_files": any(
            (adapter_dir / name).is_file()
            for name in ("tokenizer.json", "tokenizer_config.json")
        ),
        "compatible": False,
    }
    report["compatible"] = bool(
        report["exists"]
        and report["adapter_config"]
        and report["weights"]
        and report["tokenizer_files"]
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="InsightOS isolated Qwen fine-tuning environment"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("environment-check")
    model_parser = subparsers.add_parser("model-preflight")
    model_parser.add_argument(
        "--model", choices=("qwen3-0.6b", "qwen3-4b", "qwen3-8b"), required=True
    )

    dataset_parser = subparsers.add_parser("dataset-preflight")
    dataset_parser.add_argument("--approved", action="store_true")

    dry_parser = subparsers.add_parser("dry-run")
    dry_parser.add_argument(
        "--model", choices=("qwen3-0.6b", "qwen3-4b", "qwen3-8b"), required=True
    )

    train_parser = subparsers.add_parser("train")
    train_parser.add_argument(
        "--model", choices=("qwen3-0.6b", "qwen3-4b", "qwen3-8b"), required=True
    )

    inspect_parser = subparsers.add_parser("inspect-adapter")
    inspect_parser.add_argument("adapter_directory", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "environment-check":
            report = inspect_environment()
            report["classification"] = classify_environment(report)
            print_json(report)
            return 0 if report["classification"] in {"cuda-compatible", "cpu-only-development"} else 2

        if args.command in {"model-preflight", "train", "dry-run"}:
            path = config_path(args.model)
            report = preflight(path)
            if args.command == "model-preflight":
                print_json(report)
                return 0 if report["ready"] else 2
            if args.command == "dry-run":
                _read_config(path)
                synthetic = validate_dataset(DATASET_DIR, require_approved=False)
                dry_run = {
                    "model_id": report["model_id"],
                    "config_valid": True,
                    "synthetic_smoke_test": True,
                    "dataset_examples": synthetic["examples"],
                    "approved_records_used": False,
                    "training_started": False,
                    "dataset_preflight_ready": False,
                }
                print_json(dry_run)
                return 0
            if not report["ready"]:
                print_json(report)
                return 2
            print_json(run_training(path))
            return 0

        if args.command == "dataset-preflight":
            report = validate_dataset(
                DATASET_DIR,
                require_approved=args.approved,
                require_evaluation_review=args.approved,
            )
            print_json(report)
            return 0

        if args.command == "inspect-adapter":
            print_json(inspect_adapter(args.adapter_directory))
            return 0

    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
