import unittest
from unittest.mock import patch

from app.agent.inspection_handoff import build_inspection_handoff
from app.agent.tools import ToolRegistry, _profile_summary, _quality_summary
from app.provenance import build_provenance, canonical_sha256


SOURCE_SHA256 = "a" * 64
SCHEMA_KEY = "b" * 64


def schema_record(*, stale=False, invalid_column=False, invalid_relationship=False):
    columns = [
        {
            "name": "account_id",
            "position": 0,
            "candidate_key_status": "candidate",
            "identifier_like": True,
        },
        {
            "name": "balance",
            "position": 1,
            "candidate_key_status": "not_candidate",
            "identifier_like": False,
        },
    ]
    if invalid_column:
        columns[0]["name"] = "fabricated_id"
    source_identity = {
        "dataset_id": 7,
        "file_id": 11,
        "filename": "accounts.csv",
        "table_index": 0,
        "table_name": "accounts",
    }
    related_identity = {
        "dataset_id": 7,
        "file_id": 12,
        "filename": "transactions.csv",
        "table_index": 0,
        "table_name": "transactions",
    }
    source_columns = columns
    related_columns = [
        {
            "name": "account_id",
            "position": 0,
            "candidate_key_status": "candidate",
            "identifier_like": True,
        },
    ]
    result = {
        "schema": {
            "tables": [
                {
                    "identity": source_identity,
                    "columns": source_columns,
                },
                {
                    "identity": related_identity,
                    "columns": related_columns,
                },
            ]
        },
        "relationships": [
            {
                "source": {
                    "table": source_identity,
                    "column": "balance",
                    "position": 1,
                },
                "target": {
                    "table": related_identity,
                    "column": (
                        "missing_account_id"
                        if invalid_relationship
                        else "account_id"
                    ),
                    "position": 0,
                },
                "status": "inferred",
                "cardinality_hypothesis": "many_to_one",
                "evidence": {
                    "non_null_overlap_count": 2,
                    "source_referential_coverage": 1.0,
                    "target_candidate_key": True,
                },
                "risks": ["Business meaning is unconfirmed."],
            }
        ],
        "warnings": ["distinct_tracking_limit"],
        "limitations": ["Business semantics remain unconfirmed."],
    }
    provenance = build_provenance(
        operation_type="schema_architect",
        operation_version="1",
        configuration_sha256="c" * 64,
        status="completed",
        inputs=[{"kind": "source", "sha256": SOURCE_SHA256}],
        outputs=[
            {
                "kind": "schema_analysis",
                "analysis_key": SCHEMA_KEY,
                "result_sha256": canonical_sha256(result),
            }
        ],
        verification_status="verified",
        checks=[{"name": "source_manifest_stability", "status": "verified"}],
        completed_at="2026-05-01T00:00:00+00:00",
    )
    return {
        "id": 31,
        "dataset_id": 7,
        "status": "Stale" if stale else "Completed",
        "stale": stale,
        "analysis_key": SCHEMA_KEY,
        "configuration": {
            "files": [
                {"file_id": 11, "table_indices": None},
                {"file_id": 12, "table_indices": None},
            ]
        },
        "result": result,
        "provenance": provenance,
    }


def inspection_inputs():
    file_record = {
        "id": 11,
        "dataset_id": 7,
        "filename": "accounts.csv",
        "checksum": SOURCE_SHA256,
        "status": "Ready",
        "updated_at": "2026-05-01T00:00:00",
    }
    profile_response = {
        "dataset_id": 7,
        "file_id": 11,
        "profile_result": {
            "version": 1,
            "warnings": ["Distinct tracking reached its configured bound."],
            "unsupported_analyses": ["Exact median unavailable."],
            "tables": [
                {
                    "name": "accounts",
                    "row_count": 3,
                    "column_count": 2,
                    "duplicate_row_count": 1,
                    "duplicate_row_count_exact": True,
                    "columns": [
                        {
                            "name": "account_id",
                            "position": 0,
                            "physical_types": ["integer"],
                            "missing_value_count": 0,
                            "empty_value_count": 0,
                            "distinct_value_count": 3,
                            "distinct_count_exact": True,
                        },
                        {
                            "name": "balance",
                            "position": 1,
                            "physical_types": ["number"],
                            "missing_value_count": 1,
                            "empty_value_count": 0,
                            "distinct_value_count": 2,
                            "distinct_count_exact": True,
                        },
                    ],
                }
            ],
        },
    }
    quality_response = {
        "dataset_id": 7,
        "file_id": 11,
        "status": "Completed",
        "report_is_current": True,
        "quality_report": {
            "analysis": {
                "analysis_key": "d" * 64,
                "source_sha256": SOURCE_SHA256,
            },
            "provenance": build_provenance(
                operation_type="quality_analysis",
                operation_version="1",
                configuration_sha256="e" * 64,
                status="completed",
                inputs=[{"kind": "source", "sha256": SOURCE_SHA256}],
                outputs=[{"kind": "quality_report", "analysis_key": "d" * 64}],
                verification_status="verified",
                checks=[{"name": "source_checksum", "status": "verified"}],
                completed_at="2026-05-01T00:00:00+00:00",
            ),
            "findings": [
                {
                    "finding_id": "finding-1",
                    "rule_id": "generic.missing_value_candidates",
                    "classification": "unusual_candidate",
                    "title": "Missing value candidates",
                    "scope": {
                        "table_index": 0,
                        "table_name": "accounts",
                        "column_position": 1,
                        "column_name": "balance",
                    },
                    "evidence": {
                        "observed": {
                            "missing_value_count": 1,
                            "empty_value_count": 0,
                        },
                        "example_refs": [],
                    },
                    "denominator": {"basis": "rows", "count": 3},
                    "confidence": 1.0,
                }
            ],
        },
    }
    preprocessing_operations = [
        {
            "operation_id": "da.missing_value_handling",
            "status": "implemented",
            "changes_rows": True,
            "changes_columns": True,
            "changes_values": True,
            "changes_metadata": False,
            "required_approvals": ["approved_missing_value_strategy"],
        }
    ]
    return file_record, profile_response, quality_response, preprocessing_operations


class InspectionHandoffTests(unittest.TestCase):
    def build(self, **overrides):
        file_record, profile, quality, operations = inspection_inputs()
        values = {
            "file_record": file_record,
            "profile_response": profile,
            "quality_response": quality,
            "schema_analysis": schema_record(),
            "preprocessing_operations": operations,
        }
        values.update(overrides)
        return build_inspection_handoff(**values)

    def test_handoff_uses_persisted_profile_quality_and_schema_results(self):
        handoff = self.build()
        table = handoff["tables"][0]
        self.assertEqual(handoff["status"], "ready")
        self.assertIsNone(handoff["inspection_execution_id"])
        self.assertIn(
            "Distinct tracking reached its configured bound.",
            handoff["warnings"],
        )
        self.assertIn("Exact median unavailable.", handoff["limitations"])
        self.assertIn(
            "Business semantics remain unconfirmed.",
            handoff["limitations"],
        )
        self.assertEqual((table["row_count"], table["column_count"]), (3, 2))
        self.assertEqual(table["duplicate_row_count"], 1)
        self.assertEqual(table["columns"][1]["missing_value_count"], 1)
        self.assertEqual(
            handoff["quality_findings"][0]["measurements"]["missing_value_count"],
            1,
        )
        self.assertEqual(handoff["candidate_keys"][0]["column_name"], "account_id")
        self.assertEqual(handoff["candidate_keys"][1]["status"], "not_candidate")
        self.assertEqual(
            handoff["candidate_identifiers"][0]["status"],
            "heuristic_candidate",
        )
        self.assertEqual(handoff["relationships"][0]["status"], "inferred")
        self.assertEqual(
            handoff["relationships"][0]["evidence_classification"],
            "INFERRED",
        )
        self.assertNotEqual(handoff["relationships"][0]["status"], "confirmed")
        self.assertEqual(
            handoff["preprocessing_recommendations"][0]["required_approvals"],
            ["approved_missing_value_strategy"],
        )
        self.assertEqual(
            handoff["preprocessing_recommendations"][0]["affected_columns"][0][
                "column_name"
            ],
            "balance",
        )
        self.assertIn(
            "May change the number of rows.",
            handoff["preprocessing_recommendations"][0]["expected_consequences"],
        )
        self.assertTrue(
            all(
                reference["jev_resolution"] == "unresolved"
                for reference in handoff["evidence_references"]
            )
        )

    def test_missing_quality_and_schema_results_remain_explicitly_unresolved(self):
        _file, profile, _quality, _operations = inspection_inputs()
        unavailable_quality = {
            "dataset_id": 7,
            "file_id": 11,
            "status": "NotStarted",
            "report_is_current": False,
            "quality_report": None,
        }
        handoff = build_inspection_handoff(
            file_record=inspection_inputs()[0],
            profile_response=profile,
            quality_response=unavailable_quality,
        )
        self.assertEqual(handoff["status"], "partial")
        self.assertEqual(handoff["quality_findings"], [])
        self.assertEqual(handoff["candidate_keys"], [])
        self.assertEqual(handoff["relationships"], [])
        self.assertTrue(handoff["unresolved"])

    def test_fabricated_schema_column_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "absent from the profile"):
            self.build(schema_analysis=schema_record(invalid_column=True))

    def test_fabricated_relationship_column_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonexistent relationship column"):
            self.build(schema_analysis=schema_record(invalid_relationship=True))

    def test_fabricated_quality_column_reference_is_rejected(self):
        _file, _profile, quality, _operations = inspection_inputs()
        quality["quality_report"]["findings"][0]["scope"]["column_name"] = "invented"
        with self.assertRaisesRegex(ValueError, "does not match the profile"):
            self.build(quality_response=quality)

    def test_schema_result_mismatch_with_its_provenance_stays_unverified(self):
        schema = schema_record()
        schema["result"]["relationships"][0]["status"] = "confirmed"
        handoff = self.build(schema_analysis=schema)
        self.assertEqual(handoff["status"], "partial")
        self.assertEqual(handoff["candidate_keys"], [])
        self.assertEqual(handoff["relationships"], [])
        self.assertEqual(
            handoff["evidence_references"][-1]["resolution"],
            "unavailable",
        )

    def test_quality_report_for_an_old_source_version_is_not_consumed(self):
        _file, profile, quality, _operations = inspection_inputs()
        quality["quality_report"]["analysis"]["source_sha256"] = "f" * 64
        handoff = build_inspection_handoff(
            file_record=inspection_inputs()[0],
            profile_response=profile,
            quality_response=quality,
            schema_analysis=schema_record(),
        )
        self.assertEqual(handoff["status"], "partial")
        self.assertEqual(handoff["quality_findings"], [])
        self.assertIn(
            "A current completed quality report is unavailable.",
            handoff["unresolved"],
        )

    def test_all_provenance_evidence_and_legacy_statuses_stay_distinct(self):
        file_record, profile, quality, operations = inspection_inputs()
        for status in (
            "OBSERVED",
            "COMPUTED",
            "INFERRED",
            "ASSUMED",
            "UNKNOWN",
            "FAILED",
            "verified",
            "failed",
            "stale",
            "unverifiable",
        ):
            with self.subTest(status=status):
                report = quality["quality_report"]
                report["provenance"] = build_provenance(
                    operation_type="quality_analysis",
                    operation_version="1",
                    configuration_sha256="e" * 64,
                    status="completed",
                    inputs=[{"kind": "source", "sha256": SOURCE_SHA256}],
                    outputs=[{"kind": "quality_report", "analysis_key": "d" * 64}],
                    verification_status=status,
                    checks=[{"name": "source_checksum", "status": status}],
                    completed_at="2026-05-01T00:00:00+00:00",
                )
                handoff = build_inspection_handoff(
                    file_record=file_record,
                    profile_response=profile,
                    quality_response=quality,
                    schema_analysis=schema_record(),
                    preprocessing_operations=operations,
                )
                self.assertEqual(
                    handoff["evidence_references"][1]["verification_status"],
                    status,
                )
                self.assertEqual(
                    handoff["status"],
                    "ready" if status == "verified" else "partial",
                )

    def test_stale_schema_analysis_is_not_used_as_evidence(self):
        handoff = self.build(schema_analysis=schema_record(stale=True))
        self.assertEqual(handoff["status"], "partial")
        self.assertEqual(handoff["candidate_keys"], [])
        self.assertEqual(handoff["relationships"], [])
        self.assertIn(
            "The stored schema analysis is stale, incomplete, or unverified.",
            handoff["unresolved"],
        )

    def test_failed_source_does_not_produce_a_handoff(self):
        file_record, profile, quality, _operations = inspection_inputs()
        file_record["status"] = "Failed"
        with self.assertRaisesRegex(ValueError, "ready source file"):
            build_inspection_handoff(
                file_record=file_record,
                profile_response=profile,
                quality_response=quality,
            )

    def test_agent_summaries_preserve_the_actual_profile_and_quality_fields(self):
        _file_record, profile, quality, _operations = inspection_inputs()
        profile_summary = _profile_summary(profile)
        first_column = profile_summary["profile"]["tables"][0]["columns"][0]
        self.assertEqual(profile_summary["profile"]["version"], 1)
        self.assertEqual(first_column["physical_types"], ["integer"])
        self.assertEqual(first_column["missing_value_count"], 0)
        self.assertEqual(
            profile_summary["profile"]["tables"][0]["duplicate_row_count"],
            1,
        )

        quality_summary = _quality_summary(quality)
        self.assertEqual(
            quality_summary["findings"][0]["evidence"]["observed"][
                "missing_value_count"
            ],
            1,
        )
        self.assertEqual(
            quality_summary["findings"][0]["denominator"],
            {"basis": "rows", "count": 3},
        )

    def test_registered_tool_builds_the_handoff_from_existing_api_results(self):
        file_record, profile, quality, _operations = inspection_inputs()

        class FakeMain:
            get_dataset_file = staticmethod(lambda *_args: file_record)
            get_dataset_file_profile = staticmethod(lambda *_args: profile)
            get_dataset_file_quality_analysis = staticmethod(lambda *_args: quality)
            list_dataset_schema_analyses = staticmethod(
                lambda *_args, **_kwargs: {
                    "items": [
                        {
                            "id": 31,
                            "status": "Completed",
                            "stale": False,
                        }
                    ]
                }
            )
            get_dataset_schema_analysis = staticmethod(
                lambda *_args: schema_record()
            )
            list_preprocessing_operations = staticmethod(
                lambda: {
                    "operations": inspection_inputs()[3],
                    "total": 1,
                }
            )

        with patch("app.agent.tools._main", return_value=FakeMain):
            _output, result, _label = ToolRegistry().execute(
                "get_inspection_handoff",
                {"dataset_id": 7, "file_id": 11},
            )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data["schema_version"], 1)
        self.assertEqual(result.data["status"], "ready")


if __name__ == "__main__":
    unittest.main()
