from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .exchange import EXPECTED_MODEL, validate_exchange_manifest

ADAPTER_FILES = (
    "adapter_config.json",
    "adapter_model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "chat_template.jinja",
    "README.md",
)


def _cleanup_import_staging(staging: Path, storage_root: Path) -> None:
    root = storage_root.expanduser().resolve(strict=False).parent
    path = staging.expanduser().resolve(strict=False)
    if path == root or root not in path.parents:
        return
    if not path.name.startswith(".kaggle-import-"):
        return
    if path.exists():
        shutil.rmtree(path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_extract(archive: Path, staging: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        names: list[str] = []
        for info in zf.infolist():
            name = info.filename
            path = PurePosixPath(name)
            if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError(f"Unsafe archive path: {name}")
            if name in names:
                raise ValueError(f"Duplicate archive path: {name}")
            names.append(name)
        zf.extractall(staging)


def validate_safetensors(path: Path) -> None:
    try:
        from safetensors import safe_open

        with safe_open(str(path), framework="pt", device="cpu") as tensor:
            if len(tensor.keys()) == 0:
                raise ValueError("Safetensors file contains no tensors")
    except ImportError:
        with path.open("rb") as handle:
            if handle.read(8)[:8] == b"":
                raise ValueError("Invalid Safetensors file")


def import_kaggle_experiment(archive: Path, expected_sha256: str, storage_root: Path) -> dict[str, Any]:
    archive = archive.expanduser()
    if not archive.is_file():
        raise ValueError(f"Archive does not exist: {archive}")
    actual = sha256_file(archive)
    if actual != expected_sha256.lower():
        raise ValueError(f"Archive SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    try:
        if not zipfile.is_zipfile(archive):
            raise ValueError("Invalid ZIP archive")
    except zipfile.BadZipFile as error:
        raise ValueError("Invalid ZIP archive") from error
    storage_root = storage_root.expanduser()
    storage_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".kaggle-import-", dir=storage_root.parent))
    try:
        _safe_extract(archive, staging)
        kaggle_manifest_path = staging / "experiment_manifest.json"
        validation_path = staging / "validation_report.json"
        if not kaggle_manifest_path.is_file():
            raise ValueError("Missing Kaggle experiment manifest: experiment_manifest.json")
        if not validation_path.is_file():
            raise ValueError("Missing validation report: validation_report.json")
        kaggle = json.loads(kaggle_manifest_path.read_text(encoding="utf-8"))
        _validation = json.loads(validation_path.read_text(encoding="utf-8"))
        experiment_id = kaggle.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            experiment_id = archive.stem
        destination = storage_root / experiment_id
        if destination.exists():
            raise ValueError(f"Experiment already exists and cannot be overwritten: {destination}")
        adapter_source = staging / "adapter"
        if not adapter_source.is_dir():
            raise ValueError("Missing adapter directory")
        for name in ADAPTER_FILES:
            if not (adapter_source / name).is_file():
                raise ValueError(f"Missing adapter file: adapter/{name}")
        manifest_hashes = kaggle.get("adapter_sha256", {})
        if not manifest_hashes:
            manifest_hashes = kaggle.get("adapter_file_hashes", {})
        if not manifest_hashes:
            manifest_hashes = kaggle.get("adapter", {}).get("file_hashes", {})
        for name in ADAPTER_FILES:
            expected = manifest_hashes.get(name) or manifest_hashes.get(f"adapter/{name}")
            if not isinstance(expected, str) or len(expected) != 64:
                raise ValueError(f"Missing adapter hash for adapter/{name}")
            actual_file_hash = sha256_file(adapter_source / name)
            if actual_file_hash != expected.lower():
                raise ValueError(f"Adapter file hash mismatch: adapter/{name}")
        weights = adapter_source / "adapter_model.safetensors"
        validate_safetensors(weights)
        adapter_config = json.loads((adapter_source / "adapter_config.json").read_text(encoding="utf-8"))
        base_model = adapter_config.get("base_model_name_or_path")
        if base_model != EXPECTED_MODEL:
            raise ValueError("Adapter base model identifier mismatch")
        destination_staging = staging / "experiment"
        adapter_destination = destination_staging / "adapter"
        shutil.copytree(adapter_source, adapter_destination)
        shutil.copy2(kaggle_manifest_path, destination_staging / "source_kaggle_manifest.json")
        shutil.copy2(validation_path, destination_staging / "validation_report.json")
        weights_path = adapter_destination / "adapter_model.safetensors"
        main_hash = sha256_file(weights_path)
        exchange = {
            "schema_version": 1,
            "experiment_id": experiment_id,
            "model": {
                "identifier": EXPECTED_MODEL,
                "revision": None,
                "revision_status": "not_recorded",
                "lora": adapter_config,
            },
            "dataset": {
                "synthetic": True,
                "approved": False,
                "sha256": None,
                "hash_status": "not_recorded",
                "split": "synthetic_smoke",
            },
            "adapter": {
                "directory": str(destination / "adapter"),
                "main_weight_file": str(destination / "adapter" / "adapter_model.safetensors"),
                "main_weight_sha256": main_hash,
                "main_weight_size": weights_path.stat().st_size,
                "file_hashes": {name: sha256_file(adapter_destination / name) for name in ADAPTER_FILES},
            },
            "validation_status": "artifact_integrity_validated",
            "production_ready": False,
            "status": "synthetic_experimental_adapter",
            "training": {
                "method": kaggle.get("method"),
                "quantization": kaggle.get("quantization"),
                "compute_precision": kaggle.get("compute_precision"),
                "gpu": kaggle.get("gpu"),
                "training_steps": kaggle.get("training_steps"),
                "synthetic_training_examples": kaggle.get("training_examples"),
                "sequence_length": 512,
                "micro_batch_size": 1,
                "gradient_accumulation": 1,
                "learning_rate": 0.0002,
                "lora_rank": 8,
                "lora_alpha": 16,
                "lora_dropout": 0,
                "gradient_checkpointing": True,
                "optimizer": "adamw_torch",
                "seed": 3407,
            },
            "metrics": {
                "training_loss": kaggle.get("training_loss"),
                "peak_gpu_memory_gib": kaggle.get("peak_gpu_memory_gib"),
            },
            "dependencies": kaggle.get("dependency_versions", {}),
        }
        validate_exchange_manifest(exchange)
        import_report = {
            "registration_outcome": "registered",
            "archive_sha256": actual,
            "import_timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "production_ready": False,
            "validation_status": "artifact_integrity_validated",
        }
        (destination_staging / "insightos_exchange_manifest.json").write_text(
            json.dumps(exchange, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (destination_staging / "import_report.json").write_text(
            json.dumps(import_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.rename(destination_staging, destination)
        return import_report | {"experiment_id": experiment_id, "exchange_manifest": exchange}
    finally:
        _cleanup_import_staging(staging, storage_root)


def list_experiments(root: Path) -> list[str]:
    root = root.expanduser()
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def inspect_experiment(root: Path, experiment_id: str) -> dict[str, Any]:
    path = root.expanduser() / experiment_id
    if not path.is_dir():
        raise ValueError(f"Experiment not found: {experiment_id}")
    return {
        "experiment_id": experiment_id,
        "experiment_directory": str(path),
        "exchange_manifest": json.loads((path / "insightos_exchange_manifest.json").read_text()),
        "import_report": json.loads((path / "import_report.json").read_text()),
    }
