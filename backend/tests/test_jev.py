import unittest
from decimal import Decimal

from app.execution import ExecutionBudget
from app.jev import (
    VerificationEvidence,
    approved_correction_policy,
    evidence_for,
    policy_for,
    verify_claim,
)


class JEVPolicyTests(unittest.TestCase):
    def evidence(self, primary, expected, **overrides):
        arguments = {
            "reference": "fixture:independent-result",
            "evidence_kind": "deterministic_recomputation",
            "independent": True,
            "authoritative": True,
            "status": "verified",
        }
        arguments.update(overrides)
        return evidence_for(primary, expected, **arguments)

    def test_exact_match_retains_primary_value(self):
        evidence = self.evidence(3, 3)
        result = verify_claim(3, evidence, policy_for("count"))
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.decision, "agreed")
        self.assertEqual(result.selected_value, 3)
        self.assertIsNone(result.correction_metadata)

    def test_floating_difference_within_type_tolerance_retains_primary(self):
        primary = 1.0
        evidence = self.evidence(primary, 1.0 + 5e-10)
        result = verify_claim(
            primary,
            evidence,
            policy_for("floating_point"),
        )
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.selected_value, primary)

    def test_approved_correction_requires_authority_and_output_validation(self):
        primary = Decimal("10.00")
        expected = Decimal("10.01")
        policy = approved_correction_policy(
            policy_id="approved-financial-policy-v1",
            value_type="financial",
            currency="USD",
            precision=2,
            absolute_tolerance=Decimal("0.001"),
            relative_tolerance=Decimal(0),
            correction_boundary=Decimal("0.02"),
            output_contract_id="invoice-amount-decimal-v1",
            rounding_mode="ROUND_HALF_EVEN",
        )
        evidence = self.evidence(
            primary,
            expected,
            currency="USD",
            correction_output_validated=True,
            correction_output_contract_id="invoice-amount-decimal-v1",
        )
        result = verify_claim(primary, evidence, policy)
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.decision, "correction_applied")
        self.assertEqual(result.selected_value, expected)
        self.assertEqual(
            result.correction_metadata,
            {
                "policy_id": "approved-financial-policy-v1",
                "policy_version": "1",
                "reason_code": "approved_correction_boundary",
                "evidence_reference": "fixture:independent-result",
                "original_value": primary,
                "selected_value": expected,
            },
        )
        audit_record = str(result.audit_record())
        self.assertNotIn("10.00", audit_record)
        self.assertNotIn("10.01", audit_record)

    def test_discrepancy_without_authoritative_evidence_is_unverifiable(self):
        primary = 10.0
        evidence = self.evidence(
            primary,
            10.1,
            authoritative=False,
        )
        result = verify_claim(
            primary,
            evidence,
            policy_for("floating_point"),
        )
        self.assertEqual(result.status, "unverifiable")
        self.assertEqual(result.decision, "skipped")
        self.assertEqual(result.selected_value, primary)
        self.assertIsNone(result.correction_metadata)

    def test_large_discrepancy_preserves_primary_without_claiming_verified(self):
        primary = 10.0
        policy = approved_correction_policy(
            policy_id="bounded-float-correction-v1",
            value_type="floating_point",
            absolute_tolerance=Decimal("0.001"),
            relative_tolerance=Decimal(0),
            correction_boundary=Decimal("0.1"),
            output_contract_id="bounded-float-v1",
        )
        result = verify_claim(
            primary,
            self.evidence(
                primary,
                20.0,
                correction_output_validated=True,
                correction_output_contract_id="bounded-float-v1",
            ),
            policy,
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.decision, "discrepancy_preserved")
        self.assertEqual(result.selected_value, primary)

    def test_timeout_and_verifier_exception_evidence_preserve_primary(self):
        primary = 4.5
        for status, code in (
            ("unverifiable", "verifier_timeout"),
            ("failed", "verifier_error"),
        ):
            with self.subTest(status=status):
                evidence = self.evidence(
                    primary,
                    4.5,
                    status=status,
                    limitation_code=code,
                )
                result = verify_claim(
                    primary,
                    evidence,
                    policy_for("floating_point"),
                )
                self.assertEqual(result.status, status)
                self.assertEqual(result.reason_code, code)
                self.assertEqual(result.selected_value, primary)

    def test_budget_exhaustion_preserves_primary(self):
        primary = 9
        result = verify_claim(
            primary,
            self.evidence(primary, 9),
            policy_for("count"),
            budget=ExecutionBudget({"jev_checks": 0, "jev_evidence_bytes": 100}),
        )
        self.assertEqual(result.status, "unverifiable")
        self.assertEqual(result.reason_code, "budget_exhausted")
        self.assertEqual(result.selected_value, primary)

    def test_malformed_or_stale_evidence_cannot_correct(self):
        primary = 2.0
        policy = approved_correction_policy(
            policy_id="approved-float-v1",
            value_type="floating_point",
            absolute_tolerance=Decimal("0.001"),
            relative_tolerance=Decimal(0),
            correction_boundary=Decimal("1"),
            output_contract_id="float-result-v1",
        )
        malformed = VerificationEvidence(
            status="verified",
            expected_value=3.0,
            reference="fixture:independent-result",
            independent=True,
            authoritative=True,
            claim_sha256="not-a-sha",
            correction_output_validated=True,
            correction_output_contract_id="float-result-v1",
        )
        result = verify_claim(primary, malformed, policy)
        self.assertEqual(result.status, "stale")
        self.assertEqual(result.selected_value, primary)
        oversized = self.evidence(primary, ["x" * 5000])
        self.assertEqual(
            verify_claim(primary, oversized, policy).reason_code,
            "malformed_evidence",
        )

    def test_counts_identifiers_booleans_and_categories_are_exact_and_typed(self):
        cases = (
            ("count", 4, 4, "verified"),
            ("count", 4, 5, "failed"),
            ("identifier", "001", "001", "verified"),
            ("identifier", "001", "1", "failed"),
            ("boolean", True, 1, "unverifiable"),
            ("categorical", "north", "south", "failed"),
        )
        for value_type, primary, expected, status in cases:
            with self.subTest(value_type=value_type, primary=primary, expected=expected):
                result = verify_claim(
                    primary,
                    self.evidence(primary, expected),
                    policy_for(value_type),
                )
                self.assertEqual(result.status, status)
                if status != "verified":
                    self.assertEqual(result.selected_value, primary)

    def test_financial_policy_requires_currency_precision_and_explicit_tolerance(self):
        with self.assertRaises(ValueError):
            policy_for("financial")
        policy = policy_for(
            "financial",
            currency="USD",
            precision=2,
            rounding_mode="ROUND_HALF_EVEN",
        )
        primary = Decimal("1.00")
        evidence = self.evidence(
            primary,
            Decimal("1.004"),
            currency="USD",
        )
        self.assertEqual(
            verify_claim(primary, evidence, policy).status,
            "verified",
        )
        wrong_currency = self.evidence(
            primary,
            Decimal("1.00"),
            currency="EUR",
        )
        self.assertEqual(
            verify_claim(primary, wrong_currency, policy).reason_code,
            "financial_currency_mismatch",
        )

    def test_financial_policy_applies_its_explicit_rounding_mode(self):
        primary = Decimal("1.00")
        half_even_policy = policy_for(
            "financial",
            currency="USD",
            precision=2,
            rounding_mode="ROUND_HALF_EVEN",
        )
        half_up_policy = policy_for(
            "financial",
            currency="USD",
            precision=2,
            rounding_mode="ROUND_HALF_UP",
        )
        evidence = self.evidence(
            primary,
            Decimal("1.005"),
            currency="USD",
        )

        self.assertEqual(
            verify_claim(primary, evidence, half_even_policy).status,
            "verified",
        )
        half_up_result = verify_claim(primary, evidence, half_up_policy)
        self.assertEqual(half_up_result.status, "failed")
        self.assertEqual(half_up_result.selected_value, primary)

    def test_extreme_decimal_exponents_are_rejected_before_serialization(self):
        primary = Decimal("1")
        evidence = self.evidence(
            primary,
            Decimal("1E+999999999"),
        )
        result = verify_claim(primary, evidence, policy_for("statistical_summary"))
        self.assertEqual(result.status, "unverifiable")
        self.assertEqual(result.reason_code, "malformed_evidence")

    def test_aggregate_evidence_size_is_bounded_during_normalization(self):
        primary = {"claim": 1}
        evidence = self.evidence(
            primary,
            {"values": ["x" * 3000 for _ in range(8)]},
        )
        result = verify_claim(
            primary,
            evidence,
            policy_for("schema"),
        )
        self.assertEqual(result.status, "unverifiable")
        self.assertEqual(result.reason_code, "malformed_evidence")

    def test_percentage_values_are_compared_unrounded(self):
        primary = Decimal("12.345678901")
        result = verify_claim(
            primary,
            self.evidence(primary, Decimal("12.345678902")),
            policy_for("percentage"),
        )
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.selected_value, primary)

    def test_schema_values_require_exact_approved_structure(self):
        primary = {"fields": [{"name": "amount", "type": "decimal"}]}
        matching = verify_claim(
            primary,
            self.evidence(primary, {"fields": [{"name": "amount", "type": "decimal"}]}),
            policy_for("schema"),
        )
        changed = verify_claim(
            primary,
            self.evidence(primary, {"fields": [{"name": "amount", "type": "string"}]}),
            policy_for("schema"),
        )
        self.assertEqual(matching.status, "verified")
        self.assertEqual(changed.status, "failed")

    def test_schema_values_distinguish_booleans_from_integers_recursively(self):
        primary = {"required": True, "properties": {"enabled": [True]}}
        expected = {"required": 1, "properties": {"enabled": [1]}}
        result = verify_claim(
            primary,
            self.evidence(primary, expected),
            policy_for("schema"),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.selected_value, primary)

    def test_forecasts_and_semantic_interpretations_are_not_verified_by_agreement(self):
        for value_type in ("forecast", "semantic_interpretation"):
            result = verify_claim(
                0.5,
                self.evidence(
                    0.5,
                    0.5,
                    evidence_kind="second_model_prediction",
                ),
                policy_for(value_type),
            )
            self.assertEqual(result.status, "unverifiable")
            self.assertEqual(result.reason_code, "no_approved_independent_oracle")

    def test_correction_metadata_is_absent_unless_correction_occurs(self):
        primary = "private-category"
        result = verify_claim(
            primary,
            self.evidence(primary, primary),
            policy_for("categorical"),
        )
        self.assertIsNone(result.correction_metadata)
        self.assertNotIn("private-category", str(result.audit_record()))

    def test_correction_requires_matching_output_contract_validation(self):
        primary = 1.0
        policy = approved_correction_policy(
            policy_id="approved-float-v1",
            value_type="floating_point",
            absolute_tolerance=Decimal("0.001"),
            relative_tolerance=Decimal(0),
            correction_boundary=Decimal("1"),
            output_contract_id="float-output-v1",
        )
        evidence = self.evidence(
            primary,
            1.1,
            correction_output_validated=True,
            correction_output_contract_id="different-contract-v1",
        )
        result = verify_claim(primary, evidence, policy)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.selected_value, primary)
        self.assertIsNone(result.correction_metadata)

    def test_no_callback_is_accepted_so_verification_cannot_recurse(self):
        result = verify_claim(
            1,
            lambda: verify_claim(1, None, policy_for("count")),
            policy_for("count"),
        )
        self.assertEqual(result.status, "unverifiable")
        self.assertEqual(result.reason_code, "malformed_evidence")

    def test_verification_work_and_evidence_are_bounded(self):
        primary = 1
        evidence = self.evidence(primary, {"values": list(range(257))})
        result = verify_claim(primary, evidence, policy_for("schema"))
        self.assertEqual(result.status, "unverifiable")
        self.assertEqual(result.reason_code, "malformed_evidence")

        budget = ExecutionBudget({"jev_checks": 2, "jev_evidence_bytes": 1000})
        for _ in range(2):
            self.assertEqual(
                verify_claim(
                    primary,
                    self.evidence(primary, 1),
                    policy_for("count"),
                    budget=budget,
                ).status,
                "verified",
            )
        self.assertEqual(budget.used("jev_checks"), 2)

    def test_malformed_evidence_attempt_consumes_check_budget(self):
        primary = 1
        budget = ExecutionBudget({"jev_checks": 1, "jev_evidence_bytes": 1000})
        malformed = self.evidence(primary, ["x" * 5000])
        result = verify_claim(
            primary,
            malformed,
            policy_for("count"),
            budget=budget,
        )
        self.assertEqual(result.reason_code, "malformed_evidence")
        self.assertEqual(budget.used("jev_checks"), 1)
        self.assertEqual(budget.used("jev_evidence_bytes"), 0)


if __name__ == "__main__":
    unittest.main()
