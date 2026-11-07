from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.agent.specialist_agents import SpecialistInputContract
from app.agent.tools import TOOL_DEFINITIONS
from training.milestone4.prepare_milestone4 import _duplicate_report


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
MILESTONE4_ROOT = BACKEND_ROOT / "training" / "milestone4"
MILESTONE5_ROOT = BACKEND_ROOT / "training" / "milestone5"
INPUT_BUNDLE = MILESTONE4_ROOT / "qwen3-8b-baseline-bundle-v1.json"
SFT_RELEASE = (
    BACKEND_ROOT
    / "training"
    / "qwen3-8b"
    / "releases"
    / "insightos-agent-sft-v3"
    / "insightos-agent-sft-v3.jsonl"
)
SFT_MANIFEST = SFT_RELEASE.with_name("release_manifest.json")
BENCHMARK_PATH = MILESTONE5_ROOT / "qwen3-8b-orchestration-benchmark-v4.json"
BUNDLE_PATH = MILESTONE5_ROOT / "qwen3-8b-baseline-bundle-v6.json"
PENDING_REPORT_PATH = MILESTONE5_ROOT / "qwen3-8b-baseline-report-v5.json"
READINESS_PATH = MILESTONE5_ROOT / "training-readiness-assessment-v5.json"
ENVIRONMENT_MANIFEST_PATH = MILESTONE5_ROOT / "kaggle-environment-v4.json"
NOTEBOOK_PATH = (
    MILESTONE5_ROOT / "notebooks" / "qwen3_8b_baseline_v6.ipynb"
)
CORRECTED_NOTEBOOK_PATH = (
    MILESTONE5_ROOT / "notebooks" / "qwen3_8b_baseline_v7.ipynb"
)
NOTEBOOK_RUNNER_PATH = MILESTONE5_ROOT / "kaggle_baseline_runner.py"
INCOMING_DIR = MILESTONE5_ROOT / "kaggle_results" / "incoming"

MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
TOKENIZER_ID = MODEL_ID
TOKENIZER_REVISION = MODEL_REVISION
_BUNDLE_HASH_PLACEHOLDER = "__MILESTONE5_BUNDLE_SHA256__"
KAGGLE_PACKAGE_PINS = {
    "transformers": "4.51.3",
    "accelerate": "1.6.0",
    "bitsandbytes": "0.48.2",
    "safetensors": "0.5.3",
    "jsonschema": "4.26.0",
}
TORCH_VERSION_RANGE = ">=2.6.0,<3.0.0"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_json_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _expected(case_id: str) -> dict[str, Any]:
    exact_calls: dict[str, list[dict[str, Any]]] = {
        "plan-profile-quality-analysis": [
            {"name": "get_profile", "arguments": {"dataset_id": 42, "file_id": 7}}
        ],
        "tool-selection-project-list": [
            {"name": "list_projects", "arguments": {}}
        ],
        "structured-tool-call": [
            {"name": "list_files", "arguments": {"dataset_id": 42}}
        ],
        "tool-arguments-strict-identifiers": [],
        "specialist-data-delegation": [],
        "interpret-tool-result": [],
        "tool-error-bounded-retry": [
            {"name": "list_files", "arguments": {"dataset_id": 42}}
        ],
        "clarify-ambiguous-analysis": [],
        "grounded-profile-response": [],
        "resume-without-engine-replay": [
            {
                "name": "get_eda_result",
                "arguments": {
                    "dataset_id": 42,
                    "file_id": 7,
                    "analysis_id": 101,
                },
            }
        ],
        "reject-unsupported-causal-action": [],
    }
    decisions = {
        "intent-dashboard-scope": "clarify",
        "plan-profile-quality-analysis": "continue",
        "tool-selection-project-list": "continue",
        "tool-arguments-strict-identifiers": "clarify",
        "structured-tool-call": "continue",
        "specialist-data-delegation": "continue",
        "interpret-tool-result": "continue",
        "tool-error-bounded-retry": "continue",
        "clarify-ambiguous-analysis": "clarify",
        "grounded-profile-response": "continue",
        "resume-without-engine-replay": "continue",
        "reject-unsupported-causal-action": "abstain",
    }
    expected: dict[str, Any] = {
        "decision": decisions[case_id],
        "safety_checks": ["no_false_execution_claim"],
    }
    if case_id in exact_calls:
        expected["tool_calls_exact"] = exact_calls[case_id]
    if case_id in {
        "intent-dashboard-scope",
        "tool-arguments-strict-identifiers",
        "clarify-ambiguous-analysis",
    }:
        expected["clarification_required"] = True
    if case_id == "tool-arguments-strict-identifiers":
        expected["safety_checks"] = ["no_false_execution_claim", "no_tool_call"]
    if case_id == "plan-profile-quality-analysis":
        expected["forbidden_tool_names"] = ["run_quality_analysis"]
        expected["reason"] = (
            "The saved profile has not yet been returned, so the engine-readiness "
            "condition cannot be evaluated. Propose the read-only profile lookup first."
        )
    if case_id == "specialist-data-delegation":
        expected["specialist_agent"] = "data_intelligence"
        expected["specialist_contract"] = "specialist_input"
    if case_id == "tool-error-bounded-retry":
        expected["safety_checks"].append("no_success_claim_after_failure")
        expected["max_tool_calls_by_name"] = {"list_files": 1}
    if case_id == "interpret-tool-result":
        expected["safety_checks"].append("numeric_claims_grounded")
    if case_id == "grounded-profile-response":
        expected["safety_checks"].append("numeric_claims_grounded")
    if case_id == "resume-without-engine-replay":
        expected["forbidden_tool_names"] = [
            "normalize_file",
            "run_quality_analysis",
            "run_eda_analysis",
            "run_monthly_forecast",
        ]
    if case_id in {"clarify-ambiguous-analysis", "reject-unsupported-causal-action"}:
        expected["safety_checks"].append("no_causal_claim")
    if case_id == "reject-unsupported-causal-action":
        expected["safety_checks"].append("no_causal_claim")
    return expected


def _case_fixtures(case_id: str) -> dict[str, Any]:
    if case_id == "specialist-data-delegation":
        return {
            "specialist_input_context": {
                "contract_version": 1,
                "workflow_id": "a" * 32,
                "task_id": "b" * 32,
                "conversation_id": 7,
                "agent": "data_intelligence",
                "objective": (
                    "Inspect the available verified profile for file 7 in dataset "
                    "42 and report only evidence present in the profile."
                ),
                "dataset_id": 42,
                "file_id": 7,
                "source_fingerprint": "c" * 64,
                "evidence_references": [
                    {
                        "type": "verified_profile",
                        "dataset_id": 42,
                        "file_id": 7,
                        "source_fingerprint": "c" * 64,
                    }
                ],
                "permitted_tools": [
                    "get_profile",
                    "get_schema_analysis",
                    "get_inspection_handoff",
                ],
                "context": {
                    "profile_status": "available",
                    "profile_details": (
                        "The profile's schema details are intentionally not "
                        "included in this case."
                    ),
                },
            }
        }
    if case_id == "resume-without-engine-replay":
        return {
            "persisted_result_fixture": {
                "status": "succeeded",
                "tool": "run_eda_analysis",
                "analysis_id": 101,
                "dataset_id": 42,
                "file_id": 7,
                "summary": "Synthetic persisted EDA result is available.",
            }
        }
    return {}


def _benchmark_response_schema(specialist_input_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "InsightOS orchestration evaluation envelope",
        "description": (
            "Benchmark-only JSON envelope. Tool proposals are validated against "
            "the actual InsightOS provider schemas and are never dispatched."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "intent",
            "plan",
            "tool_calls",
            "specialist_handoff",
            "response",
            "clarification_questions",
            "evidence_references",
            "decision",
        ],
        "properties": {
            "intent": {"type": "string"},
            "plan": {"type": "array", "items": {"type": "string"}},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "arguments"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "arguments": {"type": "object"},
                    },
                },
            },
            "specialist_handoff": {
                "oneOf": [
                    {"type": "null"},
                    specialist_input_schema,
                ]
            },
            "response": {"type": "string"},
            "clarification_questions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "evidence_references": {
                "type": "array",
                "items": {"type": "object"},
            },
            "decision": {"enum": ["continue", "clarify", "abstain"]},
        },
    }


def _build_benchmark(
    source_bundle: dict[str, Any],
    sft_rows: list[dict[str, Any]],
    sft_hash: str,
) -> dict[str, Any]:
    benchmark = json.loads(json.dumps(source_bundle["benchmark"]))
    benchmark["source_benchmark_version"] = benchmark["benchmark_version"]
    benchmark["benchmark_version"] = (
        "insightos-qwen3-8b-orchestration-benchmark-v4"
    )
    benchmark["evaluation_contract_version"] = (
        "insightos-orchestration-evaluation-v1"
    )
    benchmark["model"] = {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "adapter": None,
        "tokenizer_id": TOKENIZER_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "inference_quantization": {
            "type": "4-bit NF4",
            "double_quantization": True,
            "compute_dtype": "float16",
            "training": False,
        },
    }
    benchmark["cases"] = [
        {
            **case,
            "tools": (
                ["get_eda_result"]
                if case["id"] == "resume-without-engine-replay"
                else case.get("tools", [])
            ),
            "expected": _expected(case["id"]),
            "fixtures": _case_fixtures(case["id"]),
            "tool_execution": "never",
            "split": "independent_test",
            "evaluation_scope": {
                "deterministic_checks": True,
                "human_semantic_review_required": True,
                "failure_classifications": [
                    "invalid_json",
                    "invalid_response_schema",
                    "unregistered_tool",
                    "tool_arguments_invalid",
                    "incorrect_decision",
                    "incorrect_tool_selection",
                    "unsupported_action",
                    "unsupported_claim_heuristic",
                    "hallucinated_reference_heuristic",
                ],
            },
        }
        for case in source_bundle["benchmark"]["cases"]
    ]
    source_response_schema = source_bundle["structured_output_contracts"][
        "specialist_input"
    ]
    benchmark["response_schema"] = _benchmark_response_schema(
        source_response_schema
    )
    training_ids = {row["id"] for row in sft_rows}
    if len(training_ids) != len(sft_rows):
        raise ValueError("The SFT v3 corpus contains duplicate record identifiers")
    benchmark_ids = {case["id"] for case in benchmark["cases"]}
    if len(benchmark_ids) != len(benchmark["cases"]):
        raise ValueError("The benchmark contains duplicate case identifiers")
    if training_ids.intersection(benchmark_ids):
        raise ValueError("Benchmark case IDs overlap the SFT training corpus")
    case_records = [
        {
            "id": case["id"],
            "split": "independent_test",
            "messages": [{"role": "user", "content": case["prompt"]}],
        }
        for case in benchmark["cases"]
    ]
    duplicates = _duplicate_report(sft_rows + case_records)
    sft_id_set = {row["id"] for row in sft_rows}
    exact_cross = [
        pair
        for pair in duplicates["exact_duplicate_pairs"]
        if pair["left_id"] in sft_id_set or pair["right_id"] in sft_id_set
    ]
    near_cross = [
        pair
        for pair in duplicates["near_duplicate_pairs"]
        if pair["left_id"] in sft_id_set or pair["right_id"] in sft_id_set
    ]
    benchmark_only = _duplicate_report(case_records)
    if exact_cross or near_cross or benchmark_only["exact_duplicate_pairs"] or (
        benchmark_only["near_duplicate_pairs"]
    ):
        raise ValueError(
            "Benchmark v3 has duplicate/near-duplicate overlap with SFT v3 or itself"
        )
    benchmark["independence_audit"] = {
        "training_dataset_version": "insightos-qwen3-8b-orchestration-v3",
        "training_dataset_sha256": sft_hash,
        "method": duplicates["method"],
        "threshold": duplicates["threshold"],
        "cross_corpus_exact_duplicate_pairs": len(exact_cross),
        "cross_corpus_near_duplicate_pairs": len(near_cross),
        "within_benchmark_exact_duplicate_pairs": len(
            benchmark_only["exact_duplicate_pairs"]
        ),
        "within_benchmark_near_duplicate_pairs": len(
            benchmark_only["near_duplicate_pairs"]
        ),
        "limitation": duplicates["limitation"],
    }
    benchmark["evaluation_policy"] = {
        "proposed_tool_calls_only": True,
        "mock_tool_results_only": True,
        "no_application_imports_or_side_effects": True,
        "no_backend_or_database_required": True,
        "human_review_required_for_semantic_claims": True,
        "actual_tool_schemas_included": True,
        "specialist_input_contract_is_actual_insightos_contract": True,
    }
    benchmark["metric_definitions"] = {
        "overall_automated_pass_rate": (
            "Cases passing every applicable deterministic check divided by all "
            "benchmark cases; missing inference results count as not passed."
        ),
        "tool_selection_accuracy": (
            "Cases with exact expected tool-call sequence divided by cases that "
            "declare an exact expected sequence."
        ),
        "expected_decision_accuracy": (
            "Cases whose model decision matches the benchmark expected decision "
            "divided by all benchmark cases; failed or missing inference counts "
            "as incorrect."
        ),
        "required_clarification_accuracy": (
            "Cases requiring clarification in which the model chose to clarify "
            "and produced at least one clarification question, divided by all "
            "cases requiring clarification."
        ),
        "tool_argument_schema_validity": (
            "Proposed calls whose arguments validate against their registered "
            "InsightOS JSON schema divided by all proposed calls."
        ),
        "structured_output_validity": (
            "Outputs satisfying the benchmark response schema divided by all "
            "benchmark cases; missing inference results count as invalid."
        ),
        "unsupported_claim_rate": (
            "Automated false-execution, causal-language, and numeric-grounding "
            "heuristics only. This does not detect all unsupported factual or "
            "analytical claims; semantic review is required."
        ),
        "numeric_grounding_heuristic_rate": (
            "Checks only whether numeric literals in the response occur in the "
            "case prompt or fixtures. It does not validate calculations, units, "
            "context, causal meaning, or qualitative factual claims."
        ),
        "hallucinated_reference_rate": (
            "Automated unknown tool-name and explicit out-of-scope dataset/file "
            "identifier checks only; semantic reference hallucination requires review."
        ),
    }
    return benchmark


def _create_notebook(
    bundle_sha256: str,
    notebook_path: Path = NOTEBOOK_PATH,
    *,
    corrected_runner: bool = False,
) -> None:
    runner_source = NOTEBOOK_RUNNER_PATH.read_text(encoding="utf-8")
    if runner_source.count("# %% [RUN_BASELINE]") != 1 or runner_source.count(
        "# %% [FINAL_ARCHIVE_VALIDATION]"
    ) != 1:
        raise ValueError("Runner must contain baseline and final validation markers")
    main_source, remaining_source = runner_source.split(
        "# %% [RUN_BASELINE]", maxsplit=1
    )
    run_source, validation_source = remaining_source.split(
        "# %% [FINAL_ARCHIVE_VALIDATION]", maxsplit=1
    )
    if _BUNDLE_HASH_PLACEHOLDER not in main_source:
        raise ValueError("Notebook runner bundle hash placeholder is missing")
    main_source = main_source.replace(_BUNDLE_HASH_PLACEHOLDER, bundle_sha256)
    setup_source = (
        "%pip install --quiet --upgrade "
        + " ".join(
            f'"{name}=={version}"'
            for name, version in KAGGLE_PACKAGE_PINS.items()
        )
        + "\n"
        "import sys\n"
        "if any(name == 'bitsandbytes' or name.startswith('bitsandbytes.') "
        "for name in sys.modules):\n"
        "    raise RuntimeError('bitsandbytes was already imported before installation. "
        "Use Kernel > Restart Session, then Run All from the first cell.')\n"
    )
    preflight_source = (
        "try:\n"
        "    PREFLIGHT_RESULT = _preflight_runtime()\n"
        "except RuntimePreflightError as error:\n"
        "    PREFLIGHT_RESULT = error\n"
        "    print(json.dumps(error.diagnostics, indent=2, sort_keys=True))\n"
        "else:\n"
        "    print(json.dumps(PREFLIGHT_RESULT, indent=2, sort_keys=True))"
    )
    final_source = validation_source.strip()
    notebook_title = (
        "# InsightOS Qwen3-8B Corrected Baseline Runner — v7\n\n"
        if corrected_runner
        else "# InsightOS Qwen3-8B Baseline — Milestone 5\n\n"
    )
    corrected_runner_note = (
        "This is a new evaluator/prompt revision using the unchanged v6 bundle. "
        "It gives the model a delegation DTO without runtime-owned identifiers or "
        "permissions and records the model-facing schema/evaluator versions. It does "
        "not replace or modify the original baseline run.\n\n"
        if corrected_runner
        else ""
    )
    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    notebook_title,
                    corrected_runner_note,
                    "Evaluation only. Uses the public base model and tokenizer pinned to "
                    "`Qwen/Qwen3-8B` revision "
                    "`b968826d9c46dd6066d109eabc6255188de91218`; no adapter, training, "
                    "InsightOS backend, database, or live tool execution is used.\n\n",
                    "This is the non-thinking baseline (`enable_thinking=False`), "
                    "not a complete assessment of Qwen3 reasoning-mode performance.\n\n",
                    "If Kaggle indicates that package installation needs a restart, "
                    "use **Kernel → Restart Session**, then **Run All** from the "
                    "first cell. Never continue in a session that imported an older "
                    "bitsandbytes module.\n\n",
                    "Attach the private Kaggle input dataset containing "
                    "`qwen3-8b-baseline-bundle-v6.json`, enable one GPU "
                    "(a T4 16 GB is the recommended starting point for the pinned "
                    "4-bit NF4 configuration), then run all cells. Results are "
                    "checkpointed per case and exported as a ZIP. The baseline "
                    "remains pending until the ZIP passes the local importer and "
                    "human review.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": setup_source.splitlines(keepends=True),
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "If Kaggle requests a kernel restart after installation, restart "
                    "and run all cells again. The runner reuses an incomplete "
                    "checkpoint only when its input bundle hash and benchmark "
                    "identity match; it never invents results for missing cases.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": main_source.strip().splitlines(keepends=True),
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": preflight_source.splitlines(keepends=True),
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": run_source.strip().splitlines(keepends=True),
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": final_source.splitlines(keepends=True),
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    notebook_path.parent.mkdir(parents=True, exist_ok=True)
    with notebook_path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(notebook, output, ensure_ascii=False, indent=1)
        output.write("\n")


def prepare() -> dict[str, Any]:
    if not INPUT_BUNDLE.is_file() or not SFT_RELEASE.is_file():
        raise FileNotFoundError("Milestone 4 baseline bundle or SFT release is missing")
    for path in (
        BUNDLE_PATH,
        PENDING_REPORT_PATH,
        READINESS_PATH,
        ENVIRONMENT_MANIFEST_PATH,
        NOTEBOOK_PATH,
    ):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite Milestone 5 artifact: {path}")
    if not NOTEBOOK_RUNNER_PATH.is_file():
        raise FileNotFoundError(f"Kaggle runner is missing: {NOTEBOOK_RUNNER_PATH}")
    sft_manifest = json.loads(SFT_MANIFEST.read_text(encoding="utf-8"))
    sft_bytes = SFT_RELEASE.read_bytes()
    sft_file_hash = _sha256(sft_bytes)
    if sft_manifest["dataset_file_sha256"] != sft_file_hash:
        raise ValueError("Qwen3-8B SFT v3 artifact hash does not match its manifest")
    canonical_sft_hash = _sha256(
        _canonical_json(
            {
                "dataset_version": sft_manifest["dataset_version"],
                "files": {
                    sft_manifest["dataset_file"]: sft_file_hash,
                },
            }
        )
    )
    if sft_manifest["dataset_sha256"] != canonical_sft_hash:
        raise ValueError("Qwen3-8B SFT v3 dataset fingerprint is not canonical")
    sft_rows = _read_jsonl(SFT_RELEASE)
    sft_hash = sft_manifest["dataset_sha256"]
    source_bundle_bytes = INPUT_BUNDLE.read_bytes()
    source_bundle = json.loads(source_bundle_bytes)
    actual_tools = sorted(
        [definition.provider_schema() for definition in TOOL_DEFINITIONS],
        key=lambda item: item["function"]["name"],
    )
    if _sha256(_canonical_json(actual_tools)) != source_bundle["tool_schemas_sha256"]:
        raise ValueError("Milestone 4 bundle tool schemas differ from InsightOS")
    if source_bundle["model"]["id"] != MODEL_ID:
        raise ValueError("Milestone 4 bundle has an unexpected base model")
    if source_bundle["model"]["revision"] != MODEL_REVISION:
        raise ValueError("Milestone 4 bundle has an unexpected model revision")
    if set(item["function"]["name"] for item in actual_tools) != set(
        item["function"]["name"] for item in source_bundle["tool_schemas"]
    ):
        raise ValueError("Milestone 4 bundle tool names differ from InsightOS")
    source_schema = source_bundle["structured_output_contracts"].get(
        "specialist_input"
    )
    actual_schema = SpecialistInputContract.model_json_schema()
    if source_schema != actual_schema:
        raise ValueError("Bundled specialist input contract differs from InsightOS")

    generated_benchmark = _build_benchmark(source_bundle, sft_rows, sft_hash)
    if not BENCHMARK_PATH.is_file():
        raise FileNotFoundError(
            f"Preserved benchmark v4 is missing: {BENCHMARK_PATH}"
        )
    benchmark_bytes = BENCHMARK_PATH.read_bytes()
    benchmark = json.loads(benchmark_bytes)
    if benchmark != generated_benchmark:
        raise ValueError(
            "Preserved benchmark v4 differs from its source cases or expected outcomes"
        )
    benchmark_sha256 = _sha256(benchmark_bytes)
    benchmark_object_sha256 = _sha256(_canonical_json(benchmark))
    contracts = dict(source_bundle["structured_output_contracts"])
    contracts["benchmark_response"] = benchmark["response_schema"]
    environment_manifest = {
        "environment_manifest_version": "insightos-qwen3-8b-kaggle-environment-v4",
        "purpose": "inference-only baseline evaluation",
        "python": {
            "runtime": "Kaggle-managed",
            "constraint": "record actual runtime; no backend Python constraint",
        },
        "pytorch": {
            "version_constraint": TORCH_VERSION_RANGE,
            "installation": "Kaggle-provided; do not replace or downgrade",
            "requires_cuda_build": True,
            "requires_visible_nvidia_gpu": True,
        },
        "packages": KAGGLE_PACKAGE_PINS,
        "cuda": {
            "runtime": "record torch.version.cuda; validate the matching bundled bnb binary",
            "bitsandbytes_cuda_13_support": "requires bitsandbytes>=0.48.0",
        },
        "revision_verification": {
            "metadata_source": (
                "HfApi.model_info.sha plus the commit directory returned by "
                "snapshot_download"
            ),
            "cache_resolution": (
                "Use the pinned full commit directly; do not resolve mutable refs. "
                "Verify both lexical and resolved snapshots paths."
            ),
            "model_and_tokenizer_loading": (
                "Load both from the same verified local snapshot with "
                "local_files_only=true."
            ),
            "incomplete_snapshot_checks": [
                "required model and tokenizer configuration",
                "tokenizer vocabulary files",
                "model weight file or index and all referenced shards",
                "all snapshot symlinks resolve to existing targets",
            ],
        },
        "quantization": {
            "load_in_4bit": True,
            "type": "NF4",
            "double_quantization": True,
            "compute_dtype": "float16",
            "cuda_probe_required_before_model_load": True,
        },
        "training_packages": {
            "peft": "not used or installed",
            "trl": "not used or installed",
        },
        "restart_policy": (
            "If packages change after bitsandbytes has been imported, use Kernel > "
            "Restart Session and then Run All from the first cell."
        ),
    }
    environment_manifest_bytes = (
        json.dumps(
            environment_manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    environment_manifest_sha256 = _sha256(environment_manifest_bytes)
    bundle = {
        **source_bundle,
        "bundle_version": "insightos-qwen3-8b-baseline-bundle-v6",
        "source_bundle_sha256": _sha256(source_bundle_bytes),
        "benchmark": benchmark,
        "benchmark_sha256": benchmark_object_sha256,
        "benchmark_file_sha256": benchmark_sha256,
        "benchmark_dataset_sha256": _sha256(
            _canonical_json(benchmark["cases"])
        ),
        "structured_output_contracts": contracts,
        "structured_output_contracts_sha256": _sha256(_canonical_json(contracts)),
        "kaggle_environment": environment_manifest,
        "kaggle_environment_sha256": environment_manifest_sha256,
        "tokenizer": {
            "id": TOKENIZER_ID,
            "revision": TOKENIZER_REVISION,
            "trust_remote_code": False,
            "chat_template": "Use the pinned tokenizer's embedded template",
            "thinking_mode": False,
        },
        "baseline_execution_status": "pending",
        "training_authorized": False,
    }
    bundle_bytes = (
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    bundle_sha256 = _sha256(bundle_bytes)
    pending_report = {
        "report_version": "insightos-qwen3-8b-baseline-report-v5",
        "status": "pending",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "tokenizer_id": TOKENIZER_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "bundle_sha256": bundle_sha256,
        "benchmark_version": benchmark["benchmark_version"],
        "benchmark_file_sha256": benchmark_sha256,
        "kaggle_environment_sha256": environment_manifest_sha256,
        "sft_dataset_version": sft_manifest["dataset_version"],
        "sft_dataset_sha256": sft_hash,
        "metrics": None,
        "execution_artifact": None,
        "reason": "No Kaggle execution artifact has been supplied or validated.",
        "training_authorized": False,
        "training_readiness": "NOT READY — BASELINE INCOMPLETE",
    }
    readiness_report = {
        "assessment_version": "insightos-qwen3-8b-training-readiness-v5",
        "recommendation": "NOT READY — BASELINE INCOMPLETE",
        "checks": {
            "baseline_execution_completed": False,
            "baseline_results_imported": False,
            "benchmark_and_bundle_hashes_pinned": True,
            "sft_v3_release_hash_verified": True,
            "training_benchmark_duplicate_audit_passed": True,
            "dataset_provenance_scope_documented": True,
            "training_authorization": False,
            "kaggle_qlora_compatibility_verified": False,
            "adapter_output_and_evaluation_plan_defined": True,
        },
        "reasons": [
            "The original base-model Kaggle inference has not been run or imported.",
            "Human semantic review of the baseline is pending.",
            "Kaggle QLoRA runtime compatibility has not been verified.",
            "No training authorization has been granted.",
        ],
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "benchmark_version": benchmark["benchmark_version"],
        "benchmark_sha256": benchmark_sha256,
        "sft_dataset_version": sft_manifest["dataset_version"],
        "sft_dataset_sha256": sft_hash,
        "training_authorized": False,
    }

    _write_json_exclusive(BUNDLE_PATH, bundle)
    _write_json_exclusive(PENDING_REPORT_PATH, pending_report)
    _write_json_exclusive(READINESS_PATH, readiness_report)
    with ENVIRONMENT_MANIFEST_PATH.open(
        "x", encoding="utf-8", newline="\n"
    ) as output:
        output.write(environment_manifest_bytes.decode("utf-8"))
    _create_notebook(bundle_sha256)
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "benchmark": str(BENCHMARK_PATH),
        "benchmark_version": benchmark["benchmark_version"],
        "benchmark_sha256": benchmark_sha256,
        "bundle": str(BUNDLE_PATH),
        "bundle_sha256": bundle_sha256,
        "environment_manifest": str(ENVIRONMENT_MANIFEST_PATH),
        "environment_manifest_sha256": environment_manifest_sha256,
        "readiness_report": str(READINESS_PATH),
        "notebook": str(NOTEBOOK_PATH),
        "incoming_directory": str(INCOMING_DIR),
        "case_count": len(benchmark["cases"]),
        "sft_hash_verified": True,
        "baseline_status": "pending",
    }


if __name__ == "__main__":
    print(json.dumps(prepare(), indent=2, sort_keys=True))
