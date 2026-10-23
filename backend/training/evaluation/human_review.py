from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from training.evaluation.hf_runner import (
    RESCORED_SCHEMA_VERSION,
    SCORING_VERSION,
    load_suite,
    rescore_saved_report,
)


RECONCILED_SCHEMA_VERSION = "insightos-reconciled-evaluation-report-v1"
REVIEW_RECORD_KEYS = {
    "source_report",
    "source_report_sha256",
    "source_run_id",
    "reviewed_at",
    "reviewer",
    "cases",
}
REVIEW_CASE_KEYS = {
    "id",
    "review_status",
    "review_result",
    "notes",
}
VALID_REVIEW_RESULTS = {"passed", "failed"}
MACHINE_STATUSES = ("passed", "failed", "error", "skipped", "needs_review")
RECONCILED_STATUSES = (
    "passed_automated",
    "passed_automated_and_human_review",
    "passed_human_review",
    "failed_automated",
    "failed_automated_human_passed",
    "failed_automated_and_human_review",
    "failed_human_review",
    "error",
    "skipped",
    "unresolved",
)


def _read_json_object(path: Path, description: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not read {description} as valid JSON.") from error
    if not isinstance(value, dict):
        raise ValueError(f"The {description} must contain a JSON object.")
    return value, raw


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Review record field '{field}' must be a non-empty string.")
    return value


def _validate_review_timestamp(value: Any) -> str:
    timestamp = _nonempty_string(value, "reviewed_at")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Review record field 'reviewed_at' must be an ISO timestamp.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Review timestamp must include a timezone.")
    return timestamp


def _source_cases(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Source report must contain a cases array.")
    by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Every source report case must be a JSON object.")
        case_id = _nonempty_string(case.get("id"), "source case id")
        if case_id in by_id:
            raise ValueError(f"Source report contains duplicate case ID '{case_id}'.")
        if case.get("status") not in MACHINE_STATUSES:
            raise ValueError(f"Source case '{case_id}' has an invalid machine status.")
        by_id[case_id] = case
    return by_id


def _validate_review_record(
    review: dict[str, Any],
    *,
    report_path: Path,
    report: dict[str, Any],
    report_sha256: str,
    source_cases: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if set(review) != REVIEW_RECORD_KEYS:
        raise ValueError("Review record has missing or unexpected top-level fields.")
    if review.get("source_report") != report_path.name:
        raise ValueError("Review record does not identify this source report.")
    if review.get("source_report_sha256") != report_sha256:
        raise ValueError("Review record source-report SHA-256 does not match.")
    run_id = _nonempty_string(review.get("source_run_id"), "source_run_id")
    if run_id != report.get("run_id"):
        raise ValueError("Review record source run ID does not match the report.")
    _validate_review_timestamp(review.get("reviewed_at"))
    _nonempty_string(review.get("reviewer"), "reviewer")

    entries = review.get("cases")
    if not isinstance(entries, list):
        raise ValueError("Review record field 'cases' must be an array.")
    reviews_by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != REVIEW_CASE_KEYS:
            raise ValueError("Each review entry must contain exactly the established fields.")
        case_id = _nonempty_string(entry.get("id"), "review case id")
        if case_id in reviews_by_id:
            raise ValueError(f"Review record contains duplicate case ID '{case_id}'.")
        source_case = source_cases.get(case_id)
        if source_case is None:
            raise ValueError(f"Review record contains unknown case ID '{case_id}'.")
        if source_case.get("human_review_status") != "pending":
            raise ValueError(
                f"Review entry for '{case_id}' conflicts with its source human-review status."
            )
        if entry.get("review_status") != "reviewed":
            raise ValueError(f"Review entry for '{case_id}' is not marked reviewed.")
        if entry.get("review_result") not in VALID_REVIEW_RESULTS:
            raise ValueError(f"Review entry for '{case_id}' has an invalid decision.")
        _nonempty_string(entry.get("notes"), f"notes for {case_id}")
        reviews_by_id[case_id] = entry
    return reviews_by_id


def _reconciled_status(
    machine_status: str,
    review_result: str | None,
) -> str:
    if machine_status == "passed":
        if review_result == "passed":
            return "passed_automated_and_human_review"
        if review_result == "failed":
            return "failed_human_review"
        return "passed_automated"
    if machine_status == "needs_review":
        if review_result == "passed":
            return "passed_human_review"
        if review_result == "failed":
            return "failed_human_review"
        return "unresolved"
    if machine_status == "failed":
        if review_result == "passed":
            return "failed_automated_human_passed"
        if review_result == "failed":
            return "failed_automated_and_human_review"
        return "failed_automated"
    return machine_status


def reconcile_report(report_path: Path, review_path: Path) -> dict[str, Any]:
    """Build an immutable, provenance-bound view of source, current, and human results."""
    report_path = report_path.resolve()
    review_path = review_path.resolve()
    report_bytes = report_path.read_bytes()
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    try:
        report = json.loads(report_bytes)
    except ValueError as error:
        raise ValueError("Source report is not valid JSON.") from error
    if not isinstance(report, dict):
        raise ValueError("Source report must contain a JSON object.")
    source_cases = _source_cases(report)
    review, review_bytes = _read_json_object(review_path, "human-review record")
    reviews_by_id = _validate_review_record(
        review,
        report_path=report_path,
        report=report,
        report_sha256=report_sha256,
        source_cases=source_cases,
    )

    current = rescore_saved_report(report_path)
    if hashlib.sha256(report_path.read_bytes()).hexdigest() != report_sha256:
        raise ValueError("Source report changed during reconciliation.")
    review_sha256 = hashlib.sha256(review_bytes).hexdigest()
    if hashlib.sha256(review_path.read_bytes()).hexdigest() != review_sha256:
        raise ValueError("Human-review record changed during reconciliation.")
    current_by_id = {case["id"]: case for case in current["cases"]}
    reconciled_cases = []
    for case_id, source_case in source_cases.items():
        current_case = current_by_id.get(case_id)
        current_status = (
            current_case["rescored_status"]
            if current_case is not None
            else "skipped"
        )
        human = reviews_by_id.get(case_id)
        decision = human.get("review_result") if human is not None else None
        reconciled_cases.append(
            {
                "id": case_id,
                "source_case": source_case,
                "source_machine_status": source_case["status"],
                "source_failure_category": source_case.get("failure_category"),
                "current_automated_status": current_status,
                "current_failure_category": (
                    current_case.get("failure_category")
                    if current_case is not None
                    else "case_not_in_current_suite"
                ),
                "current_machine_checks": (
                    current_case.get("machine_checks", {})
                    if current_case is not None
                    else {}
                ),
                "human_review_status": (
                    "reviewed"
                    if human is not None
                    else source_case.get("human_review_status", "pending")
                ),
                "human_decision": decision,
                "human_review_notes": human.get("notes") if human is not None else None,
                "reconciled_status": _reconciled_status(current_status, decision),
            }
        )

    decisions = Counter(
        item["human_decision"]
        for item in reconciled_cases
        if item["human_decision"] is not None
    )
    source_counts = Counter(item["source_machine_status"] for item in reconciled_cases)
    current_counts = Counter(item["current_automated_status"] for item in reconciled_cases)
    final_counts = Counter(item["reconciled_status"] for item in reconciled_cases)
    original_meta = {
        key: report.get(key)
        for key in (
            "schema_version",
            "run_id",
            "timestamp_utc",
            "scoring_version",
            "suite_version",
            "model",
        )
    }
    suite_version, _ = load_suite()
    return {
        "schema_version": RECONCILED_SCHEMA_VERSION,
        "source_report": {
            "path": report_path.name,
            "sha256": report_sha256,
            **original_meta,
        },
        "human_review": {
            "path": review_path.name,
            "reviewed_at": review["reviewed_at"],
            "reviewer": review["reviewer"],
            "review_record_sha256": review_sha256,
        },
        "current_automated_scoring": {
            "scoring_version": SCORING_VERSION,
            "suite_version": suite_version,
            "rescored_schema_version": RESCORED_SCHEMA_VERSION,
        },
        "summary": {
            "source_machine_status_counts": {
                status: source_counts.get(status, 0) for status in MACHINE_STATUSES
            },
            "current_machine_status_counts": {
                status: current_counts.get(status, 0) for status in MACHINE_STATUSES
            },
            "human_review_counts": {
                "passed": decisions.get("passed", 0),
                "failed": decisions.get("failed", 0),
                "unreviewed": sum(
                    item["human_review_status"] == "pending"
                    for item in reconciled_cases
                ),
            },
            "reconciled_outcome_counts": {
                status: final_counts.get(status, 0) for status in RECONCILED_STATUSES
            },
        },
        "status_rule": (
            "Source machine status is retained unchanged. Current automated status "
            "is rescored with the recorded current scoring and suite versions. A "
            "human pass can resolve needs_review but cannot override an automated "
            "failure. Missing human decisions remain unresolved unless the current "
            "automated status already passed."
        ),
        "cases": reconciled_cases,
    }


def comparison_reconciliation_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_report": result["source_report"],
        "human_review": result["human_review"],
        "current_automated_scoring": result["current_automated_scoring"],
        "summary": result["summary"],
        "per_case": [
            {
                "case_id": case["id"],
                "source_machine_status": case["source_machine_status"],
                "current_automated_status": case["current_automated_status"],
                "human_review_status": case["human_review_status"],
                "human_decision": case["human_decision"],
                "reconciled_status": case["reconciled_status"],
            }
            for case in result["cases"]
        ],
    }


def write_reconciled_report(
    result: dict[str, Any],
    output_path: Path,
) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a provenance-checked evaluation report with human review."
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = reconcile_report(arguments.report, arguments.review)
    write_reconciled_report(result, arguments.output)
    print(f"Reconciled report written to {arguments.output.resolve()}")


if __name__ == "__main__":
    main()
