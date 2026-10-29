import unittest
import json

from app.agent.claim_validation import validate_claim_envelope
from app.agent.inspection_handoff import build_inspection_handoff
from app.agent.contracts import (
    ModelResponse,
    ProviderMessage,
    ToolCall,
    ToolResult,
)
from app.agent.config import AgentSettings
from app.agent.runtime import execute_agent_request
from app.agent.tools import InspectionArguments, ToolDefinition, ToolRegistry
from tests.test_agent_inspection_handoff import inspection_inputs, schema_record


class ClaimValidationTests(unittest.TestCase):
    def setUp(self):
        file_record, profile, quality, operations = inspection_inputs()
        self.handoff = build_inspection_handoff(
            file_record=file_record,
            profile_response=profile,
            quality_response=quality,
            schema_analysis=schema_record(),
            preprocessing_operations=operations,
        )
        self.tool_results = [
            {
                "tool": "get_inspection_handoff",
                "status": "succeeded",
                "data": self.handoff,
            }
        ]
        self.context = {"dataset_id": 7, "project_id": 2}

    def claim(
        self,
        claim_type="count",
        claim_key="table[0].row_count",
        value=3,
        *,
        unit="rows",
        denominator=None,
        handoff_id=None,
        dataset_id=7,
        file_id=11,
    ):
        return {
            "claim_type": claim_type,
            "dataset_id": dataset_id,
            "file_id": file_id,
            "claim_key": claim_key,
            "value": value,
            "unit": unit,
            "denominator": denominator,
            "evidence_reference": {
                "handoff_id": handoff_id or self.handoff["handoff_id"],
                "dataset_id": dataset_id,
                "file_id": file_id,
            },
        }

    def validate(self, claims, *, results=None, context=None):
        import json

        return validate_claim_envelope(
            json.dumps({"claims": claims}),
            structured_results=results if results is not None else self.tool_results,
            context=context if context is not None else self.context,
        )

    def handoff_with_reference_status(self, kind, status):
        import hashlib

        from app.agent.claim_validation import _handoff_content

        handoff = dict(self.handoff)
        handoff["evidence_references"] = [
            dict(reference)
            for reference in self.handoff["evidence_references"]
        ]
        reference = next(
            item for item in handoff["evidence_references"]
            if item["kind"] == kind
        )
        reference["verification_status"] = status
        handoff["handoff_id"] = hashlib.sha256(
            json.dumps(
                _handoff_content(handoff),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return handoff

    def test_valid_count_resolves_from_current_registered_handoff(self):
        answer, decisions, supported = self.validate([self.claim()])
        self.assertTrue(supported)
        self.assertEqual(decisions[0]["outcome"], "SUPPORTED")
        self.assertIn("has 3 rows", answer)

    def test_fabricated_value_is_contradicted_and_not_rendered_as_fact(self):
        answer, decisions, supported = self.validate(
            [self.claim(value=300)]
        )
        self.assertFalse(supported)
        self.assertEqual(decisions[0]["outcome"], "CONTRADICTED")
        self.assertNotIn("300", answer)

    def test_nonexistent_reference_and_measurement_are_unresolved(self):
        _answer, decisions, supported = self.validate(
            [
                self.claim(handoff_id="f" * 64),
                self.claim(claim_key="table[0].invented_metric"),
            ]
        )
        self.assertFalse(supported)
        self.assertEqual(
            [item["reason_code"] for item in decisions],
            ["evidence_reference_not_resolved", "measurement_not_found"],
        )

    def test_model_claim_without_current_turn_execution_evidence_is_unresolved(self):
        _answer, decisions, supported = self.validate(
            [self.claim()],
            results=[],
        )
        self.assertFalse(supported)
        self.assertEqual(
            decisions[0]["reason_code"],
            "evidence_reference_not_resolved",
        )

    def test_mismatched_scope_is_not_resolved(self):
        _answer, decisions, supported = self.validate(
            [self.claim(dataset_id=8, file_id=11)]
        )
        self.assertFalse(supported)
        self.assertEqual(decisions[0]["reason_code"], "evidence_scope_mismatch")

    def test_column_existence_and_type_require_exact_profile_values(self):
        claims = [
            self.claim(
                "column_exists",
                "column[0,0].name",
                True,
                unit=None,
            ),
            self.claim(
                "schema_type",
                "column[0,0].physical_types",
                "integer",
                unit=None,
            ),
        ]
        answer, decisions, supported = self.validate(claims)
        self.assertTrue(supported)
        self.assertEqual(
            [item["outcome"] for item in decisions],
            ["SUPPORTED", "SUPPORTED"],
        )
        self.assertIn("Column account_id exists", answer)
        self.assertIn("observed physical types: integer", answer)

    def test_mismatched_unit_and_denominator_are_rejected(self):
        answer, decisions, supported = self.validate(
            [
                self.claim(
                    "count",
                    "column[0,1].missing_value_count",
                    1,
                    unit="rows",
                    denominator={"unit": "rows", "count": 3},
                ),
                self.claim(
                    "count",
                    "column[0,1].missing_value_count",
                    1,
                    unit="values",
                    denominator={"unit": "rows", "count": 300},
                ),
            ]
        )
        self.assertFalse(supported)
        self.assertEqual(
            [item["reason_code"] for item in decisions],
            ["unit_mismatch", "denominator_mismatch"],
        )
        self.assertNotIn("has 1 missing values", answer)

    def test_quality_claim_requires_current_verified_finding(self):
        claim = self.claim(
            "quality",
            "quality_findings[finding-1]",
            "finding-1",
            unit=None,
        )
        answer, decisions, supported = self.validate([claim])
        self.assertTrue(supported)
        self.assertIn("Missing value candidates", answer)

        stale_handoff = dict(self.handoff)
        stale_handoff["evidence_references"] = [
            dict(reference)
            for reference in self.handoff["evidence_references"]
        ]
        quality_reference = next(
            reference
            for reference in stale_handoff["evidence_references"]
            if reference["kind"] == "quality_report"
        )
        quality_reference["verification_status"] = "UNKNOWN"
        from app.agent.claim_validation import _handoff_content
        import hashlib
        import json

        stale_handoff["handoff_id"] = hashlib.sha256(
            json.dumps(
                _handoff_content(stale_handoff),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        _answer, decisions, supported = self.validate(
            [
                self.claim(
                    "quality",
                    "quality_findings[finding-1]",
                    "finding-1",
                    unit=None,
                    handoff_id=stale_handoff["handoff_id"],
                )
            ],
            results=[
                {
                    "tool": "get_inspection_handoff",
                    "status": "succeeded",
                    "data": stale_handoff,
                }
            ],
        )
        self.assertFalse(supported)
        self.assertEqual(
            decisions[0]["reason_code"], "evidence_not_verified"
        )

    def test_unverified_or_failed_profile_status_never_supports_claims(self):
        for status in (
            "UNKNOWN",
            "FAILED",
            "OBSERVED",
            "COMPUTED",
            "INFERRED",
            "ASSUMED",
            "unverifiable",
        ):
            with self.subTest(status=status):
                handoff = self.handoff_with_reference_status("profile", status)
                _answer, decisions, supported = self.validate(
                    [self.claim(handoff_id=handoff["handoff_id"])],
                    results=[
                        {
                            "tool": "get_inspection_handoff",
                            "status": "succeeded",
                            "data": handoff,
                        }
                    ],
                )
                self.assertFalse(supported)
                self.assertEqual(
                    decisions[0]["outcome"],
                    "FAILED" if status == "FAILED" else "UNRESOLVED",
                )

    def test_failed_quality_verification_is_not_supported(self):
        handoff = self.handoff_with_reference_status("quality_report", "failed")
        _answer, decisions, supported = self.validate(
            [
                self.claim(
                    "quality",
                    "quality_findings[finding-1]",
                    "finding-1",
                    unit=None,
                    handoff_id=handoff["handoff_id"],
                )
            ],
            results=[
                {
                    "tool": "get_inspection_handoff",
                    "status": "succeeded",
                    "data": handoff,
                }
            ],
        )
        self.assertFalse(supported)
        self.assertEqual(decisions[0]["outcome"], "FAILED")

    def test_relationship_is_only_supported_as_a_qualified_hypothesis(self):
        answer, decisions, supported = self.validate(
            [
                self.claim(
                    "relationship",
                    "relationships[0].status",
                    "inferred",
                    unit=None,
                )
            ]
        )
        self.assertTrue(supported)
        self.assertEqual(
            decisions[0]["outcome"],
            "SUPPORTED_WITH_QUALIFICATION",
        )
        self.assertIn("not a confirmed join", answer)

        _answer, decisions, supported = self.validate(
            [
                self.claim(
                    "relationship",
                    "relationships[0].status",
                    "confirmed",
                    unit=None,
                )
            ]
        )
        self.assertFalse(supported)
        self.assertEqual(decisions[0]["outcome"], "CONTRADICTED")

    def test_causal_prediction_forecast_and_preprocessing_claims_fail_closed(self):
        claims = [
            self.claim("causal", "causes.churn", True, unit=None),
            self.claim("forecast", "forecast.revenue", 10, unit="USD"),
            self.claim("prediction", "prediction.churn", True, unit=None),
            self.claim("preprocessing", "execution[5]", "completed", unit=None),
        ]
        answer, decisions, supported = self.validate(claims)
        self.assertFalse(supported)
        self.assertEqual(
            [item["outcome"] for item in decisions],
            ["UNSUPPORTED"] * len(claims),
        )
        self.assertIn("not supported", answer)

    def test_recommendation_does_not_claim_execution(self):
        claim = self.claim(
            "recommendation",
            "recommendations[da.missing_value_handling]",
            "recommended",
            unit=None,
        )
        answer, decisions, supported = self.validate([claim])
        self.assertTrue(supported)
        self.assertIn("has not been run", answer)

        _answer, decisions, supported = self.validate(
            [
                self.claim(
                    "recommendation",
                    "recommendations[da.missing_value_handling]",
                    "executed",
                    unit=None,
                )
            ]
        )
        self.assertFalse(supported)
        self.assertEqual(decisions[0]["outcome"], "CONTRADICTED")

    def test_mixed_supported_and_unsupported_claims_render_safely(self):
        answer, decisions, supported = self.validate(
            [
                self.claim(),
                self.claim("causal", "causes.churn", True, unit=None),
            ]
        )
        self.assertFalse(supported)
        self.assertIn("has 3 rows", answer)
        self.assertIn("not supported", answer)

    def test_malformed_and_oversized_envelopes_fail_closed(self):
        answer, decisions, supported = validate_claim_envelope(
            "The file has 999 rows.",
            structured_results=self.tool_results,
            context=self.context,
        )
        self.assertFalse(supported)
        self.assertEqual(decisions[0]["reason_code"], "claim_envelope_invalid")
        self.assertNotIn("999", answer)

        answer, decisions, supported = validate_claim_envelope(
            " " * (32 * 1024 + 1),
            structured_results=self.tool_results,
            context=self.context,
        )
        self.assertFalse(supported)
        self.assertEqual(decisions[0]["reason_code"], "claim_envelope_too_large")

    def test_runtime_persists_only_the_validated_evidence_based_answer(self):
        handoff = self.handoff
        registry = ToolRegistry(
            (
                ToolDefinition(
                    name="get_inspection_handoff",
                    label="Preparing an inspection handoff",
                    description="Return the current test handoff.",
                    arguments_model=InspectionArguments,
                    execute=lambda _arguments: ToolResult(
                        status="succeeded",
                        summary="Inspection completed.",
                        data=handoff,
                    ),
                ),
            )
        )
        final_response = json.dumps(
            {
                "claims": [
                    {
                        "claim_type": "count",
                        "dataset_id": 7,
                        "file_id": 11,
                        "claim_key": "table[0].row_count",
                        "value": 3,
                        "unit": "rows",
                        "denominator": None,
                        "evidence_reference": {
                            "handoff_id": handoff["handoff_id"],
                            "dataset_id": 7,
                            "file_id": 11,
                        },
                    }
                ]
            }
        )

        class Provider:
            def __init__(self):
                self.responses = iter(
                    (
                        ModelResponse(
                            ProviderMessage(
                                role="assistant",
                                content="",
                                tool_calls=(
                                    ToolCall(
                                        "handoff-call",
                                        "get_inspection_handoff",
                                        {"dataset_id": 7, "file_id": 11},
                                    ),
                                ),
                            ),
                            finish_reason="tool_calls",
                        ),
                        ModelResponse(
                            ProviderMessage(
                                role="assistant",
                                content=final_response,
                            ),
                            finish_reason="stop",
                        ),
                    )
                )

            def complete(self, _request):
                return next(self.responses)

        outcome = execute_agent_request(
            user_message="How many rows are in my dataset?",
            history=[],
            context={"dataset_id": 7, "project_id": 2},
            settings=AgentSettings(
                provider="ollama",
                base_url="http://localhost:11434",
                model="qwen3:8b",
                api_key=None,
                timeout_seconds=1,
                max_output_tokens=256,
                max_model_turns=3,
                max_tool_calls=3,
                max_history_messages=10,
            ),
            provider=Provider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertIn("has 3 rows", outcome.assistant_text)
        self.assertEqual(
            outcome.structured_results[-1]["data"]["claims"][0]["outcome"],
            "SUPPORTED",
        )


if __name__ == "__main__":
    unittest.main()
