import copy
import unittest
from datetime import datetime, timedelta, timezone

from app.quality_analysis import (
    MAX_QUALITY_REPORT_BYTES,
    MAX_QUALITY_FINDINGS,
    QualityAnalysisError,
    build_contract_report,
    finding_id_for,
    quality_analysis_key,
    normalized_summary_totals,
    validate_quality_report,
)


class QualityAnalysisContractTests(unittest.TestCase):
    def setUp(self):
        self.source_sha256 = "a" * 64
        self.normalization_result = {
            "tables": [
                {
                    "table_index": 0,
                    "sha256": "b" * 64,
                    "row_count": 5,
                    "column_count": 2,
                }
            ]
        }
        self.analysis_key, self.configuration_sha256 = quality_analysis_key(
            self.source_sha256,
            self.normalization_result,
        )

    def report(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        return build_contract_report(
            self.source_sha256,
            self.normalization_result,
            self.analysis_key,
            self.configuration_sha256,
            now,
            now + timedelta(seconds=1),
        )

    def validate(self, report):
        return validate_quality_report(
            report,
            expected_summary_totals=normalized_summary_totals(
                self.normalization_result
            ),
        )

    def test_contract_report_is_partial_until_detection_rules_are_available(self):
        report = self.report()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["completion_status"], "PartiallyCompleted")
        self.assertEqual(report["coverage"]["checks"][0]["status"], "Skipped")
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["scores"], [])

    def test_optional_prefilter_skip_does_not_make_report_partial(self):
        report = self.report()
        report["completion_status"] = "Completed"
        report["coverage"]["status"] = "Completed"
        report["coverage"]["checks"][0].update(
            status="Completed",
            reason=None,
        )
        report["coverage"]["checks"].append(
            {
                "check_id": "optional.prefilter",
                "status": "Skipped",
                "required": False,
                "scope": None,
                "rows_considered": 0,
                "budget": None,
                "reason": "The documented optional prefilter did not match.",
            }
        )
        self.assertEqual(
            self.validate(report)["completion_status"],
            "Completed",
        )

    def test_empty_coverage_cannot_validate_as_completed(self):
        report = self.report()
        report["coverage"]["checks"] = []
        report["coverage"]["status"] = "Completed"
        report["completion_status"] = "Completed"
        with self.assertRaises(QualityAnalysisError):
            self.validate(report)

    def test_missing_required_check_is_rejected(self):
        report = self.report()
        report["coverage"]["checks"] = [
            {
                "check_id": "optional.prefilter",
                "status": "Skipped",
                "required": False,
                "scope": None,
                "rows_considered": 0,
                "budget": None,
                "reason": "The optional prefilter did not match.",
            }
        ]
        with self.assertRaises(QualityAnalysisError):
            self.validate(report)

    def test_inconsistent_completion_status_is_rejected(self):
        report = self.report()
        report["completion_status"] = "Completed"
        with self.assertRaises(QualityAnalysisError):
            self.validate(report)

    def test_analysis_key_does_not_depend_on_report_timestamps(self):
        first = self.report()
        second = build_contract_report(
            self.source_sha256,
            self.normalization_result,
            self.analysis_key,
            self.configuration_sha256,
            datetime(2026, 10, 11, tzinfo=timezone.utc),
            datetime(2026, 10, 11, 0, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            first["analysis"]["analysis_key"],
            second["analysis"]["analysis_key"],
        )
        self.assertNotEqual(
            first["analysis"]["started_at"],
            second["analysis"]["started_at"],
        )

    def test_summary_totals_are_checked_against_normalized_metadata(self):
        report = self.report()
        for field in ("tables_available", "rows_available", "columns_available"):
            altered = copy.deepcopy(report)
            altered["summary"][field] += 1
            with self.subTest(field=field), self.assertRaises(QualityAnalysisError):
                self.validate(altered)

    def test_summary_totals_support_legitimate_multi_table_output(self):
        multi_table_result = {
            "row_count": 7,
            "column_count": 5,
            "tables": [
                {
                    "table_index": 0,
                    "sha256": "b" * 64,
                    "row_count": 5,
                    "column_count": 2,
                },
                {
                    "table_index": 1,
                    "sha256": "c" * 64,
                    "row_count": 2,
                    "column_count": 3,
                },
            ],
        }
        analysis_key, configuration_sha256 = quality_analysis_key(
            self.source_sha256,
            multi_table_result,
        )
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        report = build_contract_report(
            self.source_sha256,
            multi_table_result,
            analysis_key,
            configuration_sha256,
            now,
            now + timedelta(seconds=1),
        )
        self.assertEqual(
            {
                key: report["summary"][key]
                for key in (
                    "tables_available",
                    "rows_available",
                    "columns_available",
                )
            },
            {
                "tables_available": 2,
                "rows_available": 7,
                "columns_available": 5,
            },
        )
        self.assertEqual(
            validate_quality_report(
                report,
                expected_summary_totals=normalized_summary_totals(
                    multi_table_result
                ),
            ),
            report,
        )

    def test_report_rejects_invalid_version_provenance_and_coverage(self):
        for mutation in (
            lambda report: report.update(schema_version=2),
            lambda report: report["analysis"].update(source_sha256="invalid"),
            lambda report: report["coverage"]["checks"][0].update(status="Unknown"),
            lambda report: report["summary"].update(findings_total=1),
        ):
            with self.subTest(mutation=mutation):
                invalid = copy.deepcopy(self.report())
                mutation(invalid)
                with self.assertRaises(QualityAnalysisError):
                    self.validate(invalid)

    def test_report_rejects_nan_infinity_and_oversized_findings(self):
        invalid = self.report()
        invalid["analysis"]["started_at"] = "2026-10-10T00:00:00+00:00"
        invalid["summary"]["rows_available"] = float("nan")
        with self.assertRaises(QualityAnalysisError):
            self.validate(invalid)

        invalid = self.report()
        invalid["findings"] = [{}] * (MAX_QUALITY_FINDINGS + 1)
        with self.assertRaises(QualityAnalysisError):
            self.validate(invalid)

    def test_finding_contract_validates_id_classification_confidence_and_denominator(self):
        report = self.report()
        report["completion_status"] = "Completed"
        report["coverage"]["status"] = "Completed"
        report["coverage"]["checks"][0].update(
            status="Completed",
            reason=None,
        )
        scope = {
            "table_index": 0,
            "table_name": None,
            "column_position": 0,
            "column_name": "value",
        }
        finding = {
            "finding_id": finding_id_for(
                self.analysis_key,
                "fixture.contract",
                "1",
                scope,
            ),
            "rule_id": "fixture.contract",
            "rule_version": "1",
            "classification": "unusual_candidate",
            "title": "Fixture candidate",
            "description": "Used to verify the quality-finding schema.",
            "scope": scope,
            "evidence": {
                "observed": {"count": 1},
                "example_refs": [{"row_index": 4}],
            },
            "denominator": {"basis": "rows", "count": 5},
            "severity": "low",
            "confidence": 0.5,
            "confidence_basis": "The deterministic fixture is synthetic.",
            "recommended_investigation": "Inspect the row without changing source data.",
        }
        report["findings"] = [finding]
        report["summary"]["findings_total"] = 1
        report["summary"]["findings_by_classification"]["unusual_candidate"] = 1
        report["summary"]["findings_by_severity"]["low"] = 1
        self.assertEqual(self.validate(report)["findings"], [finding])

        invalid = copy.deepcopy(report)
        invalid["findings"][0]["evidence"]["observed"] = {
            "oversized": "x" * MAX_QUALITY_REPORT_BYTES
        }
        with self.assertRaises(QualityAnalysisError):
            self.validate(invalid)

        invalid = copy.deepcopy(report)
        invalid["findings"][0]["evidence"]["example_refs"] = [
            {"row_index": index} for index in range(6)
        ]
        with self.assertRaises(QualityAnalysisError):
            self.validate(invalid)

        for key, value in (
            ("finding_id", "unstable"),
            ("classification", "confirmed"),
            ("confidence", float("inf")),
            ("denominator", {"basis": "rows", "count": None}),
        ):
            invalid = copy.deepcopy(report)
            invalid["findings"][0][key] = value
            with self.subTest(key=key), self.assertRaises(QualityAnalysisError):
                self.validate(invalid)

    def test_all_finding_classifications_are_supported(self):
        report = self.report()
        report["completion_status"] = "Completed"
        report["coverage"]["status"] = "Completed"
        report["coverage"]["checks"][0].update(
            status="Completed",
            reason=None,
        )
        for index, classification in enumerate(
            (
                "confirmed_violation",
                "suspected_issue",
                "unusual_candidate",
            )
        ):
            scope = {
                "table_index": 0,
                "table_name": None,
                "column_position": index,
                "column_name": f"column-{index}",
            }
            report["findings"].append(
                {
                    "finding_id": finding_id_for(
                        self.analysis_key,
                        f"fixture.{classification}",
                        "1",
                        scope,
                    ),
                    "rule_id": f"fixture.{classification}",
                    "rule_version": "1",
                    "classification": classification,
                    "title": "Contract fixture",
                    "description": "Verifies an accepted finding classification.",
                    "scope": scope,
                    "evidence": {
                        "observed": {"count": 1},
                        "example_refs": [],
                    },
                    "denominator": {"basis": "rows", "count": 5},
                    "severity": "info",
                    "confidence": 0.5,
                    "confidence_basis": "Synthetic contract test.",
                    "recommended_investigation": "Review without modifying source data.",
                }
            )
            report["summary"]["findings_by_classification"][classification] = 1
            report["summary"]["findings_by_severity"]["info"] += 1
        report["summary"]["findings_total"] = 3
        self.assertEqual(len(self.validate(report)["findings"]), 3)


if __name__ == "__main__":
    unittest.main()
