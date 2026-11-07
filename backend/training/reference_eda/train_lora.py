from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import random
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from training.reference_eda.validate_dataset import validate_dataset
from training.reference_eda.review_workflow import (
    DEFAULT_HISTORY_PATH,
    validate_approved_artifacts,
)


PACKAGE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_DIR.parents[2]
CONFIG_DIR = PACKAGE_DIR / "configs"
PINNED_PACKAGES = {
    "accelerate": "1.6.0",
    "bitsandbytes": "0.45.5",
    "peft": "0.15.2",
    "safetensors": "0.5.3",
    "torch": "2.6.0",
    "transformers": "4.51.3",
}
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_APPROVAL_PATTERN = re.compile(
    r"(?:^|;)\s*reviewer=([^;]+);\s*reviewed_on=(\d{4}-\d{2}-\d{2})(?:;|$)"
)


def _read_config(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"{config_path} must contain a JSON object")
    if not _SHA_PATTERN.fullmatch(config.get("revision", "")):
        raise ValueError("Model revision must be a pinned 40-character commit SHA")
    if config.get("platform") != "linux-x86_64-cuda":
        raise ValueError("Only the explicitly configured Linux CUDA platform is supported")
    if config.get("quantization", {}).get("backend") != "bitsandbytes":
        raise ValueError("The training backend must match the pinned bitsandbytes path")
    if config.get("training", {}).get("external_reporting") is not False:
        raise ValueError("External training reporting must remain disabled")
    if config.get("training", {}).get("assistant_completion_only_loss") is not True:
        raise ValueError("Assistant-completion-only loss must remain enabled")
    return config


def _read_split(path: Path) -> list[dict[str, Any]]:
    examples = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                raise ValueError(f"{path.name}:{line_number}: blank lines are not allowed")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{line_number}: expected an object")
            examples.append(value)
    return examples


def _review_errors(
    examples: list[dict[str, Any]],
    *,
    allowed_statuses: set[str],
    required_status_description: str,
) -> list[str]:
    errors = []
    for example in examples:
        notes = example.get("reviewer_notes", "")
        match = _APPROVAL_PATTERN.search(notes) if isinstance(notes, str) else None
        if example.get("validation_status") not in allowed_statuses or match is None:
            errors.append(
                f"{example.get('id', '<unknown>')}: needs {required_status_description} "
                "status plus "
                "reviewer=NAME; reviewed_on=YYYY-MM-DD in reviewer_notes"
            )
            continue
        if not match.group(1).strip():
            errors.append(f"{example['id']}: reviewer name must not be empty")
        try:
            date.fromisoformat(match.group(2))
        except ValueError:
            errors.append(f"{example['id']}: reviewed_on must be a real ISO date")
    return errors


def _approval_errors(examples: list[dict[str, Any]]) -> list[str]:
    return _review_errors(
        examples,
        allowed_statuses={"approved"},
        required_status_description="approved",
    )


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in PINNED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _git_revision() -> str | None:
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preflight(config_path: Path) -> dict[str, Any]:
    config = _read_config(config_path)
    blockers: list[str] = []
    warnings: list[str] = []
    python_version = tuple(sys.version_info[:3])
    if not ((3, 10, 0) <= python_version < (3, 13, 0)):
        blockers.append("Pinned training environment requires Python >=3.10,<3.13")
    if sys.platform != "linux" or platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        blockers.append("QLoRA configuration requires Linux x86_64")

    dataset_dir = (REPOSITORY_ROOT / config["dataset_directory"]).resolve()
    version_match = re.fullmatch(
        r"insightos-reference-eda-v([1-9][0-9]*)",
        config.get("dataset_version", ""),
    )
    expected_dataset_dir = (
        PACKAGE_DIR / f"dataset_v{version_match.group(1)}"
        if version_match is not None
        else None
    )
    if expected_dataset_dir is None or dataset_dir != expected_dataset_dir.resolve():
        blockers.append(
            "Training data path must match its versioned reference_eda dataset"
        )
    else:
        report_path = dataset_dir / "dataset_report.json"
        try:
            dataset_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            blockers.append(f"Dataset release report is unavailable or invalid: {error}")
        else:
            if dataset_report.get("dataset_version") != config["dataset_version"]:
                blockers.append(
                    "Configured dataset version does not match dataset_report.json"
                )
    try:
        source_validation = validate_dataset(dataset_dir, require_approved=False)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        blockers.append(f"Dataset validation failed: {error}")
        source_validation = None
    if source_validation is not None and not source_validation.get(
        "release_manifest_validated"
    ):
        blockers.append("Dataset release manifest is missing or was not validated")

    approved_dataset_dir = dataset_dir / "approved_only"
    approved_validation: dict[str, Any] | None = None
    if source_validation is not None:
        approval_state = validate_approved_artifacts(
            approved_dataset_dir,
            dataset_dir,
            DEFAULT_HISTORY_PATH,
        )
        if approval_state["ready"]:
            try:
                approved_validation = validate_dataset(
                    approved_dataset_dir,
                    require_approved=True,
                    require_evaluation_review=True,
                )
            except (OSError, ValueError, json.JSONDecodeError) as error:
                blockers.append(f"Approved dataset validation failed: {error}")
        if approved_validation is None:
            blockers.append(
                "Training examples lack explicit human approval in the "
                "authoritative review workflow"
            )
            blockers.extend(approval_state["blockers"])

    dataset_dir = approved_dataset_dir if approved_validation is not None else dataset_dir
    validation = approved_validation or source_validation

    train_path = dataset_dir / "train.jsonl"
    validation_path = dataset_dir / "validation.jsonl"
    test_path = dataset_dir / "held_out.jsonl"
    training_examples: list[dict[str, Any]] = []
    dataset_hashes: dict[str, str] = {}
    if validation is not None and approved_validation is not None:
        training_examples = _read_split(train_path)
        validation_examples = _read_split(validation_path)
        held_out_examples = _read_split(test_path)
        for path in (train_path, validation_path, test_path):
            dataset_hashes[path.name] = _sha256(path)
        approval_errors = _approval_errors(training_examples)
        if approval_errors:
            blockers.append(
                f"{len(approval_errors)} training examples lack explicit human approval"
            )
        if not training_examples:
            blockers.append("No approved training examples are available")
        for split_name, split_examples in (
            ("validation", validation_examples),
            ("held-out", held_out_examples),
        ):
            review_errors = _review_errors(
                split_examples,
                allowed_statuses={"reviewed", "approved"},
                required_status_description="reviewed or approved",
            )
            if review_errors:
                blockers.append(
                    f"{len(review_errors)} {split_name} examples lack human review"
                )
        if not validation["evaluation_review_ready"]:
            blockers.append("Held-out evaluation cases lack human review")
    elif source_validation is not None:
        for split, filename in (
            ("train", "train.jsonl"),
            ("validation", "validation.jsonl"),
            ("held-out", "held_out.jsonl"),
        ):
            path = dataset_dir / filename
            dataset_hashes[path.name] = _sha256(path)
        if not _approval_errors(_read_split(dataset_dir / "train.jsonl")):
            blockers.append(
                "Source record approval metadata is not authoritative; use the "
                "human-review workflow"
            )

    package_versions = _package_versions()
    for package, required_version in PINNED_PACKAGES.items():
        actual = package_versions[package]
        if actual is None:
            if package != "torch":
                blockers.append(f"Required package {package}=={required_version} is absent")
        elif actual.split("+", 1)[0] != required_version:
            blockers.append(
                f"Expected {package}=={required_version}, found {actual}"
            )
    torch_available = importlib.util.find_spec("torch") is not None
    cuda_available = False
    cuda_vram_gb: float | None = None
    if not torch_available:
        blockers.append("PyTorch is not installed with a CUDA-compatible build")
    else:
        import torch

        cuda_available = torch.cuda.is_available()
        if not cuda_available:
            blockers.append("CUDA is unavailable; CPU-only fine-tuning is disabled")
        else:
            try:
                cuda_vram_gb = (
                    torch.cuda.get_device_properties(0).total_memory / 1024**3
                )
            except RuntimeError as error:
                blockers.append(f"Could not inspect CUDA device memory: {error}")
            if (
                cuda_vram_gb is not None
                and cuda_vram_gb < config["minimum_cuda_vram_gb"]
            ):
                blockers.append(
                    f"GPU has {cuda_vram_gb:.1f} GiB; config requires at least "
                    f"{config['minimum_cuda_vram_gb']} GiB"
                )

    output_dir = (REPOSITORY_ROOT / config["output_directory"]).resolve()
    artifacts_dir = (REPOSITORY_ROOT / "backend" / "artifacts").resolve()
    if not output_dir.is_relative_to(artifacts_dir):
        blockers.append("Training output must remain under backend/artifacts")

    if validation is not None and validation.get("evaluation_cases", 0) == 0:
        blockers.append("The held-out human-review evaluation suite is empty")
    if config["training"].get("num_train_epochs", 0) < 1:
        blockers.append("At least one training epoch must be configured")
    if config["max_sequence_length"] < 512:
        blockers.append("Configured sequence length is too small for the task")
    if validation is not None and validation["approved_examples"] == 0:
        warnings.append("The current corpus is draft-only and must not be trained")

    return {
        "model_id": config["model_id"],
        "revision": config["revision"],
        "config_path": str(config_path),
        "python": platform.python_version(),
        "platform": f"{sys.platform}-{platform.machine()}",
        "cuda_available": cuda_available,
        "cuda_vram_gb": round(cuda_vram_gb, 2) if cuda_vram_gb is not None else None,
        "package_versions": package_versions,
        "dataset_validation": validation,
        "dataset_sha256": dataset_hashes,
        "dataset_directory": str(dataset_dir),
        "git_revision": _git_revision(),
        "output_directory": str(output_dir),
        "blockers": blockers,
        "warnings": warnings,
        "ready": not blockers,
    }


def _assistant_completion_records(
    examples: list[dict[str, Any]],
    tokenizer: Any,
    max_sequence_length: int,
) -> list[dict[str, list[int]]]:
    records: list[dict[str, list[int]]] = []
    for example in examples:
        messages = example["messages"]
        tools = example["tools"] or None
        for message_index, message in enumerate(messages):
            if message["role"] != "assistant":
                continue
            context = messages[:message_index]
            prompt_ids = tokenizer.apply_chat_template(
                context,
                tools=tools,
                tokenize=True,
                add_generation_prompt=True,
            )
            full_ids = tokenizer.apply_chat_template(
                messages[: message_index + 1],
                tools=tools,
                tokenize=True,
                add_generation_prompt=False,
            )
            full_ids = list(full_ids)
            template_prompt_ids = list(prompt_ids)
            if full_ids[: len(template_prompt_ids)] != template_prompt_ids:
                raise ValueError(
                    f"{example['id']}: tokenizer prompt is not a prefix of its target"
                )
            target_ids = full_ids[len(template_prompt_ids) :]
            prompt_ids = list(
                tokenizer.apply_chat_template(
                    context,
                    tools=tools,
                    tokenize=True,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            )
            full_ids = prompt_ids + target_ids
            if len(full_ids) > max_sequence_length:
                raise ValueError(
                    f"{example['id']}: completion example has {len(full_ids)} tokens, "
                    f"exceeding configured maximum {max_sequence_length}"
                )
            labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids) :]
            if not any(label != -100 for label in labels):
                raise ValueError(f"{example['id']}: assistant target has no loss tokens")
            records.append({"input_ids": full_ids, "labels": labels})
    if not records:
        raise ValueError("No assistant completions were created from the approved data")
    return records


class _CompletionDataset:
    def __init__(self, rows: list[dict[str, list[int]]]) -> None:
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, list[int]]:
        return self.rows[index]


def _collator(tokenizer: Any, torch: Any) -> Any:
    class CompletionCollator:
        def __call__(self, features: list[dict[str, list[int]]]) -> dict[str, Any]:
            max_length = max(len(feature["input_ids"]) for feature in features)
            input_rows = []
            label_rows = []
            attention_rows = []
            for feature in features:
                padding = max_length - len(feature["input_ids"])
                input_rows.append(
                    feature["input_ids"]
                    + [tokenizer.pad_token_id] * padding
                )
                label_rows.append(feature["labels"] + [-100] * padding)
                attention_rows.append([1] * len(feature["input_ids"]) + [0] * padding)
            return {
                "input_ids": torch.tensor(input_rows, dtype=torch.long),
                "labels": torch.tensor(label_rows, dtype=torch.long),
                "attention_mask": torch.tensor(attention_rows, dtype=torch.long),
            }

    return CompletionCollator()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_training(config_path: Path) -> dict[str, Any]:
    readiness = preflight(config_path)
    if not readiness["ready"]:
        raise RuntimeError(
            "Training blocked by preflight: " + "; ".join(readiness["blockers"])
        )
    config = _read_config(config_path)
    dataset_dir = Path(readiness["dataset_directory"])
    training_examples = _read_split(dataset_dir / "train.jsonl")
    validation_examples = _read_split(dataset_dir / "validation.jsonl")
    approval_errors = _approval_errors(training_examples)
    if approval_errors:
        raise ValueError("Training approval changed after preflight")

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    seed = int(config["training"]["seed"])
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    set_seed(seed)

    output_dir = Path(readiness["output_directory"])
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "run_manifest.json"
    manifest: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model_id": config["model_id"],
        "model_revision": config["revision"],
        "config": config,
        "dataset_sha256": readiness["dataset_sha256"],
        "dataset_examples": {
            "train": len(training_examples),
            "validation": len(validation_examples),
        },
        "assistant_completion_examples": None,
        "git_revision": readiness["git_revision"],
        "python": platform.python_version(),
        "packages": _package_versions(),
        "seed": seed,
        "external_reporting": False,
    }
    _write_json(manifest_path, manifest)
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            config["model_id"],
            revision=config["revision"],
            trust_remote_code=False,
        )
        tokenizer.padding_side = "right"
        compute_dtype = (
            torch.bfloat16
            if torch.cuda.is_bf16_supported()
            else torch.float16
        )
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type=config["quantization"]["quant_type"],
            bnb_4bit_use_double_quant=config["quantization"]["double_quant"],
            bnb_4bit_compute_dtype=compute_dtype,
        )
        model = AutoModelForCausalLM.from_pretrained(
            config["model_id"],
            revision=config["revision"],
            quantization_config=quantization_config,
            device_map="auto",
            torch_dtype=compute_dtype,
            trust_remote_code=False,
        )
        model.config.use_cache = False
        model = prepare_model_for_kbit_training(
            model,
            use_gradient_checkpointing=config["training"][
                "gradient_checkpointing"
            ],
        )
        model = get_peft_model(
            model,
            LoraConfig(
                task_type="CAUSAL_LM",
                r=config["lora"]["rank"],
                lora_alpha=config["lora"]["alpha"],
                lora_dropout=config["lora"]["dropout"],
                target_modules=config["lora"]["target_modules"],
                bias="none",
            ),
        )
        train_rows = _assistant_completion_records(
            training_examples,
            tokenizer,
            config["max_sequence_length"],
        )
        validation_rows = _assistant_completion_records(
            validation_examples,
            tokenizer,
            config["max_sequence_length"],
        )
        manifest["assistant_completion_examples"] = {
            "train": len(train_rows),
            "validation": len(validation_rows),
        }
        _write_json(manifest_path, manifest)
        use_bfloat16 = compute_dtype == torch.bfloat16
        training_args = TrainingArguments(
            output_dir=str(output_dir / "checkpoints"),
            per_device_train_batch_size=config["training"][
                "per_device_train_batch_size"
            ],
            per_device_eval_batch_size=config["training"][
                "per_device_eval_batch_size"
            ],
            gradient_accumulation_steps=config["training"][
                "gradient_accumulation_steps"
            ],
            learning_rate=config["training"]["learning_rate"],
            num_train_epochs=config["training"]["num_train_epochs"],
            warmup_ratio=config["training"]["warmup_ratio"],
            weight_decay=config["training"]["weight_decay"],
            logging_steps=config["training"]["logging_steps"],
            evaluation_strategy="epoch",
            save_strategy="epoch",
            save_total_limit=2,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            gradient_checkpointing=config["training"]["gradient_checkpointing"],
            bf16=use_bfloat16,
            fp16=not use_bfloat16,
            optim="paged_adamw_8bit",
            seed=seed,
            data_seed=seed,
            report_to=[],
            remove_unused_columns=False,
        )
        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=_CompletionDataset(train_rows),
            eval_dataset=_CompletionDataset(validation_rows),
            data_collator=_collator(tokenizer, torch),
        )
        train_result = trainer.train()
        evaluation = trainer.evaluate()
        adapter_dir = output_dir / "adapter"
        trainer.save_model(str(adapter_dir))
        tokenizer.save_pretrained(adapter_dir)
        metrics = {
            "train": train_result.metrics,
            "evaluation": evaluation,
        }
        _write_json(output_dir / "metrics.json", metrics)
        manifest["status"] = "completed"
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["adapter_directory"] = str(adapter_dir)
        _write_json(manifest_path, manifest)
        return manifest
    except Exception as error:
        manifest["status"] = "failed"
        manifest["failure_type"] = type(error).__name__
        manifest["failed_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(manifest_path, manifest)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preflight or explicitly launch review-gated Qwen3 LoRA training."
    )
    parser.add_argument(
        "--model",
        choices=("qwen3-0.6b", "qwen3-4b", "qwen3-8b"),
        default="qwen3-0.6b",
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="Actually download the pinned checkpoint and start QLoRA training.",
    )
    args = parser.parse_args()
    config_path = CONFIG_DIR / f"{args.model}.json"
    try:
        report = preflight(config_path)
        if not args.train:
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["ready"] else 2
        if not report["ready"]:
            print(json.dumps(report, indent=2, sort_keys=True), file=sys.stderr)
            return 2
        result = run_training(config_path)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Training not started or failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
