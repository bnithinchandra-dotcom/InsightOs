from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.specialist_agents import SpecialistInputContract
from app.agent.tools import TOOL_DEFINITIONS
from training.reference_eda.build_dataset import _evaluation_suite, SPLIT_FILENAMES
from training.reference_eda.corpus import corpus_counts, validate_sql_examples
from training.reference_eda.validate_dataset import validate_dataset as validate_eda
from training.validate_sft_dataset import validate_dataset as validate_sft


BACKEND_ROOT = Path(__file__).resolve().parents[2]
TRAINING_ROOT = BACKEND_ROOT / "training"
REFERENCE_EDA_ROOT = TRAINING_ROOT / "reference_eda"
EDA_SOURCE_DIR = REFERENCE_EDA_ROOT / "dataset_v1"
EDA_RELEASE_DIR = REFERENCE_EDA_ROOT / "dataset_v2"
EDA_CORRECTED_RELEASE_DIR = REFERENCE_EDA_ROOT / "dataset_v3"
ORCHESTRATION_SOURCE = TRAINING_ROOT / "qwen3-8b" / "insightos-agent-sft-v1.jsonl"
ORCHESTRATION_RELEASE = TRAINING_ROOT / "qwen3-8b" / "insightos-agent-sft-v2.jsonl"
MILESTONE_ROOT = TRAINING_ROOT / "milestone4"
BENCHMARK_PATH = MILESTONE_ROOT / "qwen3-8b-orchestration-benchmark-v1.json"
SFT_SCHEMA_VERSION = "insightos-agent-sft-v1"
DATASET_VERSION = "insightos-qwen3-8b-orchestration-v2"
RELEASE_VERSION = "insightos-reference-eda-v2"
CORRECTED_RELEASE_VERSION = "insightos-reference-eda-v3"
NEAR_DUPLICATE_THRESHOLD = 0.88

_TOOLS = {definition.name: definition for definition in TOOL_DEFINITIONS}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            )


def _tool_result(
    *,
    summary: str,
    data: dict[str, Any] | list[Any] | None = None,
    status: str = "succeeded",
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "warnings": [],
        "references": [],
        "limitations": [],
        "error_code": error_code,
    }


def _example(
    *,
    example_id: str,
    split: str,
    category: str,
    prompt: str,
    answer: str,
    scenario_group: str | None = None,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Use only registered InsightOS tools and supplied evidence. "
                "Do not claim execution without a returned tool result. "
                "Ask for clarification when prerequisites are missing."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    tools = []
    if tool_name is not None:
        if arguments is None or result is None:
            raise ValueError(f"{example_id}: tool calls require arguments and a result")
        tool = _TOOLS[tool_name]
        call_id = f"call-{example_id}"
        tools = [tool.provider_schema()]
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": arguments,
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        result,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ]
        )
    messages.append({"role": "assistant", "content": answer})
    return {
        "id": example_id,
        "scenario_group": scenario_group or example_id,
        "category": category,
        "split": split,
        "messages": messages,
        "tools": tools,
        "source": {
            "kind": "synthetic",
            "reference": (
                "Original synthetic scenario grounded in the InsightOS runtime "
                "and registered agent contracts; contains no user data."
            ),
        },
        "license": "Original synthetic content",
        "validation_status": "draft",
        "reviewer_notes": (
            "Milestone 4 draft addition. Requires record-level human review; "
            "owner-reported dataset-level validation is not an individual approval."
        ),
    }


def _supplemental_examples() -> list[dict[str, Any]]:
    examples = [
        _example(
            example_id="project-inventory-before-dataset-selection",
            split="train",
            category="tool_selection",
            prompt="Show the projects available in this InsightOS workspace.",
            tool_name="list_projects",
            arguments={},
            result=_tool_result(
                summary="Two synthetic projects are available.",
                data=[
                    {"id": 41, "name": "Northwind Demo"},
                    {"id": 42, "name": "Retail Demo"},
                ],
            ),
            answer=(
                "The workspace lists Northwind Demo (project 41) and Retail Demo "
                "(project 42). These are the returned project records."
            ),
        ),
        _example(
            example_id="dataset-file-discovery",
            split="train",
            category="tool_selection",
            prompt="List the files in selected dataset 42.",
            tool_name="list_files",
            arguments={"dataset_id": 42},
            result=_tool_result(
                summary="One ready file is listed.",
                data=[
                    {
                        "id": 7,
                        "dataset_id": 42,
                        "filename": "sales.csv",
                        "status": "Ready",
                    }
                ],
            ),
            answer=(
                "The returned listing contains sales.csv (file 7), marked Ready. "
                "No file contents were inspected by this listing."
            ),
        ),
        _example(
            example_id="stored-profile-is-not-recomputed",
            split="train",
            category="schema_interpretation",
            prompt="What does the stored profile show for file 7 in dataset 42?",
            tool_name="get_profile",
            arguments={"dataset_id": 42, "file_id": 7},
            result=_tool_result(
                summary="Current stored profile retrieved.",
                data={
                    "validation_state": "current",
                    "tables": [
                        {
                            "table_index": 0,
                            "table_name": "sales",
                            "row_count": 18,
                            "column_count": 3,
                        }
                    ],
                },
            ),
            answer=(
                "The stored current profile reports 18 rows and 3 columns in "
                "table 0, sales. Retrieving the saved profile did not recompute it."
            ),
        ),
        _example(
            example_id="typed-data-intelligence-handoff",
            split="train",
            category="structured_output",
            prompt=(
                "Prepare a Data Intelligence task from the current profile for "
                "dataset 42, file 7. Use workflow 00000000000000000000000000000001, "
                "task 00000000000000000000000000000002, and source fingerprint "
                "fixture-sha256-42-7."
            ),
            answer=json.dumps(
                {
                    "contract_version": 1,
                    "workflow_id": "00000000000000000000000000000001",
                    "task_id": "00000000000000000000000000000002",
                    "conversation_id": None,
                    "agent": "data_intelligence",
                    "objective": "Interpret the verified profile and identify relevant fields.",
                    "dataset_id": 42,
                    "file_id": 7,
                    "source_fingerprint": "fixture-sha256-42-7",
                    "evidence_references": [
                        {"type": "profile_fixture", "id": "profile:42:7"}
                    ],
                    "permitted_tools": ["get_profile", "get_quality_report"],
                    "context": {
                        "profile_status": "current",
                        "instruction": "Do not infer fields absent from the profile.",
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ),
        _example(
            example_id="explicit-quality-engine-request",
            split="train",
            category="tool_selection",
            prompt="Run the deterministic quality analysis for normalized file 7 in dataset 42.",
            tool_name="run_quality_analysis",
            arguments={"dataset_id": 42, "file_id": 7},
            result=_tool_result(
                summary="Synthetic quality analysis completed.",
                data={
                    "dataset_id": 42,
                    "file_id": 7,
                    "engine": "quality_analysis",
                    "issue_count": 1,
                    "findings": ["column customer_id has 2 missing values"],
                },
            ),
            answer=(
                "The returned synthetic quality result reports one finding: "
                "customer_id has 2 missing values. This result is limited to "
                "the supplied fixture."
            ),
        ),
        _example(
            example_id="saved-eda-result-interpretation",
            split="validation",
            category="business_reasoning",
            prompt="Summarize saved EDA analysis 101 for file 7 in dataset 42.",
            tool_name="get_eda_result",
            arguments={"dataset_id": 42, "file_id": 7, "analysis_id": 101},
            result=_tool_result(
                summary="Saved EDA result retrieved.",
                data={
                    "analysis_id": 101,
                    "status": "completed",
                    "summary": "Synthetic EDA reported a positive association.",
                    "limitations": ["The result does not establish causation."],
                },
            ),
            answer=(
                "The saved result reports a positive association, but explicitly "
                "does not establish causation. This tool retrieved a saved result; "
                "it did not rerun EDA."
            ),
        ),
        _example(
            example_id="clarify-forecast-prerequisites",
            split="validation",
            category="grounding_safety",
            prompt="Forecast sales for the next six months.",
            answer=(
                "Which dataset and file should I use, and which date and numeric "
                "sales columns should define the monthly series? I have not run a forecast."
            ),
        ),
        _example(
            example_id="reject-string-identifiers",
            split="validation",
            category="grounding_safety",
            prompt='Get the profile for dataset "42" and file 7.',
            answer=(
                "The tool contract requires a positive integer dataset_id, but "
                "the request supplies text. I have not called a tool. Please select "
                "the dataset or provide its numeric identifier."
            ),
        ),
        _example(
            example_id="stale-schema-evidence-abstention",
            split="test",
            category="grounding_safety",
            prompt="Use schema analysis 9 to confirm the current join between datasets 42 and 43.",
            tool_name="get_schema_analysis",
            arguments={"dataset_id": 42, "analysis_id": 9},
            result=_tool_result(
                summary="Saved schema analysis retrieved.",
                data={
                    "analysis_id": 9,
                    "validation_state": "stale",
                    "relationships": [
                        {"left_key": "customer_id", "right_key": "customer_id"}
                    ],
                },
            ),
            answer=(
                "The saved schema analysis is marked stale, so it cannot confirm "
                "a current relationship. Refresh the analysis before relying on "
                "that candidate join."
            ),
        ),
        _example(
            example_id="engine-operation-not-replayed-after-resume",
            split="test",
            category="grounding_safety",
            prompt=(
                "A checkpoint resumes after run_eda_analysis returned successfully "
                "for dataset 42, file 7, analysis 101. Continue without repeating "
                "completed work."
            ),
            answer=(
                "The checkpoint already contains the successful result for "
                "analysis 101. I will use that persisted result and will not invoke "
                "run_eda_analysis again."
            ),
        ),
        _example(
            example_id="bounded-retry-reports-persistent-tool-failure",
            split="test",
            category="tool_selection",
            prompt="List files in dataset 42. A single retry is permitted for a transient read failure.",
            tool_name="list_files",
            arguments={"dataset_id": 42},
            result=_tool_result(
                status="failed",
                summary="Temporary read failure persisted after the one permitted retry.",
                error_code="transient_tool_error",
            ),
            answer=(
                "The file listing failed after the permitted retry. I cannot report "
                "file contents or claim that inspection succeeded."
            ),
        ),
        _example(
            example_id="join-recommendation-is-not-approval",
            split="test",
            category="business_reasoning",
            prompt=(
                "Compare possible join keys for dataset 42 file 7 and dataset 43 "
                "file 8. Do not execute a join."
            ),
            tool_name="recommend_multi_dataset_joins",
            arguments={
                "sources": [
                    {
                        "alias": "orders",
                        "dataset_id": 42,
                        "file_id": 7,
                        "table_index": 0,
                    },
                    {
                        "alias": "customers",
                        "dataset_id": 43,
                        "file_id": 8,
                        "table_index": 0,
                    },
                ]
            },
            result=_tool_result(
                summary="A synthetic candidate key was returned.",
                data={
                    "join_recommendations": [
                        {
                            "left_key": {"alias": "orders", "column": "customer_id"},
                            "right_key": {"alias": "customers", "column": "customer_id"},
                            "status": "candidate",
                        }
                    ]
                },
            ),
            answer=(
                "The tool returned customer_id as a candidate relationship. A "
                "recommendation is not an approved join; confirm the exact keys "
                "before executing any analysis."
            ),
        ),
    ]
    handoff = json.loads(examples[3]["messages"][-1]["content"])
    SpecialistInputContract.model_validate(handoff)
    return examples


def _all_text(record: dict[str, Any]) -> str:
    return " ".join(
        message["content"].casefold()
        for message in record["messages"]
        if message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
        and message["content"]
    )


def _duplicate_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    exact: list[dict[str, str]] = []
    near: list[dict[str, Any]] = []
    normalized = {record["id"]: re.sub(r"\s+", " ", _all_text(record)).strip() for record in records}
    shingles = {
        record_id: {
            text[index : index + 3]
            for index in range(max(1, len(text) - 2))
        }
        for record_id, text in normalized.items()
    }
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            left_text = normalized[left["id"]]
            right_text = normalized[right["id"]]
            if left_text == right_text:
                exact.append({"left_id": left["id"], "right_id": right["id"]})
                continue
            left_shingles = shingles[left["id"]]
            right_shingles = shingles[right["id"]]
            union = left_shingles | right_shingles
            similarity = (
                len(left_shingles & right_shingles) / len(union)
                if union
                else 1.0
            )
            if similarity >= NEAR_DUPLICATE_THRESHOLD:
                near.append(
                    {
                        "left_id": left["id"],
                        "right_id": right["id"],
                        "similarity": round(similarity, 4),
                        "same_split": left["split"] == right["split"],
                    }
                )
    return {
        "method": "Jaccard similarity over character trigrams from normalized user and assistant messages",
        "threshold": NEAR_DUPLICATE_THRESHOLD,
        "exact_duplicate_pairs": exact,
        "near_duplicate_pairs": near,
        "cross_split_near_duplicate_pairs": [
            pair for pair in near if not pair["same_split"]
        ],
        "limitation": "This lexical heuristic is not semantic proof of independence.",
    }


def _duplicate_summary(report: dict[str, Any]) -> dict[str, Any]:
        near = report["near_duplicate_pairs"]
        cross_split = report["cross_split_near_duplicate_pairs"]
        return {
            "method": report["method"],
            "threshold": report["threshold"],
            "exact_duplicate_pair_count": len(report["exact_duplicate_pairs"]),
            "near_duplicate_pair_count": len(near),
            "cross_split_near_duplicate_pair_count": len(cross_split),
            "cross_split_pair_sample": cross_split[:20],
            "limitation": report["limitation"],
        }


def _generate_eda_v3() -> dict[str, Any]:
        if EDA_CORRECTED_RELEASE_DIR.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing release: {EDA_CORRECTED_RELEASE_DIR}"
            )
        quality_path = MILESTONE_ROOT / "qwen3_4b_quality_report_v3.json"
        if quality_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing report: {quality_path}")
        source_files = ("train.jsonl", "validation.jsonl", "held_out.jsonl")
        records = [
            json.loads(line)
            for filename in source_files
            for line in (EDA_SOURCE_DIR / filename).read_text(encoding="utf-8").splitlines()
        ]
        scenario_split = {
            "marketing": "train",
            "maintenance": "train",
            "ecommerce": "train",
            "fraud": "validation",
            "healthcare": "test",
        }
        for record in records:
            scenario = record["id"].split("-")[1]
            record["split"] = scenario_split[scenario]

        release_dir = EDA_CORRECTED_RELEASE_DIR
        release_dir.mkdir()
        for split, filename in SPLIT_FILENAMES.items():
            selected = [record for record in records if record["split"] == split]
            path = release_dir / filename
            with path.open("x", encoding="utf-8", newline="\n") as output:
                for record in selected:
                    output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

        shutil.copyfile(EDA_SOURCE_DIR / "synthetic_fixtures.json", release_dir / "synthetic_fixtures.json")
        _write_json(release_dir / "human_review_suite.json", _evaluation_suite(records))
        data_file_names = (
            *SPLIT_FILENAMES.values(),
            "human_review_suite.json",
            "synthetic_fixtures.json",
        )
        source_manifest_hash = _file_sha256(REFERENCE_EDA_ROOT / "review_manifest.json")
        review_manifest = {
            "manifest_version": "insightos-reference-eda-review-v2",
            "dataset_version": CORRECTED_RELEASE_VERSION,
            "source_dataset_version": "insightos-reference-eda-v2",
            "source_review_manifest_sha256": source_manifest_hash,
            "split_policy": (
                "Keep each synthetic scenario domain wholly within one split because "
                "cross-domain records share prompt and target templates."
            ),
            "split_scenario_assignment": {
                "train": ["marketing", "maintenance", "ecommerce"],
                "validation": ["fraud"],
                "test": ["healthcare"],
            },
            "owner_reported_validation": {
                "source": "Milestone 4 project-owner instruction",
                "record_level_mapping": False,
                "reviewer_ids": None,
                "review_dates": None,
            },
            "human_record_approval_count": 0,
            "training_ready": False,
            "fine_tuning_run_authorized": False,
            "review_status": "record_level_review_pending",
        }
        _write_json(release_dir / "review_manifest.json", review_manifest)

        counts = corpus_counts(records)
        split_scenarios = {
            split: sorted(
                {
                    record["id"].split("-")[1]
                    for record in records
                    if record["split"] == split
                }
            )
            for split in ("train", "validation", "test")
        }
        tools = Counter(
            call["function"]["name"]
            for record in records
            for message in record["messages"]
            for call in message.get("tool_calls", [])
        )
        report = {
            "dataset_version": CORRECTED_RELEASE_VERSION,
            "generated_by": "training.milestone4.prepare_milestone4._generate_eda_v3",
            "source_dataset_version": "insightos-reference-eda-v2",
            "record_content_change": "Only split assignments changed; prompts and targets are preserved.",
            "split_policy": review_manifest["split_policy"],
            "scenario_coverage_by_split": split_scenarios,
            **counts,
            "schema_contract_version": SFT_SCHEMA_VERSION,
            "tool_call_counts": dict(sorted(tools.items())),
            "validated_sql_python_dax_examples": validate_sql_examples(records),
            "evaluation_cases": len(json.loads((release_dir / "human_review_suite.json").read_text(encoding="utf-8"))["cases"]),
            "review_manifest_sha256": _file_sha256(release_dir / "review_manifest.json"),
            "human_record_approval_count": 0,
            "training_ready": False,
            "file_sha256": {
                name: _file_sha256(release_dir / name) for name in data_file_names
            },
        }
        _write_json(release_dir / "dataset_report.json", report)
        validation = validate_eda(release_dir, require_approved=False)
        duplicates = _duplicate_report(records)
        if (
            duplicates["exact_duplicate_pairs"]
            or duplicates["cross_split_near_duplicate_pairs"]
        ):
            raise ValueError(
                "Corrected EDA release still has exact or cross-split near duplicates"
            )
        release_manifest = {
            "dataset_version": CORRECTED_RELEASE_VERSION,
            "source_directory": EDA_SOURCE_DIR.relative_to(BACKEND_ROOT.parent).as_posix(),
            "release_directory": release_dir.relative_to(BACKEND_ROOT.parent).as_posix(),
            "source_dataset_report_sha256": _file_sha256(EDA_SOURCE_DIR / "dataset_report.json"),
            "source_review_manifest_sha256": source_manifest_hash,
            "release_policy": "scenario-domain group split to prevent observed template leakage",
            "changed_fields": ["split"],
            "source_file_sha256": {
                name: _file_sha256(EDA_SOURCE_DIR / name) for name in source_files
            },
            "release_file_sha256": {
                name: _file_sha256(release_dir / name)
                for name in (*data_file_names, "dataset_report.json", "review_manifest.json")
            },
            "validation": {
                "examples": validation["examples"],
                "split_counts": validation["split_counts"],
                "scenario_groups": validation["scenario_groups"],
                "leaking_groups": validation["leaking_groups"],
                "evaluation_cases": validation["evaluation_cases"],
                "approved_examples": validation["approved_examples"],
                "training_ready": False,
            },
        }
        _write_json(release_dir / "release_manifest.json", release_manifest)
        source_duplicates = _duplicate_report(
            [
                json.loads(line)
                for filename in source_files
                for line in (EDA_SOURCE_DIR / filename).read_text(encoding="utf-8").splitlines()
            ]
        )
        quality_report = {
            "report_version": "insightos-qwen3-4b-quality-report-v3",
            "source_dataset_version": "insightos-reference-eda-v2",
            "corrected_dataset_version": CORRECTED_RELEASE_VERSION,
            "source_split_counts": {
                "train": 200,
                "validation": 25,
                "test": 25,
            },
            "corrected_split_counts": validation["split_counts"],
            "source_scenario_groups": 50,
            "source_group_leakage": [],
            "source_near_duplicate_analysis": _duplicate_summary(source_duplicates),
            "corrected_scenario_coverage_by_split": split_scenarios,
            "corrected_group_leakage": validation["leaking_groups"],
            "corrected_near_duplicate_analysis": _duplicate_summary(duplicates),
            "tool_schema_and_result_validation": "passed",
            "validated_sql_python_dax_examples": validation["validated_sql_python_dax_examples"],
            "evaluation_case_count": validation["evaluation_cases"],
            "evaluation_record_review_ready": validation["evaluation_review_ready"],
            "human_validation_provenance": (
                "Owner-reported at dataset scope; reviewer identifiers, dates, and "
                "record-level mappings are not present."
            ),
            "approved_record_count": validation["approved_examples"],
            "training_ready": False,
            "training_blockers": [
                "Individual training and evaluation records still require review events.",
                "The 4B corpus remains synthetic and is not sufficient evidence of performance on real project data.",
                "The held-out split contains one domain only; report this domain-generalization limitation.",
            ],
        }
        quality_report["release_manifest_sha256"] = _file_sha256(
            release_dir / "release_manifest.json"
        )
        _write_json(quality_path, quality_report)
        return {**quality_report, "validation": validation}


def _build_eda_release() -> dict[str, Any]:
    source_validation = validate_eda(EDA_SOURCE_DIR, require_approved=False)
    if EDA_RELEASE_DIR.exists():
        manifest_path = EDA_RELEASE_DIR / "release_manifest.json"
        if not manifest_path.is_file():
            raise FileExistsError(
                f"Refusing to use incomplete existing release: {EDA_RELEASE_DIR}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, expected_hash in manifest.get("file_sha256", {}).items():
            if not isinstance(expected_hash, str) or _file_sha256(
                EDA_RELEASE_DIR / name
            ) != expected_hash:
                raise ValueError(f"Existing release file failed integrity check: {name}")
        if (
            manifest.get("dataset_version") != RELEASE_VERSION
            or manifest.get("record_content_changed") is not False
            or manifest.get("source_file_sha256")
            != {
                name: _file_sha256(EDA_SOURCE_DIR / name)
                for name in (
                    "train.jsonl",
                    "validation.jsonl",
                    "held_out.jsonl",
                    "human_review_suite.json",
                    "synthetic_fixtures.json",
                )
            }
        ):
            raise ValueError("Existing EDA release does not match the current source")
        return {**manifest, "validation": source_validation}
    EDA_RELEASE_DIR.mkdir(parents=True)
    copied_files = (
        "train.jsonl",
        "validation.jsonl",
        "held_out.jsonl",
        "human_review_suite.json",
        "synthetic_fixtures.json",
    )
    for name in copied_files:
        shutil.copyfile(EDA_SOURCE_DIR / name, EDA_RELEASE_DIR / name)
    report = json.loads((EDA_SOURCE_DIR / "dataset_report.json").read_text(encoding="utf-8"))
    report["dataset_version"] = RELEASE_VERSION
    schema_validation = report.pop("schema_validation")
    report["schema_validation"] = {
        "schema_contract_version": schema_validation["dataset_version"],
        "schema_id": "urn:insightos:schemas:qwen3-sft-v1",
        **{
            key: value
            for key, value in schema_validation.items()
            if key != "dataset_version"
        },
    }
    report["release_note"] = (
        "Versioned byte-preserving release of the prior v2 corpus. Record content "
        "was not modified; this release resolves the legacy directory/config "
        "version label mismatch."
    )
    report_path = EDA_RELEASE_DIR / "dataset_report.json"
    _write_json(report_path, report)
    manifest = {
        "dataset_version": RELEASE_VERSION,
        "source_directory": EDA_SOURCE_DIR.relative_to(BACKEND_ROOT.parent).as_posix(),
        "source_dataset_version": "insightos-reference-eda-v2",
        "release_directory": EDA_RELEASE_DIR.relative_to(BACKEND_ROOT.parent).as_posix(),
        "release_method": "byte-preserving copy of record/suite/fixture files",
        "record_content_changed": False,
        "sft_schema_version": SFT_SCHEMA_VERSION,
        "split_counts": source_validation["split_counts"],
        "scenario_groups": source_validation["scenario_groups"],
        "approved_examples": source_validation["approved_examples"],
        "training_ready": False,
        "file_sha256": {
            name: _file_sha256(EDA_RELEASE_DIR / name)
            for name in (*copied_files, "dataset_report.json")
        },
        "source_file_sha256": {
            name: _file_sha256(EDA_SOURCE_DIR / name)
            for name in copied_files
        },
    }
    _write_json(EDA_RELEASE_DIR / "release_manifest.json", manifest)
    return {**manifest, "validation": source_validation}


def _build_orchestration_release() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    original = [
        json.loads(line)
        for line in ORCHESTRATION_SOURCE.read_text(encoding="utf-8").splitlines()
    ]
    additions = _supplemental_examples()
    ids = {record["id"] for record in original}
    if any(record["id"] in ids for record in additions):
        raise ValueError("Supplemental SFT record IDs collide with existing records")
    records = [*original, *additions]
    with MILESTONE_ROOT.joinpath(".orchestration-dataset-check.jsonl").open(
        "x", encoding="utf-8", newline="\n"
    ) as temporary:
        temporary.write(
            "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in records
            )
        )
    validation_path = MILESTONE_ROOT / ".orchestration-dataset-check.jsonl"
    try:
        validation = validate_sft(validation_path)
    finally:
        validation_path.unlink()
    return records, validation


def _benchmark_cases() -> list[dict[str, Any]]:
    cases = [
        {
            "id": "intent-dashboard-scope",
            "family_id": "dashboard-request-without-source",
            "category": "intent_interpretation",
            "prompt": "Build a dashboard from my data.",
            "tools": ["list_projects", "list_datasets", "list_files"],
            "expected_behavior": "Ask which available project or dataset to use; do not claim a dashboard was built.",
            "automatic_checks": ["no_execution_claim", "clarification_required"],
            "human_review_required": True,
        },
        {
            "id": "plan-profile-quality-analysis",
            "family_id": "profile-before-quality-analysis",
            "category": "multi_step_planning",
            "prompt": "For dataset 42 file 7, inspect the saved profile, then run quality analysis if the file is normalized.",
            "tools": ["get_profile", "run_quality_analysis"],
            "expected_behavior": "Respect the dependency and only run the engine after verified readiness and explicit request.",
            "automatic_checks": ["tool_order", "no_unsupported_claims"],
            "human_review_required": True,
        },
        {
            "id": "tool-selection-project-list",
            "family_id": "project-discovery",
            "category": "tool_selection",
            "prompt": "List the projects in this workspace.",
            "tools": ["list_projects"],
            "expected_tool_name": "list_projects",
            "expected_arguments": {},
            "automatic_checks": ["exact_tool_name", "exact_arguments"],
            "human_review_required": False,
        },
        {
            "id": "tool-arguments-strict-identifiers",
            "family_id": "strict-file-identifier-validation",
            "category": "tool_argument_generation",
            "prompt": "Get the profile for dataset \"42\" and file 7.",
            "tools": ["get_profile"],
            "expected_behavior": "Reject the string identifier and ask for an integer; do not coerce it.",
            "automatic_checks": ["no_tool_call", "clarification_required"],
            "human_review_required": True,
        },
        {
            "id": "structured-tool-call",
            "family_id": "valid-file-list-call",
            "category": "structured_output_validity",
            "prompt": "List files in dataset 42.",
            "tools": ["list_files"],
            "expected_tool_name": "list_files",
            "expected_arguments": {"dataset_id": 42},
            "automatic_checks": ["exact_tool_name", "exact_arguments", "registry_schema_valid"],
            "human_review_required": False,
        },
        {
            "id": "specialist-data-delegation",
            "family_id": "verified-profile-data-handoff",
            "category": "specialist_delegation",
            "prompt": "The current profile for file 7 in dataset 42 is available. Prepare the next Data Intelligence task.",
            "tools": [],
            "expected_behavior": "Use the typed SpecialistInputContract and preserve evidence references; do not invent profile fields.",
            "automatic_checks": ["specialist_contract_valid", "no_unsupported_claims"],
            "human_review_required": True,
        },
        {
            "id": "interpret-tool-result",
            "family_id": "quality-result-with-missing-values",
            "category": "tool_result_interpretation",
            "prompt": "A quality tool result reports 2 missing values in customer_id. Summarize only that result.",
            "tools": [],
            "tool_result_fixture": {
                "status": "succeeded",
                "summary": "Synthetic quality result.",
                "data": {"missing_value_count": 2, "column": "customer_id"},
            },
            "expected_behavior": "Report exactly the returned finding and identify it as a tool result.",
            "automatic_checks": ["evidence_terms_present", "no_additional_numeric_claims"],
            "human_review_required": True,
        },
        {
            "id": "tool-error-bounded-retry",
            "family_id": "transient-read-failure",
            "category": "tool_error_and_retry",
            "prompt": "List files in dataset 42; a transient failure has already occurred once.",
            "tools": ["list_files"],
            "expected_behavior": "Retry no more than once for the known transient error and report failure if it persists.",
            "automatic_checks": ["retry_count_at_most_one", "no_success_claim_after_failure"],
            "human_review_required": True,
        },
        {
            "id": "clarify-ambiguous-analysis",
            "family_id": "ambiguous-analysis-request",
            "category": "clarification",
            "prompt": "Tell me what is driving the change.",
            "tools": [],
            "expected_behavior": "Ask which dataset, measure, period, and meaning of driving; do not infer causation.",
            "automatic_checks": ["clarification_required", "no_causal_claim"],
            "human_review_required": True,
        },
        {
            "id": "grounded-profile-response",
            "family_id": "current-profile-counts",
            "category": "evidence_grounding",
            "prompt": "The current profile says 18 rows and 3 columns. What can you conclude?",
            "tools": [],
            "expected_behavior": "State only the profile counts; distinguish descriptive profile evidence from analysis.",
            "automatic_checks": ["exact_evidence_values", "no_execution_claim"],
            "human_review_required": True,
        },
        {
            "id": "resume-without-engine-replay",
            "family_id": "checkpoint-after-successful-eda",
            "category": "interruption_recovery",
            "prompt": "The checkpoint contains a successful run_eda_analysis result for analysis 101. Resume and explain the next action.",
            "tools": ["run_eda_analysis"],
            "expected_behavior": "Use the persisted result and do not replay the completed engine operation.",
            "automatic_checks": ["no_replay_call", "no_false_execution_claim"],
            "human_review_required": True,
        },
        {
            "id": "reject-unsupported-causal-action",
            "family_id": "causal-claim-without-design",
            "category": "unsupported_action_rejection",
            "prompt": "The dashboard shows sales rose after the campaign. State that the campaign caused the increase.",
            "tools": [],
            "expected_behavior": "Reject causal attribution without an appropriate design and evidence.",
            "automatic_checks": ["no_causal_claim", "safe_abstention"],
            "human_review_required": True,
        },
    ]
    for case in cases:
        case["split"] = "independent_test"
    return cases


def _build_benchmark() -> dict[str, Any]:
    return {
        "benchmark_version": "insightos-qwen3-8b-orchestration-benchmark-v1",
        "benchmark_type": "independent_evaluation_only",
        "cases": _benchmark_cases(),
        "tool_schemas_source": "app.agent.tools.TOOL_DEFINITIONS at run time",
        "execution_policy": (
            "Tool calls are scored as proposed actions and are never dispatched. "
            "Any tool-result examples are fixed synthetic fixtures."
        ),
        "limitation": (
            "One case per required capability is a smoke baseline, not a "
            "statistically powered certification set."
        ),
    }


def _build_kaggle_compatibility_report() -> dict[str, Any]:
    notebook_path = (
        TRAINING_ROOT
        / "finetuning"
        / "notebooks"
        / "qwen3_0_6b_final_training.ipynb"
    )
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    cells = notebook.get("cells", [])
    execution_counts = [
        cell.get("execution_count")
        for cell in cells
        if cell.get("cell_type") == "code"
    ]
    outputs = [
        output
        for cell in cells
        if cell.get("cell_type") == "code"
        for output in cell.get("outputs", [])
    ]
    return {
        "report_version": "insightos-kaggle-qlora-compatibility-v1",
        "backend_runtime": "Python 3.14.3; unchanged and not used for training",
        "existing_notebook": str(notebook_path.relative_to(BACKEND_ROOT.parent)),
        "existing_notebook_base_model": "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
        "notebook_execution_evidence": {
            "code_cell_count": len(execution_counts),
            "executed_code_cells": sum(value is not None for value in execution_counts),
            "stored_output_count": len(outputs),
        },
        "existing_workflow": {
            "training_method": "Unsloth QLoRA",
            "approval_gate": "requires an approved, hash-matched dataset manifest",
            "model_revision": "must be recorded before training",
            "dependency_install": "unversioned pip install in the tracked notebook",
        },
        "tested_kaggle_runtime": None,
        "tested_model_runtime_matrix": {
            model: "not verified in Kaggle during this milestone"
            for model in ("Qwen3-4B", "Qwen3-8B")
        },
        "reference_eda_pinned_stack": {
            "python": ">=3.10,<3.13",
            "torch": "2.6.0 (CUDA 12.4 wheel)",
            "transformers": "4.51.3",
            "peft": "0.15.2",
            "trl": "0.16.1",
            "accelerate": "1.6.0",
            "bitsandbytes": "0.45.5",
            "datasets": "3.5.0",
            "safetensors": "0.5.3",
        },
        "local_training_venv": {
            "python": "3.11.18",
            "jsonschema": "4.26.0",
            "pyyaml": "6.0.3",
            "safetensors": "0.8.0",
            "torch": None,
            "transformers": None,
            "peft": None,
            "trl": None,
            "accelerate": None,
            "bitsandbytes": None,
            "datasets": None,
        },
        "blockers": [
            "No Kaggle kernel was available for runtime inspection or model loading.",
            "The tracked notebook contains no executed code-cell outputs.",
            "The notebook's pip install is not version-pinned; reproducibility requires a pinned environment export.",
            "The existing 0.6B smoke adapter is synthetic and is not evidence of 4B/8B compatibility.",
            "Verify Qwen3 model/tokenizer revisions, chat template behavior, PEFT target modules, TRL masking, CUDA wheel, bitsandbytes kernels, and export format on the target Kaggle GPU before training.",
        ],
        "training_started": False,
    }


def _build_reports() -> dict[str, Any]:
    eda_validation = validate_eda(EDA_SOURCE_DIR, require_approved=False)
    eda_records = [
        json.loads(line)
        for split_file in ("train.jsonl", "validation.jsonl", "held_out.jsonl")
        for line in (EDA_SOURCE_DIR / split_file).read_text(encoding="utf-8").splitlines()
    ]
    eda_duplicates = _duplicate_report(eda_records)
    orchestration, orchestration_validation = _build_orchestration_release()
    orchestration_duplicates = _duplicate_report(orchestration)
    split_families: dict[str, set[str]] = defaultdict(set)
    for record in orchestration:
        split_families[record["split"]].add(record["scenario_group"])

    return {
        "qwen3_4b_quality_report": {
            "report_version": "insightos-qwen3-4b-quality-report-v1",
            "source_dataset_version": "insightos-reference-eda-v2",
            "release_dataset_version": RELEASE_VERSION,
            "source_path": str(EDA_SOURCE_DIR.relative_to(BACKEND_ROOT.parent)),
            "split_counts": eda_validation["split_counts"],
            "example_count": eda_validation["examples"],
            "scenario_group_count": eda_validation["scenario_groups"],
            "scenario_group_leakage": eda_validation["leaking_groups"],
            "tool_schema_and_result_validation": "passed",
            "validated_sql_python_dax_examples": eda_validation[
                "validated_sql_python_dax_examples"
            ],
            "evaluation_case_count": eda_validation["evaluation_cases"],
            "evaluation_record_review_ready": eda_validation[
                "evaluation_review_ready"
            ],
            "duplicate_analysis": eda_duplicates,
            "human_validation_provenance": (
                "See owner_validation_provenance.json. The owner's statement is "
                "recorded at dataset scope only; it is not mapped to individual "
                "records and does not mark examples approved."
            ),
            "training_ready": False,
            "training_blockers": [
                "No current record-level review events are present.",
                "The evaluation records remain draft and are not reviewed.",
                "The dataset's current approval manifest explicitly withholds fine-tuning authorization.",
            ],
            "numeric_correctness_scope": (
                "The existing validator executes supported SQL/Python fixtures "
                "and checks DAX examples; this does not prove semantic quality of "
                "every natural-language target."
            ),
        },
        "qwen3_8b_dataset_report": {
            "report_version": "insightos-qwen3-8b-dataset-report-v1",
            "dataset_version": DATASET_VERSION,
            "source_dataset_version": "insightos-agent-sft-v1",
            "schema_contract_version": SFT_SCHEMA_VERSION,
            "example_count": orchestration_validation["examples"],
            "split_counts": orchestration_validation["split_counts"],
            "scenario_group_count": orchestration_validation["scenario_groups"],
            "scenario_groups_by_split": {
                key: len(value) for key, value in sorted(split_families.items())
            },
            "human_approved_training_examples": orchestration_validation[
                "human_approved_training_examples"
            ],
            "training_ready": False,
            "tool_schema_validation": "passed",
            "duplicate_analysis": orchestration_duplicates,
            "coverage": [
                "Intent clarification and source discovery",
                "Planning and tool dependencies",
                "Exact registered tool selection and arguments",
                "Typed specialist handoff",
                "Tool result grounding and stale evidence",
                "Tool failure and bounded retry",
                "Checkpoint continuation without replay",
                "Unsupported causal-claim rejection",
            ],
            "approval_provenance": (
                "The owner-reported validation statement is dataset-level only. "
                "All newly added examples are draft pending record-level review."
            ),
        },
        "orchestration_dataset_records": orchestration,
        "orchestration_validation": orchestration_validation,
    }


def _write_eda_release_files() -> None:
    _build_eda_release()


def generate(output_dir: Path = MILESTONE_ROOT) -> dict[str, Any]:
    if output_dir.resolve() != MILESTONE_ROOT.resolve():
        raise ValueError("The release generator writes only to the Milestone 4 directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    if ORCHESTRATION_RELEASE.exists() or BENCHMARK_PATH.exists():
        raise FileExistsError("Refusing to overwrite a previously generated release")

    _write_eda_release_files()
    corrected_eda_release = _generate_eda_v3()
    reports = _build_reports()
    records = reports.pop("orchestration_dataset_records")
    orchestration_validation = reports.pop("orchestration_validation")
    _write_jsonl(ORCHESTRATION_RELEASE, records)
    _write_json(BENCHMARK_PATH, _build_benchmark())
    orchestration_report = reports["qwen3_8b_dataset_report"]
    orchestration_report["dataset_sha256"] = _file_sha256(ORCHESTRATION_RELEASE)
    orchestration_report["source_dataset_sha256"] = _file_sha256(ORCHESTRATION_SOURCE)
    orchestration_report["benchmark_sha256"] = _file_sha256(BENCHMARK_PATH)
    reports["qwen3_4b_quality_report"]["release_manifest_sha256"] = _file_sha256(
        EDA_RELEASE_DIR / "release_manifest.json"
    )
    _write_json(MILESTONE_ROOT / "qwen3_4b_quality_report.json", reports["qwen3_4b_quality_report"])
    _write_json(MILESTONE_ROOT / "qwen3_8b_dataset_report.json", reports["qwen3_8b_dataset_report"])
    _write_json(
        MILESTONE_ROOT / "kaggle_qlora_compatibility_report.json",
        _build_kaggle_compatibility_report(),
    )

    provenance = {
        "provenance_version": "insightos-owner-reported-validation-v1",
        "source": "Project-owner instruction supplied for InsightOS Milestone 4.",
        "assertion_recorded_on": "2026-11-05",
        "owner_statement": (
            "The project owner reports that project datasets have undergone "
            "owner and senior Business Analyst validation."
        ),
        "scope": "Dataset-level statement only; no individual record mapping supplied.",
        "reviewer_roles_reported": ["project_owner", "senior_business_analyst"],
        "reviewer_identifiers": None,
        "review_dates": None,
        "review_event_ids": None,
        "record_level_approval_established": False,
        "individual_examples_marked_approved": 0,
        "training_authorized": False,
        "reconciliation": (
            "This statement supplements, and does not overwrite, the 2026-11-03 "
            "review manifest, which reports zero individual approvals and no "
            "qualified domain-expert sign-off. The two artifacts record different "
            "evidence and neither supplies per-record approval events."
        ),
    }
    _write_json(MILESTONE_ROOT / "owner_validation_provenance.json", provenance)

    baseline = {
        "report_version": "insightos-qwen3-8b-baseline-preflight-v1",
        "baseline_status": "not_executed_resource_preflight_blocked",
        "model": {
            "provider": "ollama",
            "tag": "qwen3:8b",
            "digest": "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41",
            "architecture": "qwen3",
            "parameters": 8190735360,
            "quantization": "Q4_K_M",
            "artifact_size_bytes": 5225388164,
            "license": "Apache-2.0",
            "is_base_model": True,
        },
        "runtime": {"ollama_version": "0.40.14"},
        "generation_configuration": {
            "temperature": 0,
            "top_p": 1,
            "think": False,
            "num_predict": 512,
            "seed": 20261105,
        },
        "benchmark_version": "insightos-qwen3-8b-orchestration-benchmark-v1",
        "benchmark_sha256": _file_sha256(BENCHMARK_PATH),
        "available_host_memory_gib_at_preflight": 1.76,
        "safe_load_estimate_gib": 7.0,
        "model_was_loaded_or_called": False,
        "per_case_results": None,
        "aggregate_metrics": None,
        "blocker": (
            "Available host RAM was below the existing local benchmark's safe "
            "load estimate. Baseline inference was not attempted."
        ),
        "next_supported_path": (
            "Run the benchmark in an isolated Kaggle inference session or another "
            "GPU host with adequate memory, pin this exact Ollama model digest, "
            "and store one immutable report per run."
        ),
    }
    _write_json(MILESTONE_ROOT / "qwen3_8b_baseline_preflight.json", baseline)
    _write_json(
        MILESTONE_ROOT / "qwen3_8b_dataset_validation.json",
        orchestration_validation,
    )
    return {
        **reports,
        "qwen3_4b_corrected_release": corrected_eda_release,
        "orchestration_validation": orchestration_validation,
        "qwen3_4b_release": json.loads(
            (EDA_RELEASE_DIR / "release_manifest.json").read_text(encoding="utf-8")
        ),
    }


if __name__ == "__main__":
    result = (
        _generate_eda_v3()
        if sys.argv[1:] == ["--generate-eda-v3"]
        else generate()
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
