from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from training.evaluation.hf_runner import SCORING_VERSION, load_suite
from training.evaluation.human_review import (
    reconcile_report,
    write_reconciled_report,
)
from training.evaluation.ollama_runner import _comparison


REVIEWED_CASE_IDS = (
    "business_revenue_profit",
    "instruction_exactly_two_sentences",
    "missing_churn_inputs",
    "tool_plan_without_execution",
)

FIXTURE_TEXT = {
    "business_revenue_profit": (
        "Revenue is income from sales before costs. Profit is what remains after costs."
    ),
    "instruction_exactly_two_sentences": (
        "Revenue is income from sales. Profit is what remains after costs."
    ),
    "json_profit_margin": (
        '{"metric":"profit_margin","value":25,"unit":"%"}'
    ),
    "missing_churn_inputs": (
        "Churn cannot be calculated because customer counts, churn counts, "
        "and the time period are missing."
    ),
    "analysis_plan_profit_margin": (
        "Revenue and expenses are needed. Group records by month and calculate "
        "profit margin."
    ),
    "tool_plan_without_execution": (
        "Check for revenue and expense fields and validate missing values. "
        "No file was accessed and no tool was executed."
    ),
    "repeat_exact_token": "ORBIT-72",
}


class HumanReviewReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.report_path = self.root / "source-report.json"
        self.review_path = self.root / "source-report-human-review.json"
        self.report = self._source_report()
        self._write_report()
        self.review = self._review_record(REVIEWED_CASE_IDS)
        self._write_review()

    def tearDown(self):
        self.directory.cleanup()

    def _source_report(self) -> dict:
        _, suite_cases = load_suite()
        cases = []
        for case in suite_cases:
            cases.append(
                {
                    "id": case.id,
                    "category": case.category,
                    "status": (
                        "failed"
                        if case.id == "analysis_plan_profit_margin"
                        else "passed"
                        if case.id in {"json_profit_margin", "repeat_exact_token"}
                        else "needs_review"
                    ),
                    "human_review_status": (
                        "not_required"
                        if case.id in {"json_profit_margin", "repeat_exact_token"}
                        else "pending"
                    ),
                    "human_review_rubric": list(case.human_review_rubric),
                    "failure_category": (
                        "analysis_plan_requirement_missing"
                        if case.id == "analysis_plan_profit_margin"
                        else None
                    ),
                    "generations": [
                        {
                            "text": FIXTURE_TEXT[case.id],
                            "generated_tokens": 10,
                            "duration_ms": 5.0,
                            "termination_reason": "eos",
                        }
                    ],
                }
            )
        return {
            "schema_version": "fixture-report-v1",
            "run_id": "fixture-run-id",
            "timestamp_utc": "2026-10-23T17:00:00+00:00",
            "scoring_version": "insightos-hf-scoring-v6",
            "suite_version": "insightos-hf-baseline-v1",
            "model": {"name": "fixture-model", "digest": "fixture-digest"},
            "cases": cases,
        }

    def _write_report(self) -> bytes:
        raw = json.dumps(self.report, ensure_ascii=False, indent=2).encode("utf-8")
        self.report_path.write_bytes(raw)
        return raw

    def _review_record(self, case_ids) -> dict:
        source_bytes = self.report_path.read_bytes()
        return {
            "source_report": self.report_path.name,
            "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_run_id": self.report["run_id"],
            "reviewed_at": "2026-10-23T23:29:06.183+05:30",
            "reviewer": "user",
            "cases": [
                {
                    "id": case_id,
                    "review_status": "reviewed",
                    "review_result": "passed",
                    "notes": f"Human review passed for {case_id}.",
                }
                for case_id in case_ids
            ],
        }

    def _write_review(self):
        self.review_path.write_text(
            json.dumps(self.review, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_reconciles_four_decisions_without_erasing_source_results(self):
        original_hash = hashlib.sha256(self.report_path.read_bytes()).hexdigest()
        result = reconcile_report(self.report_path, self.review_path)

        self.assertEqual(
            result["source_report"]["scoring_version"],
            "insightos-hf-scoring-v6",
        )
        self.assertEqual(
            result["source_report"]["suite_version"],
            "insightos-hf-baseline-v1",
        )
        self.assertEqual(
            result["current_automated_scoring"]["scoring_version"],
            SCORING_VERSION,
        )
        self.assertEqual(result["source_report"]["model"], self.report["model"])
        by_id = {case["id"]: case for case in result["cases"]}
        for case_id in REVIEWED_CASE_IDS:
            self.assertEqual(by_id[case_id]["human_review_status"], "reviewed")
            self.assertEqual(by_id[case_id]["human_decision"], "passed")
            self.assertEqual(by_id[case_id]["reconciled_status"], "passed_human_review")
            self.assertEqual(
                by_id[case_id]["source_case"],
                next(case for case in self.report["cases"] if case["id"] == case_id),
            )
        plan = by_id["analysis_plan_profit_margin"]
        self.assertEqual(plan["source_machine_status"], "failed")
        self.assertEqual(plan["current_automated_status"], "failed")
        self.assertEqual(plan["reconciled_status"], "failed_automated")
        self.assertEqual(
            result["summary"]["human_review_counts"],
            {"passed": 4, "failed": 0, "unreviewed": 1},
        )
        self.assertEqual(
            hashlib.sha256(self.report_path.read_bytes()).hexdigest(),
            original_hash,
        )

    def test_rejects_incorrect_source_hash(self):
        self.review["source_report_sha256"] = "0" * 64
        self._write_review()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            reconcile_report(self.report_path, self.review_path)

    def test_missing_review_entries_remain_unresolved(self):
        self.review = self._review_record(REVIEWED_CASE_IDS[:3])
        self._write_review()
        result = reconcile_report(self.report_path, self.review_path)
        by_id = {case["id"]: case for case in result["cases"]}
        self.assertEqual(
            by_id["tool_plan_without_execution"]["reconciled_status"],
            "unresolved",
        )
        self.assertIsNone(by_id["tool_plan_without_execution"]["human_decision"])
        self.assertEqual(
            by_id["analysis_plan_profit_margin"]["reconciled_status"],
            "failed_automated",
        )
        self.assertEqual(result["summary"]["human_review_counts"]["unreviewed"], 2)

    def test_rejects_duplicate_review_case_ids(self):
        self.review["cases"].append(dict(self.review["cases"][0]))
        self._write_review()
        with self.assertRaisesRegex(ValueError, "duplicate case ID"):
            reconcile_report(self.report_path, self.review_path)

    def test_rejects_unknown_review_case_id(self):
        entry = dict(self.review["cases"][0])
        entry["id"] = "unknown-case"
        self.review["cases"].append(entry)
        self._write_review()
        with self.assertRaisesRegex(ValueError, "unknown case ID"):
            reconcile_report(self.report_path, self.review_path)

    def test_rejects_duplicate_source_case_ids(self):
        self.report["cases"].append(dict(self.report["cases"][0]))
        self._write_report()
        self.review = self._review_record(REVIEWED_CASE_IDS)
        self._write_review()
        with self.assertRaisesRegex(ValueError, "Source report contains duplicate"):
            reconcile_report(self.report_path, self.review_path)

    def test_rejects_review_for_case_not_pending_human_review(self):
        self.review["cases"].append(
            {
                "id": "json_profit_margin",
                "review_status": "reviewed",
                "review_result": "passed",
                "notes": "This review conflicts with the source record.",
            }
        )
        self._write_review()
        with self.assertRaisesRegex(ValueError, "conflicts with its source"):
            reconcile_report(self.report_path, self.review_path)

    def test_rejects_invalid_review_decision_and_metadata(self):
        invalid_review = dict(self.review)
        invalid_review["cases"] = [dict(entry) for entry in self.review["cases"]]
        invalid_review["cases"][0]["review_result"] = "needs_review"
        self.review = invalid_review
        self._write_review()
        with self.assertRaisesRegex(ValueError, "invalid decision"):
            reconcile_report(self.report_path, self.review_path)

        self.review["cases"][0]["review_result"] = "passed"
        self.review["reviewer"] = " "
        self._write_review()
        with self.assertRaisesRegex(ValueError, "reviewer"):
            reconcile_report(self.report_path, self.review_path)

    def test_human_pass_does_not_override_current_automated_failure(self):
        self.review["cases"].append(
            {
                "id": "analysis_plan_profit_margin",
                "review_status": "reviewed",
                "review_result": "passed",
                "notes": "Explicitly reviewed, but automated requirements remain unmet.",
            }
        )
        self._write_review()
        result = reconcile_report(self.report_path, self.review_path)
        plan = next(
            case
            for case in result["cases"]
            if case["id"] == "analysis_plan_profit_margin"
        )
        self.assertEqual(plan["human_decision"], "passed")
        self.assertEqual(plan["current_automated_status"], "failed")
        self.assertEqual(
            plan["reconciled_status"],
            "failed_automated_human_passed",
        )

    def test_reconciliation_is_deterministic(self):
        first = reconcile_report(self.report_path, self.review_path)
        second = reconcile_report(self.report_path, self.review_path)
        self.assertEqual(first, second)

    def test_writes_only_a_new_derived_report(self):
        original_bytes = self.report_path.read_bytes()
        result = reconcile_report(self.report_path, self.review_path)
        output_path = self.root / "derived-reconciled.json"
        write_reconciled_report(result, output_path)
        self.assertEqual(
            json.loads(output_path.read_text(encoding="utf-8"))["schema_version"],
            "insightos-reconciled-evaluation-report-v1",
        )
        with self.assertRaises(FileExistsError):
            write_reconciled_report(result, output_path)
        self.assertEqual(self.report_path.read_bytes(), original_bytes)

    def test_preserves_legacy_report_fields_and_generated_text(self):
        result = reconcile_report(self.report_path, self.review_path)
        source_case = next(
            case
            for case in result["cases"]
            if case["id"] == "business_revenue_profit"
        )["source_case"]
        self.assertEqual(
            source_case["generations"][0]["text"],
            FIXTURE_TEXT["business_revenue_profit"],
        )
        self.assertEqual(
            result["source_report"]["timestamp_utc"],
            self.report["timestamp_utc"],
        )
        self.assertEqual(
            result["source_report"]["run_id"],
            self.report["run_id"],
        )

    def test_comparison_summary_includes_separate_reconciliation_when_present(self):
        comparison = _comparison(self.report_path, [])
        summary = comparison["baseline_human_review_reconciliation"]
        self.assertEqual(
            summary["source_report"]["sha256"],
            hashlib.sha256(self.report_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            summary["current_automated_scoring"]["scoring_version"],
            SCORING_VERSION,
        )
        self.assertEqual(
            next(
                item
                for item in summary["per_case"]
                if item["case_id"] == "analysis_plan_profit_margin"
            )["reconciled_status"],
            "failed_automated",
        )

    def test_comparison_summary_remains_compatible_without_review_record(self):
        self.review_path.unlink()
        comparison = _comparison(self.report_path, [])
        self.assertIsNone(comparison["baseline_human_review_reconciliation"])


if __name__ == "__main__":
    unittest.main()
