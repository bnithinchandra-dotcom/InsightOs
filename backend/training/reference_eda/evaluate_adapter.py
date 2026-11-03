from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from training.evaluation.hf_runner import (
    Generation,
    _final_answer_text,
    _has_reasoning_leak,
    _has_think_tags,
    _truncation_stage,
    evaluate_cases_with_retries,
    load_suite,
)
from training.reference_eda.build_dataset import DEFAULT_OUTPUT_DIR
from training.reference_eda.corpus import SYSTEM_MESSAGE
from training.reference_eda.train_lora import (
    CONFIG_DIR,
    REPOSITORY_ROOT,
    _package_versions,
    preflight,
)
from training.reference_eda.validate_dataset import validate_dataset


MAX_NEW_TOKENS = 384
RESULTS_DIRECTORY = REPOSITORY_ROOT / "backend" / "artifacts" / "evaluation-results"
VALID_STATUSES = ("passed", "failed", "error", "skipped", "needs_review")


def _suite_path(dataset_dir: Path) -> Path:
    return dataset_dir / "human_review_suite.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generate_with_adapter(
    torch: Any,
    tokenizer: Any,
    model: Any,
):
    def generate(case: Any, *, feedback: str | None = None) -> Generation:
        messages = [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": case.prompt},
        ]
        if feedback:
            messages.append({"role": "user", "content": feedback})
        inputs = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
            enable_thinking=False,
        )
        inputs = inputs.to(model.device)
        input_length = inputs["input_ids"].shape[1]
        started = time.perf_counter()
        with torch.inference_mode():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                do_sample=False,
                eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.pad_token_id,
            )
        duration_ms = (time.perf_counter() - started) * 1000
        token_ids = output_ids[0, input_length:].tolist()
        text = tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        reached_eos = bool(token_ids and token_ids[-1] == tokenizer.eos_token_id)
        termination = (
            "eos"
            if reached_eos
            else "max_new_tokens"
            if len(token_ids) >= MAX_NEW_TOKENS
            else "unknown"
        )
        reasoning_leak = _has_reasoning_leak(text)
        token_rate = (
            len(token_ids) / (duration_ms / 1000)
            if duration_ms > 0
            else None
        )
        return Generation(
            text=text,
            generated_tokens=len(token_ids),
            duration_ms=duration_ms,
            tokens_per_second=token_rate,
            termination_reason=termination,
            reasoning_leak=reasoning_leak,
            raw_leak=_has_think_tags(text),
            final_leak=reasoning_leak,
            truncation_stage=_truncation_stage(text, termination),
            raw_text=text,
            final_text=_final_answer_text(text),
            source="reference_eda_lora_adapter",
            max_output_tokens=MAX_NEW_TOKENS,
        )

    return generate


def _build_report(
    *,
    suite_version: str,
    cases: tuple[Any, ...],
    results: list[dict[str, Any]],
    config: dict[str, Any],
    readiness: dict[str, Any],
    model_load_ms: float,
    suite_path: Path,
    adapter_dir: Path,
) -> dict[str, Any]:
    counts = {
        status: sum(result["status"] == status for result in results)
        for status in VALID_STATUSES
    }
    categories: dict[str, dict[str, int]] = {}
    for result in results:
        category = categories.setdefault(
            result["category"], {status: 0 for status in VALID_STATUSES}
        )
        category[result["status"]] += 1
    return {
        "schema_version": "insightos-hf-evaluation-report-v2",
        "suite_version": suite_version,
        "scoring_version": "insightos-hf-scoring-v9",
        "run_id": uuid.uuid4().hex,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evaluator": "reference_eda_qwen3_lora_adapter",
        "repository": {"commit": readiness["git_revision"]},
        "checkpoint": {
            "repository": config["model_id"],
            "pinned_revision": config["revision"],
            "adapter_directory": str(adapter_dir),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": _package_versions(),
            "cuda_vram_gb": readiness["cuda_vram_gb"],
        },
        "generation_settings": {
            "thinking_mode": False,
            "max_new_tokens": MAX_NEW_TOKENS,
            "do_sample": False,
            "system_prompt_sha256": hashlib.sha256(
                SYSTEM_MESSAGE.encode("utf-8")
            ).hexdigest(),
            "suite_sha256": _sha256(suite_path),
            "tool_execution": False,
        },
        "model_load_duration_ms": round(model_load_ms, 2),
        "counts": {"total_cases": len(cases), **counts},
        "categories": categories,
        "cases": results,
    }


def evaluate_adapter(
    model_key: str,
    *,
    adapter_dir: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    config_path = CONFIG_DIR / f"{model_key}.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    readiness = preflight(config_path)
    if not readiness["ready"]:
        raise RuntimeError(
            "Evaluation blocked by preflight: " + "; ".join(readiness["blockers"])
        )
    dataset_dir = Path(readiness["dataset_directory"])
    validate_dataset(dataset_dir, require_approved=False)
    suite_path = _suite_path(dataset_dir)
    suite_version, cases = load_suite(suite_path)
    adapter_dir = adapter_dir or (
        REPOSITORY_ROOT / config["output_directory"] / "adapter"
    )
    if not (adapter_dir / "adapter_config.json").is_file():
        raise FileNotFoundError(f"LoRA adapter is missing: {adapter_dir}")

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    compute_dtype = (
        torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    )
    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(
        config["model_id"],
        revision=config["revision"],
        trust_remote_code=False,
    )
    base_model = AutoModelForCausalLM.from_pretrained(
        config["model_id"],
        revision=config["revision"],
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type=config["quantization"]["quant_type"],
            bnb_4bit_use_double_quant=config["quantization"]["double_quant"],
            bnb_4bit_compute_dtype=compute_dtype,
        ),
        device_map="auto",
        torch_dtype=compute_dtype,
        trust_remote_code=False,
    )
    model = PeftModel.from_pretrained(base_model, adapter_dir)
    model.eval()
    model_load_ms = (time.perf_counter() - started) * 1000
    results = evaluate_cases_with_retries(
        cases,
        _generate_with_adapter(torch, tokenizer, model),
        max_attempts=1,
        deterministic_fallbacks=False,
    )
    report = _build_report(
        suite_version=suite_version,
        cases=cases,
        results=results,
        config=config,
        readiness=readiness,
        model_load_ms=model_load_ms,
        suite_path=suite_path,
        adapter_dir=adapter_dir,
    )
    destination = output_path or (
        RESULTS_DIRECTORY
        / f"reference-eda-{model_key}-{report['run_id']}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    report["report_path"] = str(destination)
    report["counts_by_category"] = {
        category: dict(counts) for category, counts in report["categories"].items()
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a trained EDA adapter on held-out human-review cases."
    )
    parser.add_argument(
        "--model",
        choices=("qwen3-0.6b", "qwen3-4b", "qwen3-8b"),
        default="qwen3-0.6b",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Load the pinned base model and adapter and generate held-out answers.",
    )
    parser.add_argument("--adapter-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    suite_path = _suite_path(DEFAULT_OUTPUT_DIR)
    try:
        suite_version, cases = load_suite(suite_path)
        if not args.evaluate:
            validate_dataset(DEFAULT_OUTPUT_DIR, require_approved=False)
            print(
                json.dumps(
                    {
                        "suite_version": suite_version,
                        "cases": len(cases),
                        "scorer": "human_review",
                        "model_loaded": False,
                        "message": "Pass --evaluate after training and hardware preflight.",
                    },
                    indent=2,
                )
            )
            return 0
        report = evaluate_adapter(
            args.model,
            adapter_dir=args.adapter_dir,
            output_path=args.output,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Adapter evaluation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report["counts"], sort_keys=True))
    print(f"Report saved to {report['report_path']}")
    return 0 if report["counts"]["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
