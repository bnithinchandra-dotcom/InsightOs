from __future__ import annotations

import json
import argparse
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from app.agent.contracts import ToolResult
from app.agent.inspection_handoff import InspectionHandoff
from app.agent.tools import DatasetInspectionHandoff, TOOL_DEFINITIONS
from training.reference_eda.build_dataset import SPLIT_FILENAMES
from training.reference_eda.corpus import (
    SCENARIOS,
    build_examples,
    corpus_counts,
    fixture_payload,
    validate_sql_examples,
)
from training.validate_sft_dataset import validate_dataset as validate_sft_dataset


_REVIEW_NOTE_PATTERN = re.compile(
    r"(?:^|;)\s*reviewer=([^;]+);\s*reviewed_on=(\d{4}-\d{2}-\d{2})(?:;|$)"
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def validate_dataset(
    dataset_dir: Path,
    *,
    require_approved: bool = False,
    require_evaluation_review: bool = False,
) -> dict[str, Any]:
    examples_by_split: dict[str, list[dict[str, Any]]] = {}
    for split, filename in SPLIT_FILENAMES.items():
        examples = _read_jsonl(dataset_dir / filename)
        if not examples:
            raise ValueError(f"Dataset split {split!r} is empty")
        if any(example.get("split") != split for example in examples):
            raise ValueError(f"{filename} contains a record assigned to another split")
        examples_by_split[split] = examples

    examples = [
        example
        for split in SPLIT_FILENAMES
        for example in examples_by_split[split]
    ]
    counts = corpus_counts(examples)
    if counts["leaking_groups"]:
        raise ValueError(f"Scenario groups leak across splits: {counts['leaking_groups']}")
    ids = [example.get("id") for example in examples]
    if len(set(ids)) != len(ids):
        raise ValueError("Example IDs must be unique across all splits")
    expected_scenarios = {scenario.key for scenario in SCENARIOS}
    for split, split_examples in examples_by_split.items():
        represented_scenarios = {
            example["id"].split("-")[1] for example in split_examples
        }
        if represented_scenarios != expected_scenarios:
            raise ValueError(
                f"Split {split!r} does not represent every approved scenario"
            )

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".jsonl", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        for example in examples:
            temporary.write(json.dumps(example, ensure_ascii=False) + "\n")
    try:
        schema_report = validate_sft_dataset(
            temporary_path,
            require_approved=require_approved,
        )
    finally:
        temporary_path.unlink(missing_ok=True)

    tool_names = {definition.name for definition in TOOL_DEFINITIONS}
    expected_tool_result_fields = set(ToolResult.__dataclass_fields__)
    for example in examples:
        messages = example["messages"]
        for index, message in enumerate(messages):
            for call in message.get("tool_calls", []):
                name = call["function"]["name"]
                arguments = call["function"]["arguments"]
                if name not in tool_names:
                    raise ValueError(f"{example['id']} calls an unregistered tool")
                preceding_context = "\n".join(
                    item["content"] for item in messages[:index]
                )
                if any(
                    f"{key}={arguments[key]}" not in preceding_context
                    for key in ("dataset_id", "file_id")
                    if key in arguments
                ):
                    raise ValueError(
                        f"{example['id']} uses a tool identifier absent from context"
                    )
                result_message = next(
                    (
                        item
                        for item in messages[index + 1 :]
                        if item.get("role") == "tool"
                        and item.get("tool_call_id") == call["id"]
                    ),
                    None,
                )
                if result_message is None:
                    raise ValueError(f"{example['id']} has no matching tool result")
                try:
                    result = json.loads(result_message["content"])
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"{example['id']} has an invalid serialized tool result"
                    ) from error
                if (
                    not isinstance(result, dict)
                    or set(result) != expected_tool_result_fields
                ):
                    raise ValueError(
                        f"{example['id']} tool result does not match ToolResult"
                    )
                data = result.get("data")
                if name == "get_inspection_handoff":
                    InspectionHandoff.model_validate(data)
                if name in {"run_eda_analysis", "run_quality_analysis"}:
                    if not isinstance(data, dict):
                        raise ValueError(f"{example['id']} engine result is invalid")
                    DatasetInspectionHandoff.model_validate(
                        data.get("agent1_inspection")
                    )

    fixture_path = dataset_dir / "synthetic_fixtures.json"
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixtures != fixture_payload():
        raise ValueError("Synthetic fixtures differ from their deterministic source")
    validated_code = validate_sql_examples(examples)

    suite_path = dataset_dir / "human_review_suite.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite_ids = [case.get("id") for case in suite.get("cases", [])]
    expected_suite_ids = [
        example["id"]
        for example in examples_by_split["test"]
        if example["category"] != "tool_selection"
    ]
    if suite_ids != expected_suite_ids:
        raise ValueError("Human-review suite does not match the non-tool held-out cases")
    if any(case.get("scorer") != "human_review" for case in suite["cases"]):
        raise ValueError("Every reference EDA evaluation case must require human review")
    expected_rubric = {
        "business_understanding": 15,
        "analytical_correctness": 20,
        "tool_selection_and_execution_discipline": 15,
        "kpi_and_statistical_interpretation": 15,
        "evidence_and_hallucination_resistance": 15,
        "stakeholder_communication": 10,
        "recommendations_and_next_steps": 10,
    }
    if any(
        case.get("scoring_rubric") != expected_rubric
        or sum(case["scoring_rubric"].values()) != 100
        or not case.get("acceptance_criteria")
        or not case.get("rejection_criteria")
        or not isinstance(case.get("case_rubric"), dict)
        or sum(case["case_rubric"].values()) != 100
        or not case.get("mandatory_facts")
        or not case.get("incorrect_behaviors")
        or not isinstance(case.get("dataset_profile"), dict)
        for case in suite["cases"]
    ):
        raise ValueError(
            "Evaluation cases require the approved weighted and scenario-specific rubric"
        )
    approval_manifest_path = dataset_dir / "approval_manifest.json"
    manifest_path = dataset_dir.parent / "review_manifest.json"
    if manifest_path.exists() and not approval_manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        decisions = manifest.get("decisions")
        if (
            manifest.get("training_ready") is not False
            or manifest.get("fine_tuning_run_authorized") is not False
            or manifest.get("human_record_approval_count") != 0
            or not isinstance(decisions, list)
            or len(decisions) != 5
            or decisions[4].get("status")
            != "fail_closed_policy_accepted_product_coverage_incomplete"
            or counts["approved_examples"] != 0
        ):
            raise ValueError(
                "Review manifest conflicts with the fail-closed, draft-only gate"
            )
    evaluation_review_ready = True
    for case in suite["cases"]:
        status = case.get("validation_status")
        notes = case.get("reviewer_notes", "")
        match = _REVIEW_NOTE_PATTERN.search(notes) if isinstance(notes, str) else None
        if status not in {"reviewed", "approved"} or match is None:
            evaluation_review_ready = False
            continue
        if not match.group(1).strip():
            evaluation_review_ready = False
            continue
        try:
            date.fromisoformat(match.group(2))
        except ValueError:
            evaluation_review_ready = False
    if require_evaluation_review and not evaluation_review_ready:
        raise ValueError(
            "Held-out evaluation cases require reviewer=NAME; "
            "reviewed_on=YYYY-MM-DD and reviewed or approved status"
        )

    return {
        **counts,
        "schema_validation": schema_report,
        "validated_sql_python_dax_examples": validated_code,
        "evaluation_cases": len(suite["cases"]),
        "evaluation_review_ready": evaluation_review_ready,
        "tool_calls_by_name": dict(
            sorted(
                Counter(
                    call["function"]["name"]
                    for example in examples
                    for message in example["messages"]
                    for call in message.get("tool_calls", [])
                ).items()
            )
        ),
        "scenario_coverage_by_split": {
            split: sorted(
                {
                    example["id"].split("-")[1]
                    for example in split_examples
                }
            )
            for split, split_examples in examples_by_split.items()
        },
        "review_manifest_validated": manifest_path.exists(),
        "all_examples_remain_drafts": counts["draft_examples"] == counts["examples"],
    }


def validate_generated_source(dataset_dir: Path) -> None:
    actual = [
        example
        for split, filename in SPLIT_FILENAMES.items()
        for example in _read_jsonl(dataset_dir / filename)
    ]
    expected = build_examples()
    def content_without_review(example: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in example.items()
            if key not in {"validation_status", "reviewer_notes"}
        }

    actual_by_id = {example["id"]: example for example in actual}
    expected_by_id = {example["id"]: example for example in expected}
    if set(actual_by_id) != set(expected_by_id) or any(
        content_without_review(actual_by_id[example_id])
        != content_without_review(expected_by_id[example_id])
        for example_id in expected_by_id
    ):
        raise ValueError("Generated dataset differs from the checked-in source blueprint")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the synthetic EDA corpus and group-held-out evaluation."
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "dataset_v1",
    )
    parser.add_argument("--require-approved", action="store_true")
    parser.add_argument("--check-source", action="store_true")
    args = parser.parse_args()
    try:
        report = validate_dataset(
            args.dataset_dir,
            require_approved=args.require_approved,
        )
        if args.check_source:
            validate_generated_source(args.dataset_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Dataset validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
