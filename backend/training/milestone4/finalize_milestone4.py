from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.agent.api import ChatTurnResponse
from app.agent.inspection_handoff import InspectionHandoff
from app.agent.specialist_agents import (
    AnalyticsDecision,
    DataIntelligenceResult,
    PresentationResult,
    SpecialistHandoff,
    SpecialistInputContract,
)
from app.agent.tools import TOOL_DEFINITIONS, DatasetInspectionHandoff
from training.milestone4.approval import (
    canonical_dataset_sha256,
    sha256_files,
    validate_dataset_attestation,
)
from training.milestone4.prepare_milestone4 import (
    BENCHMARK_PATH,
    EDA_CORRECTED_RELEASE_DIR,
    MILESTONE_ROOT,
    ORCHESTRATION_RELEASE,
    _all_text,
    _duplicate_report,
)
from training.reference_eda.corpus import validate_sql_examples
from training.reference_eda.validate_dataset import (
    validate_dataset as validate_eda_dataset,
)
from training.validate_sft_dataset import validate_dataset as validate_sft_dataset


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
TRAINING_ROOT = BACKEND_ROOT / "training"
QWEN8B_ROOT = TRAINING_ROOT / "qwen3-8b"
QWEN8B_RELEASE_DIR = QWEN8B_ROOT / "releases" / "insightos-agent-sft-v3"
FINALIZATION_ATTESTATIONS = MILESTONE_ROOT / "dataset_validation_attestations_v1.json"
EDA_REPORT_PATH = MILESTONE_ROOT / "qwen3_4b_dataset_report_v3.json"
ORCHESTRATION_REPORT_PATH = MILESTONE_ROOT / "qwen3_8b_orchestration_quality_report_v3.json"
FINAL_BENCHMARK_PATH = MILESTONE_ROOT / "qwen3-8b-orchestration-benchmark-v2.json"
BASELINE_BUNDLE_PATH = MILESTONE_ROOT / "qwen3-8b-baseline-bundle-v1.json"
CONFIG_DIR = MILESTONE_ROOT / "kaggle_configs"
EDA_DATASET_FILES = (
    "train.jsonl",
    "validation.jsonl",
    "held_out.jsonl",
    "human_review_suite.json",
    "synthetic_fixtures.json",
)
SFT_RELEASE_FILENAME = "insightos-agent-sft-v3.jsonl"
SFT_RELEASE_MANIFEST = "release_manifest.json"
SFT_SCHEMA_VERSION = "insightos-agent-sft-v1"
ATTESTATION_RECORDED_ON = "2026-11-05"
MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
BENCHMARK_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "InsightOS orchestration benchmark response",
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
                    "name": {"type": "string"},
                    "arguments": {"type": "object"},
                },
            },
        },
        "specialist_handoff": {"type": ["object", "null"]},
        "response": {"type": "string"},
        "clarification_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "evidence_references": {"type": "array", "items": {"type": "object"}},
        "decision": {"enum": ["continue", "clarify", "abstain"]},
    },
}
SCORING_RULES = {
    "intent-dashboard-scope": ["clarification_required", "no_execution_claim"],
    "plan-profile-quality-analysis": [
        "tool_order",
        "profile_before_quality",
        "no_unsupported_claims",
    ],
    "tool-selection-project-list": ["exact_tool_name", "exact_arguments"],
    "tool-arguments-strict-identifiers": [
        "no_tool_call",
        "clarification_required",
        "no_string_to_integer_coercion",
    ],
    "structured-tool-call": [
        "exact_tool_name",
        "exact_arguments",
        "registered_tool_schema_valid",
    ],
    "specialist-data-delegation": [
        "specialist_contract_valid",
        "evidence_references_preserved",
        "no_unsupported_claims",
    ],
    "interpret-tool-result": [
        "returned_value_present",
        "no_additional_numeric_claims",
    ],
    "tool-error-bounded-retry": [
        "retry_count_at_most_one",
        "no_success_claim_after_failure",
    ],
    "clarify-ambiguous-analysis": [
        "clarification_required",
        "no_causal_claim",
    ],
    "grounded-profile-response": [
        "exact_evidence_values",
        "no_execution_claim",
    ],
    "resume-without-engine-replay": [
        "no_engine_replay_call",
        "no_false_execution_claim",
    ],
    "reject-unsupported-causal-action": [
        "no_causal_claim",
        "safe_abstention",
    ],
}
CAPABILITY_COVERAGE = {
    "intent_understanding": [
        "missing-dataset-prerequisite",
        "project-inventory-before-dataset-selection",
        "intent-dashboard-scope",
    ],
    "multi_step_workflow_planning": [
        "handoff-prerequisites",
        "engine-operation-not-replayed-after-resume",
        "plan-profile-quality-analysis",
    ],
    "tool_selection_and_arguments": [
        "routing-list-files",
        "invalid-tool-arguments",
        "explicit-quality-engine-request",
        "tool-selection-project-list",
        "structured-tool-call",
    ],
    "specialist_delegation": [
        "inspection-handoff-contract",
        "typed-data-intelligence-handoff",
        "specialist-data-delegation",
    ],
    "tool_result_interpretation": [
        "saved-eda-result-interpretation",
        "stored-profile-is-not-recomputed",
        "interpret-tool-result",
    ],
    "error_handling_and_bounded_retries": [
        "false-success-claim",
        "bounded-tool-retry",
        "bounded-retry-reports-persistent-tool-failure",
        "tool-error-bounded-retry",
    ],
    "clarification": [
        "missing-dataset-prerequisite",
        "clarify-forecast-prerequisites",
        "clarify-ambiguous-analysis",
    ],
    "evidence_grounding": [
        "observation-hypothesis-unknown",
        "saved-eda-result-interpretation",
        "stale-evidence-abstention",
        "grounded-profile-response",
    ],
    "interruption_and_recovery": [
        "engine-operation-not-replayed-after-resume",
        "resume-without-engine-replay",
    ],
    "unsupported_action_rejection": [
        "unsupported-new-analytics",
        "join-recommendation-is-not-approval",
        "reject-unsupported-causal-action",
    ],
}


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


def _file_hashes(directory: Path, filenames: tuple[str, ...]) -> dict[str, str]:
    return sha256_files(directory, filenames)


def _eda_report() -> tuple[dict[str, Any], dict[str, str], str]:
    validation = validate_eda_dataset(
        EDA_CORRECTED_RELEASE_DIR,
        require_approved=False,
    )
    file_hashes = _file_hashes(EDA_CORRECTED_RELEASE_DIR, EDA_DATASET_FILES)
    dataset_version = "insightos-reference-eda-v3"
    dataset_sha256 = canonical_dataset_sha256(dataset_version, file_hashes)
    rows = [
        row
        for name in ("train.jsonl", "validation.jsonl", "held_out.jsonl")
        for row in _read_jsonl(EDA_CORRECTED_RELEASE_DIR / name)
    ]
    duplicates = _duplicate_report(rows)
    if duplicates["exact_duplicate_pairs"]:
        raise ValueError("EDA release contains exact duplicate examples")
    if duplicates["cross_split_near_duplicate_pairs"]:
        raise ValueError("EDA release contains cross-split near duplicates")
    if validation["split_counts"] != {
        "test": 50,
        "train": 150,
        "validation": 50,
    }:
        raise ValueError("EDA release split counts differ from the intended 150/50/50")
    report = {
        "report_version": "insightos-qwen3-4b-final-dataset-report-v3",
        "dataset_version": dataset_version,
        "dataset_sha256": dataset_sha256,
        "dataset_sha256_method": (
            "SHA-256 of canonical JSON containing dataset_version and the "
            "lexically sorted SHA-256 values of the five listed dataset artifacts."
        ),
        "dataset_file_sha256": file_hashes,
        "release_manifest_sha256": _sha256(
            (EDA_CORRECTED_RELEASE_DIR / "release_manifest.json").read_bytes()
        ),
        "source_versions": {
            "dataset_v1": "insightos-reference-eda-v1",
            "dataset_v2": "insightos-reference-eda-v2",
        },
        "counts": {
            "examples": validation["examples"],
            "train": validation["split_counts"]["train"],
            "validation": validation["split_counts"]["validation"],
            "test": validation["split_counts"]["test"],
            "scenario_groups": validation["scenario_groups"],
            "approved_examples": validation["approved_examples"],
            "draft_examples": validation["draft_examples"],
        },
        "scenario_coverage_by_split": validation["scenario_coverage_by_split"],
        "automated_validation": {
            "release_hashes": "passed",
            "sft_schema_and_actual_tool_argument_validation": "passed",
            "tool_result_contract_validation": "passed",
            "sql_python_dax_examples": {
                "validated_count": validation["validated_sql_python_dax_examples"],
                "scope": (
                    "SQL SELECT examples execute against read-only SQLite fixtures; "
                    "Python examples compile; DAX examples are checked for required "
                    "table and field tokens, not executed."
                ),
            },
            "numerical_and_analytical_validation": {
                "state": "deterministic_fixture_tests_required_and_run_separately",
                "evidence": (
                    "Reference EDA tests assert fixture-derived expected summaries "
                    "and representative SQL query outputs."
                ),
            },
        },
        "leakage_and_duplicate_validation": {
            "scenario_group_leakage": validation["leaking_groups"],
            "exact_duplicate_pairs": len(duplicates["exact_duplicate_pairs"]),
            "near_duplicate_pairs_total": len(duplicates["near_duplicate_pairs"]),
            "cross_split_near_duplicate_pairs": len(
                duplicates["cross_split_near_duplicate_pairs"]
            ),
            "threshold": duplicates["threshold"],
            "method": duplicates["method"],
            "limitation": duplicates["limitation"],
        },
        "human_validation": {
            "state": "owner_reported_dataset_level",
            "attestation_manifest": FINALIZATION_ATTESTATIONS.name,
            "scope_limitation": (
                "Owner-reported human validation is provenance, not individual "
                "record approval or training authorization."
            ),
        },
        "training_authorization": {
            "authorized": False,
            "reason": (
                "Record-level approval and independent evaluation review remain "
                "separate gates."
            ),
        },
        "generalization_limitation": (
            "Training spans three domains; validation and test each contain one "
            "held-out domain. Report domain-specific results and do not claim broad "
            "cross-domain generalization."
        ),
        "quality_review_status": validation["evaluation_review_ready"],
    }
    return report, file_hashes, dataset_sha256


def _build_final_orchestration_release() -> tuple[
    dict[str, Any], dict[str, str], str, list[dict[str, Any]]
]:
    if QWEN8B_RELEASE_DIR.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing release: {QWEN8B_RELEASE_DIR}"
        )
    if ORCHESTRATION_RELEASE.name != "insightos-agent-sft-v2.jsonl":
        raise ValueError("Expected the audited orchestration v2 source")
    source_bytes = ORCHESTRATION_RELEASE.read_bytes()
    source_rows = _read_jsonl(ORCHESTRATION_RELEASE)
    source_validation = validate_sft_dataset(ORCHESTRATION_RELEASE)
    source_hash = _sha256(source_bytes)
    version = "insightos-qwen3-8b-orchestration-v3"
    duplicates = _duplicate_report(source_rows)
    if duplicates["exact_duplicate_pairs"]:
        raise ValueError("Orchestration corpus contains exact duplicate examples")
    if duplicates["cross_split_near_duplicate_pairs"]:
        raise ValueError("Orchestration corpus contains cross-split near duplicates")
    counts = Counter(row["split"] for row in source_rows)
    category_counts: dict[str, dict[str, int]] = defaultdict(dict)
    scenario_group_splits: dict[str, set[str]] = defaultdict(set)
    for row in source_rows:
        category_counts[row["split"]][row["category"]] = (
            category_counts[row["split"]].get(row["category"], 0) + 1
        )
        scenario_group_splits[row["scenario_group"]].add(row["split"])
    scenario_group_leakage = sorted(
        group for group, splits in scenario_group_splits.items() if len(splits) > 1
    )
    if scenario_group_leakage:
        raise ValueError("Orchestration scenario groups cross dataset splits")
    tool_call_counts = Counter(
        call["function"]["name"]
        for row in source_rows
        for message in row["messages"]
        for call in message.get("tool_calls", [])
    )
    statuses = Counter(row["validation_status"] for row in source_rows)
    original_rows = _read_jsonl(QWEN8B_ROOT / "insightos-agent-sft-v1.jsonl")
    source_by_id = {row["id"]: row for row in original_rows}
    final_ids = {row["id"] for row in source_rows}
    final_by_id = {row["id"]: row for row in source_rows}
    if len(final_ids) != len(source_rows) or not set(source_by_id).issubset(final_ids):
        raise ValueError("Final orchestration corpus lost or duplicated source records")
    if any(final_by_id[record_id] != source_row for record_id, source_row in source_by_id.items()):
        raise ValueError("An original orchestration example changed in the v2 release")
    if source_validation["split_counts"] != {
        "test": 6,
        "train": 13,
        "validation": 5,
    }:
        raise ValueError("Orchestration split counts differ from the audited v2 release")

    QWEN8B_RELEASE_DIR.mkdir(parents=True, exist_ok=False)
    corpus_path = QWEN8B_RELEASE_DIR / SFT_RELEASE_FILENAME
    with corpus_path.open("xb") as output:
        output.write(source_bytes)
    if corpus_path.read_bytes() != source_bytes:
        raise ValueError("Final orchestration corpus differs from the reviewed v2 bytes")
    validation = validate_sft_dataset(corpus_path)
    if validation != source_validation:
        raise ValueError("Versioned orchestration release validation differs from source")
    file_hashes = _file_hashes(QWEN8B_RELEASE_DIR, (SFT_RELEASE_FILENAME,))
    dataset_sha256 = canonical_dataset_sha256(version, file_hashes)

    release_manifest = {
        "manifest_version": "insightos-sft-release-v1",
        "dataset_version": version,
        "source_dataset_version": "insightos-qwen3-8b-orchestration-v2",
        "source_file": "backend/training/qwen3-8b/insightos-agent-sft-v2.jsonl",
        "source_dataset_sha256": source_hash,
        "dataset_file": SFT_RELEASE_FILENAME,
        "dataset_file_sha256": file_hashes[SFT_RELEASE_FILENAME],
        "dataset_sha256": dataset_sha256,
        "dataset_sha256_method": "Canonical version plus sorted artifact SHA-256 map.",
        "record_content_changed": False,
        "schema_contract_version": SFT_SCHEMA_VERSION,
        "counts": {
            "examples": validation["examples"],
            "split_counts": validation["split_counts"],
            "scenario_groups": validation["scenario_groups"],
            "human_approved_training_examples": validation[
                "human_approved_training_examples"
            ],
        },
        "near_duplicate_method": duplicates["method"],
        "near_duplicate_threshold": duplicates["threshold"],
        "training_authorized": False,
    }
    _write_json_exclusive(
        QWEN8B_RELEASE_DIR / SFT_RELEASE_MANIFEST,
        release_manifest,
    )
    report = {
        "report_version": "insightos-qwen3-8b-orchestration-quality-report-v3",
        "dataset_version": version,
        "dataset_sha256": dataset_sha256,
        "dataset_file_sha256": file_hashes,
        "release_manifest_sha256": _sha256(
            (QWEN8B_RELEASE_DIR / SFT_RELEASE_MANIFEST).read_bytes()
        ),
        "source_dataset_version": "insightos-qwen3-8b-orchestration-v2",
        "source_dataset_sha256": source_hash,
        "original_dataset_version": "insightos-agent-sft-v1",
        "original_dataset_sha256": _sha256(
            (QWEN8B_ROOT / "insightos-agent-sft-v1.jsonl").read_bytes()
        ),
        "record_content_changed_from_v2": False,
        "counts": {
            "examples": len(source_rows),
            "train": counts["train"],
            "validation": counts["validation"],
            "test": counts["test"],
            "scenario_groups": validation["scenario_groups"],
            "status_by_record": dict(sorted(statuses.items())),
            "approved_training_examples": validation[
                "human_approved_training_examples"
            ],
        },
        "category_counts_by_split": {
            split: dict(sorted(values.items()))
            for split, values in sorted(category_counts.items())
        },
        "tool_call_counts": dict(sorted(tool_call_counts.items())),
        "tool_schemas_validated_against": "app.agent.tools.TOOL_DEFINITIONS",
        "sft_schema_contract": "backend/training/qwen3-0.6b/insightos-sft.schema.json",
        "automated_schema_validation": "passed",
        "duplicates": {
            "exact_duplicate_pairs": len(duplicates["exact_duplicate_pairs"]),
            "near_duplicate_pairs": len(duplicates["near_duplicate_pairs"]),
            "cross_split_near_duplicate_pairs": len(
                duplicates["cross_split_near_duplicate_pairs"]
            ),
            "scenario_group_leakage": scenario_group_leakage,
            "threshold": duplicates["threshold"],
            "method": duplicates["method"],
            "limitation": duplicates["limitation"],
        },
        "capability_coverage": {
            key: {
                "status": "examples_present",
                "example_ids": ids,
            }
            for key, ids in CAPABILITY_COVERAGE.items()
        },
        "human_validation": {
            "state": "partial_owner_reported_dataset_level",
            "attestation_manifest": FINALIZATION_ATTESTATIONS.name,
            "scope": (
                "The owner-reported validation pertains to the source corpus; "
                "the 12 additive synthetic examples in v2/v3 have no supplied "
                "record-level human review evidence."
            ),
        },
        "training_authorization": {
            "authorized": False,
            "reason": (
                "All 24 examples remain draft; dataset-level provenance is not "
                "a substitute for the existing per-example training gate."
            ),
        },
        "baseline_status": "pending_kaggle_execution",
    }
    return report, file_hashes, dataset_sha256, source_rows


def _attestation_record(
    *,
    dataset_id: str,
    dataset_version: str,
    file_hashes: dict[str, str],
    dataset_sha256: str,
    scope: str,
    human_state: str,
    machine_validation: dict[str, Any],
) -> dict[str, Any]:
    source = (
        "Project-owner clarification in the Milestone 4 finalization request, "
        "recorded 2026-11-05. This is owner-reported provenance, not a direct "
        "statement from an identified senior Business Analyst."
    )
    roles = []
    for reviewer_role, approval_state in (
        ("project_owner", "owner_reported_human_validated"),
        ("senior_business_analyst", "owner_reported_for_source_scope_only"),
    ):
        roles.append(
            {
                "dataset_version": dataset_version,
                "dataset_sha256": dataset_sha256,
                "reviewer_role": reviewer_role,
                "reviewer_identity": None,
                "attestation_source": source,
                "attestation_recorded_on": ATTESTATION_RECORDED_ON,
                "historical_review_date": None,
                "review_scope": scope,
                "approval_state": approval_state,
                "per_example_approvals_created": False,
            }
        )
    return {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "dataset_sha256": dataset_sha256,
        "dataset_sha256_method": (
            "SHA-256 of canonical JSON containing dataset_version and the "
            "lexically sorted SHA-256 values of the listed dataset artifacts."
        ),
        "file_sha256": file_hashes,
        "human_validation": {
            "status": human_state,
            "attestations": roles,
        },
        "machine_validation": machine_validation,
        "training_authorization": {
            "authorized": False,
            "reason": (
                "Dataset-level human-validation provenance does not create "
                "per-example approvals or authorize training."
            ),
        },
    }


def _write_attestations(
    eda_report: dict[str, Any],
    eda_hashes: dict[str, str],
    eda_sha: str,
    orchestration_report: dict[str, Any],
    orchestration_hashes: dict[str, str],
    orchestration_sha: str,
) -> dict[str, Any]:
    datasets = [
        _attestation_record(
            dataset_id="qwen3-4b-reference-eda",
            dataset_version=eda_report["dataset_version"],
            file_hashes=eda_hashes,
            dataset_sha256=eda_sha,
            scope=(
                "Dataset-level project-work validation as reported by the owner. "
                "The v3 release changes split assignments only; model-generated "
                "human-review suite decisions remain pending."
            ),
            human_state="owner_reported_dataset_level",
            machine_validation={
                "schema_and_tool_contracts": "passed",
                "numerical_and_analytical": "fixture tests and read-only SQL verified; DAX is static-checked",
                "leakage_and_duplicates": "passed",
            },
        ),
        _attestation_record(
            dataset_id="qwen3-8b-orchestration",
            dataset_version=orchestration_report["dataset_version"],
            file_hashes=orchestration_hashes,
            dataset_sha256=orchestration_sha,
            scope=(
                "Owner-reported source-dataset validation applies to the original "
                "12-example v1 subset. The 12 synthetic additions introduced in "
                "v2/v3 are explicitly outside that reported human-review scope."
            ),
            human_state="partial_owner_reported_dataset_level",
            machine_validation={
                "schema_and_tool_contracts": "passed",
                "numerical_and_analytical": "not_applicable_to_orchestration_corpus",
                "leakage_and_duplicates": "passed",
            },
        ),
    ]
    manifest = {
        "manifest_version": "insightos-dataset-validation-v1",
        "attestation_source": (
            "Project-owner clarification in the Milestone 4 finalization request."
        ),
        "attestation_recorded_on": ATTESTATION_RECORDED_ON,
        "historical_review_dates": None,
        "reviewer_identities": None,
        "training_authorization_default": False,
        "datasets": datasets,
    }
    _write_json_exclusive(FINALIZATION_ATTESTATIONS, manifest)
    for record in datasets:
        expected_dir = (
            EDA_CORRECTED_RELEASE_DIR
            if record["dataset_id"] == "qwen3-4b-reference-eda"
            else QWEN8B_RELEASE_DIR
        )
        verified = validate_dataset_attestation(
            FINALIZATION_ATTESTATIONS,
            dataset_id=record["dataset_id"],
            dataset_version=record["dataset_version"],
            expected_dataset_sha256=record["dataset_sha256"],
            expected_files=record["file_sha256"],
        )
        actual_files = _file_hashes(expected_dir, tuple(record["file_sha256"]))
        if actual_files != record["file_sha256"]:
            raise ValueError(f"{record['dataset_id']} files changed after attestation")
        if verified["training_authorized"]:
            raise ValueError("Dataset attestation unexpectedly authorizes training")
    return manifest


def _benchmark_v2(bundle_tools: list[dict[str, Any]]) -> dict[str, Any]:
    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    benchmark["source_benchmark_version"] = benchmark["benchmark_version"]
    benchmark["benchmark_version"] = "insightos-qwen3-8b-orchestration-benchmark-v2"
    benchmark["response_schema"] = BENCHMARK_OUTPUT_SCHEMA
    sorted_tools = sorted(
        bundle_tools,
        key=lambda tool: tool["function"]["name"],
    )
    benchmark["tool_schemas_sha256"] = _sha256(_canonical_json(sorted_tools))
    benchmark["model"] = {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "adapter": None,
        "inference_quantization": {
            "type": "4-bit NF4",
            "double_quantization": True,
            "compute_dtype": "float16",
            "training": False,
        },
    }
    for case in benchmark["cases"]:
        case["scoring_rules"] = SCORING_RULES[case["id"]]
        case["human_semantic_review_required"] = True
        case["tool_execution"] = "never"
    benchmark["evaluation_policy"] = {
        "proposed_tool_calls_only": True,
        "mock_tool_results_only": True,
        "no_application_side_effects": True,
        "human_review_required_for_semantic_claims": True,
    }
    return benchmark


def _bundle(
    benchmark: dict[str, Any],
    benchmark_bytes: bytes,
) -> dict[str, Any]:
    tool_schemas = [definition.provider_schema() for definition in TOOL_DEFINITIONS]
    tool_schemas.sort(key=lambda tool: tool["function"]["name"])
    contracts = {
        "specialist_input": SpecialistInputContract.model_json_schema(),
        "data_intelligence_result": DataIntelligenceResult.model_json_schema(),
        "analytics_decision": AnalyticsDecision.model_json_schema(),
        "presentation_result": PresentationResult.model_json_schema(),
        "specialist_handoff": SpecialistHandoff.model_json_schema(),
        "inspection_handoff": InspectionHandoff.model_json_schema(),
        "dataset_inspection_handoff": DatasetInspectionHandoff.model_json_schema(),
        "chat_turn_response": ChatTurnResponse.model_json_schema(),
        "benchmark_response": BENCHMARK_OUTPUT_SCHEMA,
    }
    return {
        "bundle_version": "insightos-qwen3-8b-baseline-bundle-v1",
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "adapter": None,
            "inference_quantization": {
                "load_in_4bit": True,
                "quant_type": "nf4",
                "double_quant": True,
                "compute_dtype": "float16",
                "training": False,
            },
        },
        "benchmark": benchmark,
        "benchmark_sha256": _sha256(_canonical_json(benchmark)),
        "benchmark_file_sha256": _sha256(benchmark_bytes),
        "benchmark_dataset_sha256": _sha256(_canonical_json(benchmark["cases"])),
        "tool_schemas": tool_schemas,
        "tool_schemas_sha256": _sha256(_canonical_json(tool_schemas)),
        "structured_output_contracts": contracts,
        "structured_output_contracts_sha256": _sha256(_canonical_json(contracts)),
        "schema_sources": {
            "tools": "app.agent.tools.TOOL_DEFINITIONS.provider_schema()",
            "specialist_contracts": "app.agent.specialist_agents",
            "inspection_handoff": "app.agent.inspection_handoff.InspectionHandoff",
            "dataset_inspection_handoff": "app.agent.tools.DatasetInspectionHandoff",
            "chat_turn_response": "app.agent.api.ChatTurnResponse",
        },
        "execution_policy": (
            "All tool calls are proposals validated against schemas. The notebook "
            "must never invoke the application or dispatch a tool."
        ),
    }


def _write_kaggle_configs(
    eda_report: dict[str, Any],
    orchestration_report: dict[str, Any],
    benchmark_sha: str,
) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(CONFIG_DIR.glob("qwen3-*-kaggle-qlora-v1.json"))
    if existing:
        raise FileExistsError(f"Refusing to overwrite Kaggle configs: {existing}")
    candidate_stack = {
        "notebook_runtime": "Kaggle-provided Linux/CUDA; exact versions recorded at run",
        "reference_notebook_method": "Unsloth FastLanguageModel QLoRA",
        "package_versions_observed": None,
        "compatibility_status": "unverified_without Kaggle execution evidence",
        "do_not_infer_from_backend_or_reference_eda_environment": True,
    }
    common = {
        "config_version": "insightos-kaggle-qlora-preparation-v1",
        "status": "preparation_only",
        "training_started": False,
        "base_method": "QLoRA",
        "load_in_4bit": True,
        "quantization": {
            "type": "NF4",
            "double_quantization": True,
            "compute_dtype": "float16",
        },
        "lora": {
            "rank": 8,
            "alpha": 16,
            "dropout": 0.05,
            "target_modules": [
                "q_proj",
                "k_proj",
                "v_proj",
                "o_proj",
                "gate_proj",
                "up_proj",
                "down_proj",
            ],
        },
        "kaggle_candidate_environment": candidate_stack,
        "runtime_capture_required": [
            "python",
            "torch",
            "transformers",
            "accelerate",
            "bitsandbytes",
            "peft",
            "trl",
            "unsloth",
            "cuda",
            "gpu_name_and_vram",
        ],
        "approval_gates": {
            "dataset_level_human_validation_is_provenance_only": True,
            "require_record_level_approval_before_training": True,
            "require_independent_baseline_before_training": True,
            "training_authorized": False,
        },
    }
    configs = {
        "qwen3-4b-kaggle-qlora-v1.json": {
            **common,
            "model_id": "Qwen/Qwen3-4B",
            "model_revision": "1cfa9a7208912126459214e8b04321603b3df60c",
            "dataset_version": eda_report["dataset_version"],
            "dataset_sha256": eda_report["dataset_sha256"],
            "dataset_path": (
                "backend/training/reference_eda/dataset_v3/"
            ),
            "sequence_length": 2048,
            "training_parameters": {
                "per_device_batch_size": 1,
                "gradient_accumulation_steps": 8,
                "num_train_epochs": 1,
                "gradient_checkpointing": True,
            },
            "blockers": [
                "Kaggle QLoRA compatibility has not been executed or verified.",
                "Dataset-level provenance does not approve examples individually.",
                "The held-out domain split is narrow and requires human evaluation.",
            ],
        },
        "qwen3-8b-kaggle-qlora-v1.json": {
            **common,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "dataset_version": orchestration_report["dataset_version"],
            "dataset_sha256": orchestration_report["dataset_sha256"],
            "dataset_path": (
                "backend/training/qwen3-8b/releases/insightos-agent-sft-v3/"
                + SFT_RELEASE_FILENAME
            ),
            "benchmark_version": "insightos-qwen3-8b-orchestration-benchmark-v2",
            "benchmark_sha256": benchmark_sha,
            "sequence_length": 1024,
            "training_parameters": {
                "per_device_batch_size": 1,
                "gradient_accumulation_steps": 8,
                "num_train_epochs": 1,
                "gradient_checkpointing": True,
            },
            "blockers": [
                "Kaggle QLoRA compatibility has not been executed or verified.",
                "12 generated supplement examples are outside the owner-reported review scope.",
                "The independent base-model benchmark remains pending Kaggle execution.",
                "No QLoRA run is authorized.",
            ],
        },
    }
    for filename, value in configs.items():
        _write_json_exclusive(CONFIG_DIR / filename, value)


def finalize() -> dict[str, Any]:
    for path in (
        FINALIZATION_ATTESTATIONS,
        EDA_REPORT_PATH,
        ORCHESTRATION_REPORT_PATH,
        FINAL_BENCHMARK_PATH,
        BASELINE_BUNDLE_PATH,
        QWEN8B_RELEASE_DIR,
        CONFIG_DIR / "qwen3-4b-kaggle-qlora-v1.json",
        CONFIG_DIR / "qwen3-8b-kaggle-qlora-v1.json",
    ):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing artifact: {path}")
    if not (QWEN8B_ROOT / "insightos-agent-sft-v1.jsonl").is_file():
        raise FileNotFoundError("The original Qwen3-8B SFT v1 dataset is unavailable")

    eda_report, eda_hashes, eda_sha = _eda_report()
    orchestration_report, orchestration_hashes, orchestration_sha, _ = (
        _build_final_orchestration_release()
    )
    attestations = _write_attestations(
        eda_report,
        eda_hashes,
        eda_sha,
        orchestration_report,
        orchestration_hashes,
        orchestration_sha,
    )
    attestation_sha = _sha256(FINALIZATION_ATTESTATIONS.read_bytes())
    eda_report["attestation_manifest_sha256"] = attestation_sha
    orchestration_report["attestation_manifest_sha256"] = attestation_sha
    benchmark = _benchmark_v2(
        [definition.provider_schema() for definition in TOOL_DEFINITIONS]
    )
    benchmark_bytes = (
        json.dumps(benchmark, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_json_exclusive(FINAL_BENCHMARK_PATH, benchmark)
    bundle = _bundle(benchmark, benchmark_bytes)
    _write_json_exclusive(BASELINE_BUNDLE_PATH, bundle)
    benchmark_sha = _sha256(FINAL_BENCHMARK_PATH.read_bytes())
    eda_report["attestation_manifest"] = FINALIZATION_ATTESTATIONS.name
    orchestration_report["attestation_manifest"] = FINALIZATION_ATTESTATIONS.name
    orchestration_report["benchmark_version"] = benchmark["benchmark_version"]
    orchestration_report["benchmark_sha256"] = benchmark_sha
    orchestration_report["baseline_bundle_sha256"] = _sha256(
        BASELINE_BUNDLE_PATH.read_bytes()
    )
    orchestration_report["capability_counts"] = {
        "independent_benchmark_cases": len(benchmark["cases"]),
        "capability_categories": len(
            {case["category"] for case in benchmark["cases"]}
        ),
        "human_review_required_cases": sum(
            case["human_semantic_review_required"] for case in benchmark["cases"]
        ),
    }
    for record in attestations["datasets"]:
        if record["dataset_id"] == "qwen3-4b-reference-eda":
            eda_report["human_validation"] = record["human_validation"]
        else:
            orchestration_report["human_validation"] = record["human_validation"]
    _write_json_exclusive(EDA_REPORT_PATH, eda_report)
    _write_json_exclusive(ORCHESTRATION_REPORT_PATH, orchestration_report)
    _write_kaggle_configs(eda_report, orchestration_report, benchmark_sha)

    return {
        "eda_report": str(EDA_REPORT_PATH),
        "eda_dataset_sha256": eda_sha,
        "orchestration_release": str(QWEN8B_RELEASE_DIR),
        "orchestration_dataset_sha256": orchestration_sha,
        "benchmark": str(FINAL_BENCHMARK_PATH),
        "baseline_bundle": str(BASELINE_BUNDLE_PATH),
        "baseline_status": "pending_kaggle_execution",
        "attestation_manifest": str(FINALIZATION_ATTESTATIONS),
        "training_authorized": False,
    }


if __name__ == "__main__":
    print(json.dumps(finalize(), indent=2, sort_keys=True))
