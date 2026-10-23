from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from training.evaluation.hf_runner import (
    CASE_MAX_OUTPUT_TOKENS,
    CHECKPOINT_PATH,
    SCORING_VERSION,
    _git_metadata,
    _load_local_model,
    _safe_error,
    evaluate_cases_with_retries,
    load_suite,
    _generator as _hf_generator,
)
from training.evaluation.ollama_runner import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    _token_limit_profile,
    _request_json,
    _validate_local_base_url,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIRECTORY = REPOSITORY_ROOT / "backend" / "artifacts" / "evaluation-results"
OLLAMA_MODELS = ("qwen3:4b", "qwen3:4b-instruct")
TEMPERATURES = (0.0, 0.3)
TRIALS_PER_CASE = 10
MAX_ATTEMPTS = 3
COMPARISON_SCHEMA_VERSION = "insightos-qwen3-controlled-comparison-v1"


def _configuration_descriptor(
    model: str,
    temperature: float,
    trials_per_case: int,
    ollama_models: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if model in OLLAMA_MODELS:
        profile, token_limits = _token_limit_profile(model)
        metadata = ollama_models.get(model, {})
        return {
            "model": {
                "name": model,
                "tag": model,
                "digest": metadata.get("digest"),
            },
            "temperature": temperature,
            "generation_settings": {
                "temperature": temperature,
                "think": False,
                "stream": True,
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "token_limit_profile": profile,
                "max_output_tokens_by_case": token_limits,
                "trials_per_case": trials_per_case,
            },
        }
    return {
        "model": {
            "name": "Qwen/Qwen3-0.6B",
            "checkpoint_path": str(CHECKPOINT_PATH),
        },
        "temperature": temperature,
        "generation_settings": {
            "temperature": temperature,
            "think": False,
            "stream": False,
            "timeout_seconds": None,
            "token_limit_profile": "hf_no_think",
            "max_output_tokens_by_case": CASE_MAX_OUTPUT_TOKENS,
            "trials_per_case": trials_per_case,
        },
    }


def _memory_state() -> dict[str, float]:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    if os.name != "nt":
        raise RuntimeError("Memory telemetry for this comparison is Windows-only.")
    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed.")
    gigabyte = 1024**3
    return {
        "physical_available_gb": round(status.ullAvailPhys / gigabyte, 2),
        "page_file_available_gb": round(status.ullAvailPageFile / gigabyte, 2),
        "page_file_total_gb": round(status.ullTotalPageFile / gigabyte, 2),
    }


def _resident_models(base_url: str) -> list[dict[str, Any]]:
    payload = _request_json(
        f"{base_url}/api/ps",
        timeout_seconds=min(DEFAULT_TIMEOUT_SECONDS, 30),
    )
    models = payload.get("models", [])
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned invalid residency metadata.")
    return models


def _case_failure_types(
    case_result: dict[str, Any],
    *,
    score_name: str = "final_score",
) -> list[str]:
    categories: set[str] = set()
    for trial in case_result.get("trials", []):
        score = trial.get(score_name, {})
        if not isinstance(score, dict):
            continue
        category = score.get("failure_category")
        if category:
            if "json" in category or category == "json_only_violation":
                categories.add("format/schema")
            elif "formula" in category or "calculation" in category:
                categories.add("formula")
            elif category == "reasoning_leak":
                categories.add("reasoning_leak")
            elif category == "output_truncated":
                categories.add("truncation")
            elif "sentence" in category or "token" in category:
                categories.add("format/instruction")
            else:
                categories.add("content")
    return sorted(categories)


def _summary_row(configuration: dict[str, Any], case_result: dict[str, Any]) -> dict[str, Any]:
    initial_generations = [
        trial["attempts"][0]["generation"]
        for trial in case_result.get("trials", [])
        if trial.get("attempts") and trial.get("completed", True)
    ]
    count = len(initial_generations)
    configured_model = configuration.get("model")
    model_name = (
        configured_model.get("name")
        if isinstance(configured_model, dict)
        else configured_model
    )
    generation_settings = configuration.get("generation_settings", {})
    temperature = configuration.get(
        "temperature",
        generation_settings.get("temperature"),
    )
    return {
        "model": model_name,
        "model_digest": (
            configured_model.get("digest")
            if isinstance(configured_model, dict)
            else None
        ),
        "temperature": temperature,
        "case_id": case_result["id"],
        "overall_status": case_result.get("status"),
        "evaluation_status": case_result.get("evaluation_status"),
        "pass_at_1": case_result.get("pass_at_1", {}).get("rate"),
        "pass_after_retry": case_result.get("pass_after_retry", {}).get("rate"),
        "raw_leak_rate": (
            sum(bool(item.get("raw_leak")) for item in initial_generations) / count
            if count
            else None
        ),
        "final_leak_rate": (
            sum(bool(item.get("final_leak")) for item in initial_generations) / count
            if count
            else None
        ),
        "reasoning_leak_rate": (
            sum(bool(item.get("final_leak", item.get("reasoning_leak"))) for item in initial_generations) / count
            if count
            else None
        ),
        "truncation_rate": (
            sum(item.get("termination_reason") == "max_new_tokens" for item in initial_generations)
            / count
            if count
            else None
        ),
        "median_latency_ms": (
            sorted(item["duration_ms"] for item in initial_generations)[count // 2]
            if count % 2
            else (
                (
                    sorted(item["duration_ms"] for item in initial_generations)[count // 2 - 1]
                    + sorted(item["duration_ms"] for item in initial_generations)[count // 2]
                )
                / 2
                if count
                else None
            )
        ),
        "remaining_failure_types": _case_failure_types(case_result),
        "pass_at_1_failure_types": _case_failure_types(
            case_result,
            score_name="pass_at_1_score",
        ),
        "fallback_count": case_result.get("fallback_count", 0),
        "pass_at_1_count": case_result.get("pass_at_1"),
        "pass_after_retry_count": case_result.get("pass_after_retry"),
        "requested_trial_count": case_result.get("requested_trial_count"),
        "completed_trial_count": case_result.get("completed_trial_count"),
        "incomplete_trial_count": case_result.get("incomplete_trial_count"),
    }


def _write_unique_report(report: dict[str, Any], name: str) -> Path:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIRECTORY / f"{name}-{uuid.uuid4().hex}.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return path


def _result_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    statuses = ("complete", "incomplete", "not_started")
    evaluation_statuses = ("passed", "failed", "error", "skipped", "needs_review")
    return {
        "total_cases": len(results),
        **{
            status: sum(item["status"] == status for item in results)
            for status in statuses
        },
        "evaluation_status_counts": {
            status: sum(item.get("evaluation_status") == status for item in results)
            for status in evaluation_statuses
        },
    }


def _summary_trial_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    requested = sum(row.get("requested_trial_count") or 0 for row in rows)
    completed = sum(row.get("completed_trial_count") or 0 for row in rows)
    return {
        "requested": requested,
        "completed": completed,
        "incomplete": requested - completed,
    }


def _ollama_run(
    model: str,
    temperature: float,
    base_url: str,
    cases,
    suite_version: str,
    trials_per_case: int,
    model_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    api_usage: dict[str, list[dict[str, Any]]] = {}
    from training.evaluation.ollama_runner import _generator as _ollama_generator

    token_limit_profile, token_limits = _token_limit_profile(model)
    generator = _ollama_generator(
        base_url=base_url,
        model=model,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        max_output_tokens=None,
        api_usage=api_usage,
        temperature=temperature,
        max_output_tokens_by_case=token_limits,
    )
    evaluated_results = evaluate_cases_with_retries(
        cases,
        generator,
        trials_per_case=trials_per_case,
        max_attempts=MAX_ATTEMPTS,
        deterministic_fallbacks=True,
        stop_on_generation_error=True,
    )
    evaluated_by_id = {result["id"]: result for result in evaluated_results}
    results = []
    for case in cases:
        evaluated = evaluated_by_id.get(case.id)
        if evaluated is None:
            results.append(
                {
                    "id": case.id,
                    "status": "not_started",
                    "evaluation_status": None,
                    "requested_trials": trials_per_case,
                    "completed_trials": 0,
                    "requested_trial_count": trials_per_case,
                    "completed_trial_count": 0,
                    "incomplete_trial_count": trials_per_case,
                    "incomplete": True,
                    "interruption": None,
                    "trials": [],
                    "generations": [],
                    "pass_at_1": {"passed": 0, "total": 0, "rate": None},
                    "pass_after_retry": {"passed": 0, "total": 0, "rate": None},
                    "fallback_count": 0,
                }
            )
            continue
        completed = evaluated["completed_trial_count"]
        interruption_error = evaluated.get("error")
        interrupted_trial = next(
            (
                trial
                for trial in reversed(evaluated.get("trials", []))
                if not trial.get("completed", True)
            ),
            None,
        )
        evaluated_status = evaluated["status"]
        results.append(
            {
                **evaluated,
                "status": "complete" if completed == trials_per_case else "incomplete",
                "evaluation_status": evaluated_status,
                "requested_trials": trials_per_case,
                "completed_trials": completed,
                "interruption": (
                    {
                        "category": (
                            interruption_error.get("category", "unexpected")
                            if isinstance(interruption_error, dict)
                            else "unexpected"
                        ),
                        "message": (
                            interruption_error.get("message", "Generation interrupted.")[:200]
                            if isinstance(interruption_error, dict)
                            else "Generation interrupted."
                        ),
                        "trial_index": (
                            interrupted_trial.get("trial")
                            if interrupted_trial is not None
                            else completed + 1
                        ),
                    }
                    if interruption_error is not None
                    else None
                ),
            }
        )
    return {
        "suite_version": suite_version,
        "scoring_version": SCORING_VERSION,
        "model": {
            "name": model,
            "tag": model,
            "digest": (model_metadata or {}).get("digest"),
        },
        "evaluator": "local_ollama",
        "generation_settings": {
            "temperature": temperature,
            "think": False,
            "thinking_mode_request_field": "think=false",
            "stream": True,
            "token_limit_profile": token_limit_profile,
            "max_output_tokens_by_case": token_limits,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "trials_per_case": trials_per_case,
            "max_attempts_including_initial": MAX_ATTEMPTS,
            "deterministic_fallback_case_ids": [
                "repeat_exact_token",
                "json_profit_margin",
                "missing_churn_inputs",
            ],
        },
        "counts": _result_counts(results),
        "trial_counts": {
            "requested": len(cases) * trials_per_case,
            "completed": sum(item["completed_trial_count"] for item in results),
            "incomplete": len(cases) * trials_per_case
            - sum(item["completed_trial_count"] for item in results),
            "requested_per_case": trials_per_case,
        },
        "cases": results,
    }


def _hf_run(
    torch_module: Any,
    model: Any,
    tokenizer: Any,
    temperature: float,
    cases,
    suite_version: str,
    trials_per_case: int,
) -> dict[str, Any]:
    results = evaluate_cases_with_retries(
        cases,
        _hf_generator(torch_module, tokenizer, model, temperature=temperature),
        trials_per_case=trials_per_case,
        max_attempts=MAX_ATTEMPTS,
        deterministic_fallbacks=True,
    )
    return {
        "suite_version": suite_version,
        "scoring_version": SCORING_VERSION,
        "model": {
            "name": "Qwen/Qwen3-0.6B",
            "checkpoint_path": str(CHECKPOINT_PATH),
        },
        "evaluator": "local_hugging_face_transformers",
        "generation_settings": {
            "temperature": temperature,
            "enable_thinking": False,
            "think": False,
            "stream": False,
            "timeout_seconds": None,
            "token_limit_profile": "hf_no_think",
            "max_output_tokens_by_case": CASE_MAX_OUTPUT_TOKENS,
            "do_sample": temperature > 0,
            "trials_per_case": trials_per_case,
            "max_attempts_including_initial": MAX_ATTEMPTS,
            "deterministic_fallback_case_ids": [
                "repeat_exact_token",
                "json_profit_margin",
                "missing_churn_inputs",
            ],
        },
        "counts": _result_counts(results),
        "trial_counts": {
            "requested": len(cases) * trials_per_case,
            "completed": sum(item["completed_trial_count"] for item in results),
            "incomplete": len(cases) * trials_per_case
            - sum(item["completed_trial_count"] for item in results),
            "requested_per_case": trials_per_case,
        },
        "cases": results,
    }


def run_comparison(
    *,
    base_url: str = DEFAULT_BASE_URL,
    trials_per_case: int = TRIALS_PER_CASE,
    temperatures: tuple[float, ...] = TEMPERATURES,
) -> Path:
    local_base_url = _validate_local_base_url(base_url)
    if trials_per_case < 1:
        raise ValueError("trials_per_case must be at least one.")
    suite_version, cases = load_suite()
    available = _request_json(
        f"{local_base_url}/api/tags",
        timeout_seconds=min(DEFAULT_TIMEOUT_SECONDS, 30),
    ).get("models")
    if not isinstance(available, list):
        raise RuntimeError("Ollama returned invalid model tag metadata.")
    installed = {
        name
        for item in available
        if isinstance(item, dict)
        for name in (item.get("name"), item.get("model"))
        if isinstance(name, str)
    }
    missing = [model for model in OLLAMA_MODELS if model not in installed]
    if missing:
        raise RuntimeError("Required local Ollama models are missing: " + ", ".join(missing))
    installed_details = {
        item.get("name"): item
        for item in available
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    summary_rows = []
    run_reports = []
    configuration_settings = []
    completed_configurations: set[tuple[str, float]] = set()
    for model_name in OLLAMA_MODELS:
        for temperature in temperatures:
            before = {
                "memory": _memory_state(),
                "resident_models": _resident_models(local_base_url),
            }
            run_payload = None
            run_error = None
            try:
                run_payload = _ollama_run(
                    model_name,
                    temperature,
                    local_base_url,
                    cases,
                    suite_version,
                    trials_per_case,
                    installed_details.get(model_name),
                )
                run_payload["model"]["ollama_metadata"] = installed_details.get(model_name)
                run_payload["model"]["digest"] = (
                    installed_details.get(model_name, {}).get("digest")
                )
            except Exception as exc:
                run_error = _safe_error(exc)
            finally:
                stop = subprocess.run(
                    ["ollama", "stop", model_name],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if stop.returncode != 0:
                    run_error = run_error or {
                        "type": "OllamaStopError",
                        "message": stop.stderr.strip()[:500],
                    }
            after = {
                "memory": _memory_state(),
                "resident_models": _resident_models(local_base_url),
            }
            if run_payload is None:
                token_profile, token_limits = _token_limit_profile(model_name)
                run_payload = {
                    "suite_version": suite_version,
                    "scoring_version": SCORING_VERSION,
                    "model": {
                        "name": model_name,
                        "tag": model_name,
                        "digest": installed_details.get(model_name, {}).get("digest"),
                        "ollama_metadata": installed_details.get(model_name),
                    },
                    "evaluator": "local_ollama",
                    "generation_settings": {
                        "temperature": temperature,
                        "think": False,
                        "stream": True,
                        "token_limit_profile": token_profile,
                        "max_output_tokens_by_case": token_limits,
                        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                        "trials_per_case": trials_per_case,
                        "max_attempts_including_initial": MAX_ATTEMPTS,
                    },
                    "counts": _result_counts([]),
                    "cases": [],
                }
            report = {
                "schema_version": COMPARISON_SCHEMA_VERSION,
                "run_id": uuid.uuid4().hex,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "repository": _git_metadata(),
                "ollama_version": _request_json(
                    f"{local_base_url}/api/version",
                    timeout_seconds=10,
                ).get("version"),
                "before": before,
                "after": after,
                "run_error": run_error,
                **run_payload,
            }
            report_path = _write_unique_report(
                report,
                f"qwen3-comparison-{model_name.replace(':', '-')}-temp-{str(temperature).replace('.', '-')}",
            )
            run_reports.append(str(report_path.resolve()))
            completed_configurations.add((model_name, temperature))
            configuration_settings.append(
                {
                    "model": run_payload["model"],
                    "generation_settings": run_payload["generation_settings"],
                }
            )
            summary_rows.extend(
                _summary_row(run_payload, result)
                for result in run_payload["cases"]
            )
            print(f"Saved {report_path}; counts={json.dumps(report['counts'], sort_keys=True)}")
            if any(
                result.get("evaluation_status") == "error"
                for result in run_payload["cases"]
            ):
                summary = {
                    "schema_version": COMPARISON_SCHEMA_VERSION,
                    "run_id": uuid.uuid4().hex,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "suite_version": suite_version,
                    "status": "stopped_after_generation_error",
                    "configurations": configuration_settings,
                    "interrupted_configuration": {
                        "model": run_payload["model"],
                        "temperature": run_payload["generation_settings"]["temperature"],
                        "generation_settings": run_payload["generation_settings"],
                    },
                    "trial_counts": _summary_trial_counts(summary_rows),
                    "completed_configuration_reports": run_reports,
                    "unrun_configurations": [
                        _configuration_descriptor(
                            model,
                            value,
                            trials_per_case,
                            installed_details,
                        )
                        for model in (*OLLAMA_MODELS, "Qwen/Qwen3-0.6B (HF)")
                        for value in temperatures
                        if (model, value) not in completed_configurations
                    ],
                    "table": summary_rows,
                    "limitations": [
                        "The comparison stopped after a model generation error, as required; unrun configurations have no results.",
                        "pass_after_retry includes deterministic fallbacks for repeat_exact_token, json_profit_margin and missing_churn_inputs; pass_at_1 excludes retries and fallbacks.",
                        "Cases marked needs_review still require the suite's human rubric.",
                    ],
                }
                return _write_unique_report(summary, "qwen3-comparison-partial")

    torch_module, tokenizer, model, load_ms = _load_local_model(CHECKPOINT_PATH)
    hf_memory_before = _memory_state()
    try:
        for temperature in temperatures:
            payload = _hf_run(
                torch_module,
                model,
                tokenizer,
                temperature,
                cases,
                suite_version,
                trials_per_case,
            )
            report = {
                "schema_version": COMPARISON_SCHEMA_VERSION,
                "run_id": uuid.uuid4().hex,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "repository": _git_metadata(),
                "model_load_duration_ms": round(load_ms, 2),
                "memory_before_load": hf_memory_before,
                "memory_after_run": _memory_state(),
                **payload,
            }
            report_path = _write_unique_report(
                report,
                f"qwen3-comparison-qwen3-0-6b-hf-temp-{str(temperature).replace('.', '-')}",
            )
            run_reports.append(str(report_path.resolve()))
            completed_configurations.add(("Qwen/Qwen3-0.6B (HF)", temperature))
            configuration_settings.append(
                {
                    "model": payload["model"],
                    "generation_settings": payload["generation_settings"],
                }
            )
            summary_rows.extend(
                _summary_row(payload, result)
                for result in payload["cases"]
            )
            print(f"Saved {report_path}; counts={json.dumps(report['counts'], sort_keys=True)}")
    finally:
        del model
        del tokenizer
        gc.collect()

    summary = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "suite_version": suite_version,
        "trials_per_case_per_temperature": trials_per_case,
        "temperatures": list(temperatures),
        "reports": run_reports,
        "configurations": configuration_settings,
        "trial_counts": _summary_trial_counts(summary_rows),
        "table": summary_rows,
        "limitations": [
            "pass_at_1 and pass_after_retry are automatic validator rates; cases marked needs_review still require the suite's human rubric.",
            "pass_after_retry includes deterministic fallbacks for repeat_exact_token, json_profit_margin and missing_churn_inputs; pass_at_1 always excludes retries and fallbacks.",
            "The HF checkpoint and Ollama models use different runtimes and prompts, so their outcomes are diagnostic rather than a controlled model-only comparison.",
        ],
    }
    return _write_unique_report(summary, "qwen3-comparison-summary")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare local Qwen3 models on the unchanged InsightOS evaluation suite."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--trials-per-case", type=int, default=TRIALS_PER_CASE)
    parser.add_argument(
        "--temperatures",
        default="0,0.3",
        help="Comma-separated temperature passes; default is 0 and 0.3.",
    )
    args = parser.parse_args(argv)
    temperatures = tuple(float(value) for value in args.temperatures.split(","))
    report = run_comparison(
        base_url=args.base_url,
        trials_per_case=args.trials_per_case,
        temperatures=temperatures,
    )
    print(f"Summary saved to {report}")
    result = json.loads(report.read_text(encoding="utf-8"))
    for row in result.get("table", []):
        requested = row.get("requested_trial_count", 0)
        completed = row.get("completed_trial_count", 0)
        print(
            f"{row.get('model')} temp={row.get('temperature')} "
            f"{row.get('case_id')}: {completed}/{requested} trials; "
            f"pass@1={row.get('pass_at_1')}; "
            f"pass-after-retry={row.get('pass_after_retry')}; "
            f"median_latency_ms={row.get('median_latency_ms')}"
        )
    if result.get("status") == "stopped_after_generation_error":
        return 2
    if any(row.get("evaluation_status") == "failed" for row in result.get("table", [])):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
