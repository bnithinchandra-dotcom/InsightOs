import tempfile
import unittest
import json
import hashlib
from decimal import Decimal
from pathlib import Path

from training.evaluation.hf_runner import (
    CASE_MAX_OUTPUT_TOKENS,
    EvaluationCase,
    Generation,
    MAX_NEW_TOKENS,
    _score,
    _generator,
    _report,
    _sentence_count,
    _final_answer_text,
    _has_reasoning_leak,
    _has_think_tags,
    _termination_reason,
    _truncation_stage,
    _write_report,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_VERSION,
    calculate_profit_margin,
    evaluate_cases,
    evaluate_cases_with_retries,
    load_suite,
    rescore_saved_report,
    _score_generation,
)


def case(scorer, *, expected=None, repetitions=1):
    return EvaluationCase(
        id="fixture",
        category="test",
        purpose="Test fixture.",
        prompt="Synthetic prompt.",
        expected_behavior="Synthetic expected behavior.",
        scorer=scorer,
        human_review_rubric=(),
        expected=expected,
        repetitions=repetitions,
    )


class HuggingFaceEvaluationTests(unittest.TestCase):
    def test_suite_has_stable_unique_ids_and_version(self):
        suite_version, cases = load_suite()
        self.assertEqual(suite_version, "insightos-hf-baseline-v2")
        self.assertEqual(len(cases), 7)
        self.assertEqual(len({item.id for item in cases}), len(cases))

    def test_sentence_count_uses_pysbd_segmentation(self):
        self.assertEqual(
            _sentence_count("Dr. Chen said revenue was $1.5 million. Profit followed."),
            2,
        )

    def test_raw_reasoning_tags_do_not_fail_when_final_text_is_clean(self):
        generation = Generation(
            text="<think>private reasoning</think>Revenue is income.",
            generated_tokens=9,
            duration_ms=1.0,
            tokens_per_second=None,
            reasoning_leak=False,
            raw_leak=True,
            final_leak=False,
            raw_text="<think>private reasoning</think>Revenue is income.",
            final_text="Revenue is income.",
        )
        self.assertTrue(_has_think_tags(generation.raw_text or ""))
        self.assertFalse(generation.final_leak)
        scored = _score_generation(
            next(item for item in load_suite()[1] if item.id == "business_revenue_profit"),
            generation,
        )
        self.assertNotEqual(scored["failure_category"], "reasoning_leak")

    def test_final_reasoning_narration_is_a_machine_failure(self):
        generation = Generation(
            text="The user wants a plain-language answer.",
            generated_tokens=8,
            duration_ms=1.0,
            tokens_per_second=None,
            reasoning_leak=True,
            final_leak=True,
            raw_text="The user wants a plain-language answer.",
            final_text="The user wants a plain-language answer.",
        )
        scored = _score_generation(
            next(item for item in load_suite()[1] if item.id == "business_revenue_profit"),
            generation,
        )
        self.assertEqual(scored["status"], "failed")
        self.assertEqual(scored["failure_category"], "reasoning_leak")

    def test_truncation_records_position_relative_to_think_close(self):
        self.assertEqual(
            _truncation_stage("<think>unfinished reasoning", "max_new_tokens"),
            "before_think_close",
        )
        self.assertEqual(
            _truncation_stage(
                "<think>reasoning</think>unfinished answer",
                "max_new_tokens",
            ),
            "after_think_close",
        )
        self.assertEqual(
            _truncation_stage("plain truncated answer", "max_new_tokens"),
            "no_think_block",
        )
        self.assertIsNone(_truncation_stage("complete", "eos"))

    def test_analysis_plan_date_field_accepts_order_date_synonyms(self):
        analysis_case = next(
            item
            for item in load_suite()[1]
            if item.id == "analysis_plan_profit_margin"
        )
        missing_date = _score(
            analysis_case,
            [
                "Group the records by month, calculate profit as revenue minus "
                "expenses, and calculate margin as profit divided by revenue "
                "times 100."
            ],
        )
        self.assertFalse(missing_date["machine_checks"]["mentions_date_column"])

        for date_field in ("order_date", "order date", "date", "timestamp"):
            with self.subTest(date_field=date_field):
                result = _score(
                    analysis_case,
                    [
                        f"Validate the {date_field} column, group by month, "
                        "calculate profit as revenue minus expenses, and "
                        "calculate margin as profit divided by revenue times 100. "
                        "If the schema is unavailable, the field cannot be confirmed."
                    ],
                )
                self.assertTrue(result["machine_checks"]["mentions_date_column"])

    def test_analysis_plan_formula_check_rejects_ten_and_accepts_one_hundred(self):
        analysis_case = next(
            item
            for item in load_suite()[1]
            if item.id == "analysis_plan_profit_margin"
        )
        incorrect = _score(
            analysis_case,
            ["Profit margin = profit / revenue * 10."],
        )
        correct = _score(
            analysis_case,
            ["Profit margin = profit / revenue * 100."],
        )
        self.assertFalse(
            incorrect["machine_checks"]["does_not_use_incorrect_margin_formula"]
        )
        self.assertTrue(
            correct["machine_checks"]["does_not_use_incorrect_margin_formula"]
        )

    def test_analysis_plan_profit_calculation_accepts_unicode_minus(self):
        analysis_case = next(
            item
            for item in load_suite()[1]
            if item.id == "analysis_plan_profit_margin"
        )
        result = _score(
            analysis_case,
            [
                "Profit = Revenue \N{MINUS SIGN} Expenses. Profit margin is "
                "profit divided by revenue times 100."
            ],
        )
        self.assertTrue(
            result["machine_checks"]["states_profit_calculation"]
        )

    def test_zero_revenue_undefined_not_zero_percent_is_not_a_unsafe_claim(self):
        analysis_case = next(
            item
            for item in load_suite()[1]
            if item.id == "analysis_plan_profit_margin"
        )
        result = _score(
            analysis_case,
            [
                "If revenue is zero or missing, the margin is undefined (not 0%). "
                "With positive revenue, zero profit produces a 0% margin."
            ],
        )
        self.assertTrue(
            result["machine_checks"]["handles_missing_or_zero_revenue_safely"]
        )
        self.assertNotEqual(
            result["failure_category"],
            "invalid_profit_margin_edge_case",
        )

    def test_tool_plan_rejects_forbidden_execution_phrases(self):
        tool_case = next(
            item
            for item in load_suite()[1]
            if item.id == "tool_plan_without_execution"
        )
        for text in (
            "I ran the CSV check. The result is a valid margin.",
            "I executed the calculation.",
        ):
            with self.subTest(text=text):
                result = _score(tool_case, [text])
                self.assertFalse(
                    result["machine_checks"]["forbidden_execution_phrases_absent"]
                )

        safe = _score(
            tool_case,
            [
                "If given access, I would check the revenue and cost columns, "
                "validate missing values, and calculate the margin."
            ],
        )
        self.assertTrue(
            safe["machine_checks"]["forbidden_execution_phrases_absent"]
        )

    def test_case_specific_output_limits_match_the_evaluation_contract(self):
        self.assertEqual(CASE_MAX_OUTPUT_TOKENS["repeat_exact_token"], 64)
        self.assertEqual(CASE_MAX_OUTPUT_TOKENS["json_profit_margin"], 64)
        self.assertEqual(
            CASE_MAX_OUTPUT_TOKENS["instruction_exactly_two_sentences"],
            256,
        )
        for case_id in (
            "business_revenue_profit",
            "missing_churn_inputs",
            "analysis_plan_profit_margin",
            "tool_plan_without_execution",
        ):
            self.assertEqual(CASE_MAX_OUTPUT_TOKENS[case_id], 768)

    def test_final_text_after_last_think_close_is_graded_and_raw_is_preserved(self):
        repeat_case = next(
            item for item in load_suite()[1] if item.id == "repeat_exact_token"
        )
        attempts = []
        responses = [
            "Reasoning text.</think>not the token",
            "Another thought.</think>ORBIT-72",
        ]

        def generate(_case, *, feedback=None):
            attempts.append(feedback)
            raw = responses[len(attempts) - 1]
            return Generation(
                text=raw,
                raw_text=raw,
                final_text=_final_answer_text(raw),
                generated_tokens=5,
                duration_ms=10.0,
                tokens_per_second=500.0,
                termination_reason="eos",
                reasoning_leak=_has_reasoning_leak(raw),
            )

        result = evaluate_cases_with_retries(
            (EvaluationCase(**{**repeat_case.__dict__, "repetitions": 1}),),
            generate,
            max_attempts=3,
            deterministic_fallbacks=False,
        )[0]
        self.assertEqual(result["pass_at_1"], {"passed": 0, "total": 1, "rate": 0.0})
        self.assertEqual(
            result["pass_after_retry"],
            {"passed": 1, "total": 1, "rate": 1.0},
        )
        self.assertEqual(result["generations"][0]["raw_text"], responses[1])
        self.assertEqual(result["generations"][0]["final_text"], "ORBIT-72")
        self.assertTrue(result["trials"][0]["attempts"][1]["generation"]["reasoning_leak"])
        self.assertIn("did not pass validation", attempts[1])

    def test_deterministic_fallbacks_are_separate_from_pass_at_one(self):
        suite_cases = {item.id: item for item in load_suite()[1]}
        for case_id in (
            "repeat_exact_token",
            "json_profit_margin",
            "missing_churn_inputs",
        ):
            with self.subTest(case_id=case_id):
                test_case = EvaluationCase(
                    **{**suite_cases[case_id].__dict__, "repetitions": 1}
                )

                def generate(_case, *, feedback=None):
                    return Generation(
                        text="invalid model output",
                        raw_text="invalid model output",
                        final_text="invalid model output",
                        generated_tokens=3,
                        duration_ms=1.0,
                        tokens_per_second=3000.0,
                        termination_reason="eos",
                    )

                result = evaluate_cases_with_retries(
                    (test_case,),
                    generate,
                    max_attempts=3,
                )[0]
                self.assertEqual(result["pass_at_1"]["passed"], 0)
                self.assertEqual(result["pass_after_retry"]["passed"], 1)
                self.assertEqual(result["fallback_count"], 1)
                self.assertEqual(
                    result["generations"][0]["source"],
                    "deterministic_fallback",
                )

    def test_retry_evaluation_stops_at_first_generation_error_when_requested(self):
        exact_case = next(
            item for item in load_suite()[1] if item.id == "repeat_exact_token"
        )
        called = []

        class SyntheticTransportError(RuntimeError):
            category = "timeout"

        def generate(_test_case, *, feedback=None):
            called.append(feedback)
            if len(called) == 1:
                return Generation(
                    text="ORBIT-72",
                    generated_tokens=1,
                    duration_ms=12.0,
                    tokens_per_second=None,
                    termination_reason="eos",
                    reasoning_leak=False,
                    raw_text="ORBIT-72",
                    final_text="ORBIT-72",
                )
            if len(called) == 2:
                return Generation(
                    text="wrong token",
                    generated_tokens=2,
                    duration_ms=13.0,
                    tokens_per_second=None,
                    termination_reason="eos",
                    reasoning_leak=False,
                    raw_text="wrong token",
                    final_text="wrong token",
                )
            raise SyntheticTransportError("Synthetic request deadline exceeded.")

        results = evaluate_cases_with_retries(
            (exact_case,),
            generate,
            trials_per_case=3,
            max_attempts=3,
            deterministic_fallbacks=True,
            stop_on_generation_error=True,
        )
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["id"], exact_case.id)
        self.assertEqual(results[0]["status"], "error")
        self.assertEqual(len(called), 3)
        self.assertEqual(result["requested_trial_count"], 3)
        self.assertEqual(result["completed_trial_count"], 1)
        self.assertEqual(result["incomplete_trial_count"], 2)
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["pass_at_1"], {"passed": 1, "total": 1, "rate": 1.0})
        self.assertEqual(result["pass_after_retry"], {"passed": 1, "total": 1, "rate": 1.0})
        self.assertEqual(result["fallback_count"], 0)
        self.assertEqual(len(result["generations"]), 1)
        self.assertEqual(len(result["trials"]), 2)
        self.assertTrue(result["trials"][0]["completed"])
        interrupted_trial = result["trials"][1]
        self.assertFalse(interrupted_trial["completed"])
        self.assertEqual(len(interrupted_trial["attempts"]), 1)
        self.assertIsNone(interrupted_trial["final_score"])
        self.assertEqual(result["error"]["category"], "timeout")

    def test_report_records_versioned_prompt_identity(self):
        report = _report("fixture", (), [], model_load_ms=None)
        self.assertEqual(
            report["generation_settings"]["system_prompt"],
            {
                "version": SYSTEM_PROMPT_VERSION,
                "sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            },
        )
        self.assertIn("profit is zero, the margin is valid", SYSTEM_PROMPT)

    def test_json_scorer_accepts_correct_margin_within_tolerance(self):
        result = _score(
            case(
                "json_profit_margin",
                expected={
                    "metric": "profit_margin",
                    "value": 25,
                    "unit": "%",
                    "absolute_tolerance": 0.01,
                },
            ),
            ['{"metric":"profit_margin","value":25.005,"unit":"%"}'],
        )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["machine_checks"]["numeric_value_within_tolerance"])

    def test_json_scorer_keeps_canonical_data_separate_from_raw_compliance(self):
        expected = {
            "metric": "profit_margin",
            "value": 25,
            "unit": "%",
            "absolute_tolerance": 0.01,
        }
        plain = _score(
            case("json_profit_margin", expected=expected),
            ['{"unit":"%","value":25,"metric":"profit_margin"}'],
        )
        self.assertEqual(plain["status"], "passed")
        self.assertEqual(
            plain["canonical_json"],
            '{"metric":"profit_margin","unit":"%","value":25}',
        )

        fenced = _score(
            case("json_profit_margin", expected=expected),
            ['```json\n{"metric":"profit_margin","value":25,"unit":"%"}\n```'],
        )
        self.assertEqual(fenced["status"], "failed")
        self.assertEqual(
            fenced["canonical_json"],
            '{"metric":"profit_margin","unit":"%","value":25}',
        )
        self.assertFalse(fenced["machine_checks"]["json_only_compliant"])

    def test_profit_and_margin_calculation_handles_missing_or_zero_revenue(self):
        self.assertEqual(
            calculate_profit_margin(1000, expenses=750),
            (Decimal("250"), Decimal("25")),
        )
        self.assertEqual(
            calculate_profit_margin(1000, profit=250),
            (Decimal("250"), Decimal("25")),
        )
        self.assertIsNone(calculate_profit_margin(None, expenses=750))
        self.assertIsNone(calculate_profit_margin(1000))
        self.assertIsNone(calculate_profit_margin(0, expenses=750))
        self.assertIsNone(calculate_profit_margin(-1000, expenses=750))
        self.assertEqual(
            calculate_profit_margin(1000, expenses=1250),
            (Decimal("-250"), Decimal("-25")),
        )
        with self.assertRaisesRegex(ValueError, "Supply expenses or profit"):
            calculate_profit_margin(1000, expenses=750, profit=250)
        with self.assertRaisesRegex(ValueError, "finite number"):
            calculate_profit_margin(True, profit=250)

    def test_json_profit_margin_checks_reference_against_prompt_inputs(self):
        suite_case = next(
            item for item in load_suite()[1] if item.id == "json_profit_margin"
        )
        correct = _score(
            suite_case,
            ['{"metric":"profit_margin","value":25,"unit":"%"}'],
        )
        self.assertTrue(correct["machine_checks"]["reference_calculation_valid"])
        self.assertEqual(correct["machine_checks"]["formula_expected_value"], 25)

        inconsistent_case = EvaluationCase(
            **{
                **suite_case.__dict__,
                "expected": {
                    **suite_case.expected,
                    "value": 20,
                },
            }
        )
        inconsistent = _score(
            inconsistent_case,
            ['{"metric":"profit_margin","value":25,"unit":"%"}'],
        )
        self.assertEqual(inconsistent["status"], "failed")
        self.assertFalse(
            inconsistent["machine_checks"]["reference_calculation_valid"]
        )

    def test_json_scorer_rejects_malformed_json_wrong_types_extra_prose_and_missing_fields(self):
        expected = {
            "metric": "profit_margin",
            "value": 25,
            "unit": "%",
            "absolute_tolerance": 0.01,
        }
        test_case = case("json_profit_margin", expected=expected)
        for output, failure in (
            ("not JSON", "malformed_json"),
            ('{"metric":"profit_margin","value":"25","unit":"%"}', "json_schema_mismatch"),
            ('Here is the result: {"metric":"profit_margin","value":25,"unit":"%"}', "json_only_violation"),
            ('{"metric":"profit_margin","value":25}', "json_schema_mismatch"),
        ):
            with self.subTest(output=output):
                result = _score(test_case, [output])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_category"], failure)

    def test_json_scorer_rejects_duplicate_keys_and_nonstandard_constants(self):
        test_case = case(
            "json_profit_margin",
            expected={
                "metric": "profit_margin",
                "value": 25,
                "unit": "%",
                "absolute_tolerance": 0.01,
            },
        )
        for output in (
            '{"metric":"profit_margin","metric":"other","value":25,"unit":"%"}',
            '{"metric":"profit_margin","value":NaN,"unit":"%"}',
        ):
            with self.subTest(output=output):
                result = _score(test_case, [output])
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["machine_checks"]["json_parse_valid"])

    def test_json_diagnostics_separate_fence_from_valid_inner_content(self):
        result = _score(
            case(
                "json_profit_margin",
                expected={
                    "metric": "profit_margin",
                    "value": 25,
                    "unit": "%",
                    "absolute_tolerance": 0.01,
                },
            ),
            ['```json\n{"metric":"profit_margin","value":25,"unit":"%"}\n```'],
        )
        checks = result["machine_checks"]
        self.assertEqual(result["status"], "failed")
        self.assertFalse(checks["json_parse_valid"])
        self.assertTrue(checks["embedded_json_parse_valid"])
        self.assertTrue(checks["markdown_fence_present"])
        self.assertFalse(checks["json_only_compliant"])
        self.assertTrue(checks["schema_compliant"])
        self.assertTrue(checks["numeric_value_within_tolerance"])
        self.assertEqual(
            result["dimension_scores"]["calculation_correctness"],
            "passed",
        )
        self.assertEqual(result["dimension_scores"]["json_compliance"], "failed")

    def test_json_scorer_rejects_boolean_as_numeric_value(self):
        result = _score(
            case(
                "json_profit_margin",
                expected={
                    "metric": "profit_margin",
                    "value": 25,
                    "unit": "%",
                    "absolute_tolerance": 0.01,
                },
            ),
            ['{"metric":"profit_margin","value":true,"unit":"%"}'],
        )
        self.assertEqual(result["status"], "failed")

    def test_two_sentence_scorer_checks_count_and_leaves_semantics_for_review(self):
        test_case = case("two_sentences")
        passed_count = _score(test_case, ["Revenue is income before costs. Profit remains after costs."])
        self.assertEqual(passed_count["status"], "needs_review")
        self.assertTrue(passed_count["machine_checks"]["exactly_two_sentences"])

        failed_count = _score(test_case, ["Revenue is income before costs."])
        self.assertEqual(failed_count["status"], "failed")
        self.assertEqual(failed_count["failure_category"], "sentence_count_mismatch")
        three_sentences = _score(
            test_case,
            ["Revenue is income. Profit remains after costs. Costs reduce profit."],
        )
        self.assertEqual(three_sentences["status"], "failed")
        newline_boundary = _score(
            test_case,
            ["Revenue is income before costs.\nProfit remains after costs."],
        )
        self.assertTrue(newline_boundary["machine_checks"]["exactly_two_sentences"])

    def test_two_sentence_scorer_handles_common_abbreviations_and_decimals(self):
        test_case = case("two_sentences")
        prohibited = _score(
            test_case,
            ["Dr. Smith should compare the segments. Use the median to reduce outlier effects."],
        )
        self.assertTrue(prohibited["machine_checks"]["exactly_two_sentences"])
        self.assertFalse(prohibited["machine_checks"]["abbreviation_free"])
        self.assertEqual(prohibited["dimension_scores"]["sentence_count_compliance"], "passed")
        self.assertEqual(prohibited["dimension_scores"]["abbreviation_compliance"], "failed")
        self.assertEqual(prohibited["dimension_scores"]["instruction_compliance"], "failed")
        self.assertEqual(prohibited["failure_category"], "abbreviation_violation")

        eg_abbreviation = _score(
            test_case,
            ["Use robust statistics, e.g. the median. Check the result against the mean."],
        )
        self.assertTrue(eg_abbreviation["machine_checks"]["exactly_two_sentences"])
        self.assertFalse(eg_abbreviation["machine_checks"]["abbreviation_free"])
        self.assertEqual(eg_abbreviation["dimension_scores"]["instruction_compliance"], "failed")

        valid = _score(
            test_case,
            ["Use a 2.5 percent threshold. Review the result before reporting it."],
        )
        self.assertTrue(valid["machine_checks"]["exactly_two_sentences"])
        self.assertTrue(valid["machine_checks"]["abbreviation_free"])
        self.assertEqual(valid["dimension_scores"]["instruction_compliance"], "passed")
        self.assertEqual(valid["status"], "needs_review")

    def test_analysis_plan_scorers_check_requirements_without_auto_approval(self):
        _, suite_cases = load_suite()
        by_id = {item.id: item for item in suite_cases}
        complete_plan = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "Check the revenue and expense fields, and validate that the "
                "transaction date column parses as dates. The source schema is "
                "unavailable, so I cannot confirm which columns exist; these are "
                "required field roles only. Aggregate revenue and expenses by month, "
                "profit = revenue - expenses, and profit margin = profit / revenue "
                "times 100. If revenue is missing or zero, do not calculate the "
                "margin; identify it as undefined. With positive revenue, zero "
                "profit gives a valid 0% margin. I have not inspected or analyzed "
                "the file."
            ],
        )
        self.assertEqual(complete_plan["status"], "needs_review")
        self.assertTrue(all(complete_plan["machine_checks"].values()))
        self.assertEqual(complete_plan["human_review_status"], "pending")

        unsafe_revenue_edge_case = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "Validate the date column and group by month. Profit is revenue "
                "minus expenses, and margin is profit divided by revenue times "
                "100. If revenue is zero, the margin is 0%."
            ],
        )
        self.assertFalse(
            unsafe_revenue_edge_case["machine_checks"][
                "handles_missing_or_zero_revenue_safely"
            ]
        )
        self.assertEqual(
            unsafe_revenue_edge_case["failure_category"],
            "invalid_profit_margin_edge_case",
        )

        contradictory_revenue_edge_case = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "Validate the date column and group by month. Profit is revenue "
                "minus expenses, and margin is profit divided by revenue times "
                "100. If revenue is missing, identify the margin as undefined "
                "(or 0% if revenue is zero)."
            ],
        )
        self.assertFalse(
            contradictory_revenue_edge_case["machine_checks"][
                "handles_missing_or_zero_revenue_safely"
            ]
        )
        self.assertEqual(
            contradictory_revenue_edge_case["failure_category"],
            "invalid_profit_margin_edge_case",
        )

        safe_revenue_edge_case = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "Validate the date column and group by month. Profit is revenue "
                "minus expenses, and margin is profit divided by revenue times "
                "100. If revenue is missing or zero, do not calculate the margin; "
                "identify the input as unavailable. With positive revenue, zero "
                "profit gives a valid 0% margin. The source schema is unavailable."
            ],
        )
        self.assertTrue(
            safe_revenue_edge_case["machine_checks"][
                "handles_missing_or_zero_revenue_safely"
            ]
        )

        false_execution_claim = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "I inspected the CSV. It contains revenue, costs, and dates. "
                "I grouped by month and calculated profit divided by revenue."
            ],
        )
        self.assertEqual(false_execution_claim["status"], "failed")
        self.assertEqual(
            false_execution_claim["failure_category"],
            "unsupported_execution_claim",
        )

        diagnostic_analysis_output = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "4. **Compute profit** as `Revenue - Expenses`. Compute profit margin as "
                "`(Profit / Revenue) * 100`."
            ],
        )
        self.assertTrue(
            diagnostic_analysis_output["machine_checks"]["states_profit_calculation"]
        )
        self.assertTrue(
            diagnostic_analysis_output["machine_checks"][
                "states_profit_margin_formula"
            ]
        )

        unsupported_column_claim = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "The CSV contains revenue, costs, and an OrderDate column. "
                "Aggregate by month and calculate profit margin."
            ],
        )
        self.assertFalse(
            unsupported_column_claim["machine_checks"]["does_not_claim_execution"]
        )
        self.assertFalse(
            unsupported_column_claim["machine_checks"][
                "acknowledges_schema_unavailable"
            ]
        )

        invalid_formula_plan = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "Validate the order date column and group revenue by month. "
                "The schema is unavailable. Profit is revenue minus expenses, "
                "but profit margin is revenue divided by total revenue times 100."
            ],
        )
        self.assertFalse(
            invalid_formula_plan["machine_checks"][
                "does_not_use_incorrect_margin_formula"
            ]
        )
        self.assertEqual(
            invalid_formula_plan["failure_category"],
            "incorrect_profit_margin_formula",
        )

        tool_plan = _score(
            by_id["tool_plan_without_execution"],
            [
                "I would check for revenue and cost or profit fields, validate "
                "missing values, and stop if required data is absent. No file was "
                "accessed and no tool was executed."
            ],
        )
        self.assertEqual(tool_plan["status"], "needs_review")
        self.assertEqual(tool_plan["human_review_status"], "pending")
        self.assertTrue(all(tool_plan["machine_checks"].values()))

        negated_execution_mention = _score(
            by_id["tool_plan_without_execution"],
            [
                "The user wants a plan without claiming I opened a file or did "
                "any calculations."
            ],
        )
        self.assertTrue(
            negated_execution_mention["machine_checks"]["does_not_claim_execution"]
        )

        diagnostic_tool_plan = _score(
            by_id["tool_plan_without_execution"],
            [
                "Describe a safe plan: Check Column Names. Ensure the CSV contains "
                "the columns Revenue and Expenses. Validate values before "
                "calculating profit margin."
            ],
        )
        self.assertTrue(
            diagnostic_tool_plan["machine_checks"]["does_not_claim_execution"]
        )
        self.assertEqual(diagnostic_tool_plan["status"], "needs_review")

        bad_tool_plan_formula = _score(
            by_id["tool_plan_without_execution"],
            [
                "Check for revenue and expense fields and validate missingness. "
                "Calculate profit margin by dividing revenue by total revenue."
            ],
        )
        self.assertEqual(bad_tool_plan_formula["status"], "failed")
        self.assertEqual(
            bad_tool_plan_formula["failure_category"],
            "incorrect_profit_margin_formula",
        )
        bad_tool_plan_scale = _score(
            by_id["tool_plan_without_execution"],
            [
                "Check revenue and expense fields and validate missingness. "
                "Profit margin is (revenue - expenses) / revenue * 10."
            ],
        )
        self.assertFalse(
            bad_tool_plan_scale["machine_checks"][
                "does_not_use_incorrect_margin_formula"
            ]
        )
        self.assertEqual(
            bad_tool_plan_scale["failure_category"],
            "incorrect_profit_margin_formula",
        )
        corrected_formula = _score(
            by_id["tool_plan_without_execution"],
            [
                "Check revenue and expense fields. Calculate profit as revenue "
                "minus expenses, then divide profit by revenue and multiply by 100. "
                "Do not divide revenue by total revenue."
            ],
        )
        self.assertTrue(
            corrected_formula["machine_checks"][
                "does_not_use_incorrect_margin_formula"
            ]
        )
        self.assertEqual(corrected_formula["status"], "needs_review")

        latex_plan = _score(
            by_id["analysis_plan_profit_margin"],
            [
                "Identify revenue, total cost, and order date; validate the date "
                "column and aggregate by month. The schema is unavailable. "
                "Profit equals revenue minus costs. "
                "Profit margin is "
                r"\left(\frac{\text{Total Revenue} - \text{Total Cost}}"
                r"{\text{Total Revenue}}\right)\times 100. "
                "If revenue is missing or zero, do not calculate the margin; "
                "identify it as undefined. With positive revenue, zero profit "
                "gives a valid 0% margin. No file was inspected."
            ],
        )
        self.assertTrue(
            latex_plan["machine_checks"]["states_profit_margin_formula"]
        )
        self.assertEqual(latex_plan["status"], "needs_review")
        self.assertTrue(
            all(latex_plan["machine_checks"].values())
        )

        conditional_plan = _score(
            by_id["tool_plan_without_execution"],
            [
                "Check whether the CSV contains revenue and cost or profit fields, "
                "validate missing data, and ensure that the CSV includes the required "
                "columns. No file has been inspected."
            ],
        )
        self.assertEqual(conditional_plan["status"], "needs_review")
        self.assertTrue(conditional_plan["machine_checks"]["does_not_claim_execution"])

    def test_business_finance_contradiction_is_flagged_without_semantic_approval(self):
        _, suite_cases = load_suite()
        finance_case = next(
            item for item in suite_cases if item.id == "business_revenue_profit"
        )
        contradicted = _score(
            finance_case,
            ["Profit is the outflow paid by the business."],
        )
        self.assertEqual(contradicted["status"], "failed")
        self.assertEqual(contradicted["failure_category"], "financial_concept_error")
        self.assertEqual(contradicted["human_review_status"], "pending")
        markdown_contradiction = _score(
            finance_case,
            ["So, **revenue** is the inflow, and **profit** is the outflow."],
        )
        self.assertEqual(markdown_contradiction["status"], "failed")

        sound_but_reviewable = _score(
            finance_case,
            ["Revenue is income before costs. Profit is what remains after costs."],
        )
        self.assertEqual(sound_but_reviewable["status"], "needs_review")
        self.assertEqual(sound_but_reviewable["human_review_status"], "pending")

    def test_missing_input_scorer_rejects_invented_percentage(self):
        test_case = case("missing_inputs")
        self.assertEqual(
            _score(
                test_case,
                [
                    "This cannot be calculated because customer counts, churn counts, "
                    "and the time period are missing."
                ],
            )["status"],
            "needs_review",
        )
        self.assertEqual(
            _score(
                test_case,
                [
                    "Without customer counts, it is impossible to calculate the "
                    "customer churn rate; churn counts and the measurement "
                    "period are missing too."
                ],
            )["status"],
            "needs_review",
        )
        incomplete = _score(
            test_case,
            ["Churn cannot be calculated because customer counts are missing."],
        )
        self.assertEqual(incomplete["status"], "failed")
        self.assertFalse(incomplete["machine_checks"]["identifies_churn_count_input"])
        self.assertFalse(incomplete["machine_checks"]["identifies_time_period_input"])
        for output in (
            '{"missing_inputs":["customer_count","churn_count","time_period"],'
            '"rate":null}',
            '```json\n{"missing_inputs":["customer_count","churn_count",'
            '"time_period"],"statement":"cannot calculate"}\n```',
        ):
            with self.subTest(output=output):
                structured_abstention = _score(test_case, [output])
                self.assertEqual(structured_abstention["status"], "needs_review")
                self.assertTrue(
                    structured_abstention["machine_checks"][
                        "identifies_customer_count_input"
                    ]
                )
                self.assertTrue(
                    structured_abstention["machine_checks"][
                        "identifies_churn_count_input"
                    ]
                )
                self.assertTrue(
                    structured_abstention["machine_checks"][
                        "identifies_time_period_input"
                    ]
                )
                self.assertTrue(
                    structured_abstention["machine_checks"][
                        "does_not_invent_percentage"
                    ]
                )
        self.assertEqual(
            _score(test_case, ["The churn rate is 23 percent."])["status"],
            "failed",
        )
        self.assertEqual(
            _score(test_case, ["The churn rate is 23%."])["status"],
            "failed",
        )
        self.assertEqual(
            _score(test_case, ["The churn rate is twenty-five percent."])["status"],
            "failed",
        )
        self.assertEqual(
            _score(test_case, ["The customer churn rate is 23."])["status"],
            "failed",
        )
        negative = _score(test_case, ["Nothing is missing; the rate is unknown."])
        self.assertEqual(negative["status"], "failed")
        self.assertFalse(negative["machine_checks"]["identifies_missing_information"])

    def test_case_runtime_exception_is_recorded_and_later_cases_continue(self):
        first, second = case("repeat_token"), case("repeat_token")
        first = EvaluationCase(**{**first.__dict__, "id": "first"})
        second = EvaluationCase(**{**second.__dict__, "id": "second"})
        calls = iter((RuntimeError("synthetic failure"), Generation("ORBIT-72", 1, 2.0, 500.0)))

        def generate(_case):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        results = evaluate_cases((first, second), generate)
        self.assertEqual([result["status"] for result in results], ["error", "passed"])
        self.assertEqual(results[0]["error"]["type"], "RuntimeError")
        self.assertEqual(results[1]["generations"][0]["text"], "ORBIT-72")

    def test_repeat_token_requires_each_complete_response_to_be_the_literal_token(self):
        suite_case = next(
            item for item in load_suite()[1] if item.id == "repeat_exact_token"
        )
        repeated_exact = _score(
            suite_case,
            ["ORBIT-72", "ORBIT-72", "ORBIT-72"],
        )
        self.assertEqual(repeated_exact["status"], "passed")
        self.assertEqual(
            repeated_exact["dimension_scores"]["per_generation_exact_match"],
            [True, True, True],
        )

        repeated_wrapper = _score(
            suite_case,
            [
                '{"synthetic_token": "ORBIT-72"}',
                '{"synthetic_token": "ORBIT-72"}',
                '{"synthetic_token": "ORBIT-72"}',
            ],
        )
        self.assertEqual(repeated_wrapper["status"], "failed")
        self.assertEqual(
            repeated_wrapper["dimension_scores"]["per_generation_exact_match"],
            [False, False, False],
        )

        inconsistent = _score(
            suite_case,
            ["ORBIT-72", "ORBIT-72", "ORBIT-73"],
        )
        self.assertEqual(inconsistent["status"], "failed")

    def test_scoring_exception_is_an_error_and_later_case_continues(self):
        first = EvaluationCase(
            id="bad-scorer",
            category="test",
            purpose="Test scoring failure.",
            prompt="Synthetic prompt.",
            expected_behavior="N/A",
            scorer="unknown_scorer",
            human_review_rubric=(),
        )
        second = EvaluationCase(**{**first.__dict__, "id": "valid", "scorer": "repeat_token"})
        results = evaluate_cases(
            (first, second),
            lambda _case: Generation("ORBIT-72", 1, 1.0, 1.0),
        )
        self.assertEqual([item["status"] for item in results], ["error", "passed"])
        self.assertEqual(results[0]["error_phase"], "scoring")
        self.assertEqual(results[0]["failure_category"], "scoring_error")

    def test_termination_reason_uses_eos_and_generation_cap(self):
        self.assertEqual(
            _termination_reason([4, 2], eos_token_id=2, max_new_tokens=192),
            "eos",
        )
        self.assertEqual(
            _termination_reason([4, 5], eos_token_id=2, max_new_tokens=2),
            "max_new_tokens",
        )
        self.assertEqual(
            _termination_reason([4], eos_token_id=2, max_new_tokens=192),
            "unknown",
        )

    def test_generator_uses_chat_template_inference_mode_and_slices_prompt_tokens(self):
        class FakeTensor:
            def __init__(self, values, shape=None):
                self.values = values
                self.shape = shape or (1, len(values))

            def __getitem__(self, key):
                batch, token_slice = key
                self.assert_batch_zero(batch)
                return FakeTensor(self.values[token_slice], (len(self.values[token_slice]),))

            def tolist(self):
                return self.values

            @staticmethod
            def assert_batch_zero(batch):
                if batch != 0:
                    raise AssertionError("unexpected batch index")

        class FakeTokenizer:
            eos_token_id = 2
            pad_token_id = 0

            def __init__(self):
                self.template_arguments = None
                self.decoded = None

            def apply_chat_template(self, messages, **kwargs):
                self.template_arguments = (messages, kwargs)
                return {"input_ids": FakeTensor([1, 2, 3])}

            def decode(self, token_ids, **kwargs):
                self.decoded = token_ids.values
                return "synthetic output"

        class FakeModel:
            def __init__(self):
                self.generation_arguments = None

            def generate(self, **kwargs):
                self.generation_arguments = kwargs
                return FakeTensor([1, 2, 3, 4, 5], shape=(1, 5))

        class FakeTorch:
            def __init__(self):
                self.inference_entered = False

            def inference_mode(self):
                class InferenceContext:
                    def __enter__(inner_self):
                        self.inference_entered = True

                    def __exit__(inner_self, *_args):
                        return False

                return InferenceContext()

        tokenizer = FakeTokenizer()
        model = FakeModel()
        torch = FakeTorch()
        generate = _generator(torch, tokenizer, model)
        repeat_case = next(
            item for item in load_suite()[1] if item.id == "repeat_exact_token"
        )
        result = generate(repeat_case)

        self.assertTrue(torch.inference_entered)
        messages, options = tokenizer.template_arguments
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], SYSTEM_PROMPT)
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("profit = revenue - expenses", SYSTEM_PROMPT)
        self.assertIn("never revenue / total revenue", SYSTEM_PROMPT)
        self.assertTrue(options["add_generation_prompt"])
        self.assertFalse(options["enable_thinking"])
        self.assertEqual(tokenizer.decoded, [4, 5])
        self.assertEqual(result.text, "synthetic output")
        self.assertEqual(result.generated_tokens, 2)
        self.assertEqual(result.termination_reason, "unknown")
        self.assertFalse(model.generation_arguments["do_sample"])
        self.assertEqual(model.generation_arguments["max_new_tokens"], 64)

        sampled = _generator(torch, tokenizer, model, temperature=0.3)
        sampled(repeat_case, feedback="Return only the token.")
        self.assertTrue(model.generation_arguments["do_sample"])
        self.assertEqual(model.generation_arguments["temperature"], 0.3)
        messages, _options = tokenizer.template_arguments
        self.assertEqual(messages[-1]["content"], "Return only the token.")

    def test_offline_rescore_preserves_raw_outputs_and_marks_unknown_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "original.json"
            output = Path(directory) / "rescored.json"
            original = {
                "run_id": "source-run",
                "timestamp_utc": "2026-10-22T00:00:00+00:00",
                "counts": {"passed": 1, "failed": 1, "needs_review": 0},
                "model_load_duration_ms": 123.4,
                "checkpoint": {"pinned_revision": "fixture-revision"},
                "environment": {"python": "fixture", "tokenizers": "fixture-tokenizer"},
                "generation_settings": {"do_sample": False},
                "cases": [
                    {
                        "id": "missing_churn_inputs",
                        "category": "grounding_and_uncertainty",
                        "status": "failed",
                        "error": None,
                        "generations": [
                            {
                                "text": (
                                    "Without customer counts, it is impossible to "
                                    "calculate the customer churn rate; the churn count and "
                                    "measurement period are also missing."
                                ),
                                "generated_tokens": 43,
                                "duration_ms": 10.0,
                                "tokens_per_second": 4.3,
                            }
                        ],
                    },
                    {
                        "id": "json_profit_margin",
                        "category": "structured_output_and_calculation",
                        "status": "failed",
                        "error": None,
                        "generations": [
                            {
                                "text": (
                                    "```json\n"
                                    '{"metric":"profit_margin","value":25,"unit":"%"}'
                                    "\n```"
                                ),
                                "generated_tokens": 29,
                                "duration_ms": 10.0,
                                "tokens_per_second": 2.9,
                            }
                        ],
                    },
                    {
                        "id": "analysis_plan_profit_margin",
                        "category": "data_analysis_planning",
                        "status": "needs_review",
                        "error": None,
                        "generations": [
                            {
                                "text": "The analysis would calculate the monthly Sub",
                                "generated_tokens": MAX_NEW_TOKENS,
                                "duration_ms": 10.0,
                                "tokens_per_second": 19.2,
                            }
                        ],
                    },
                ],
            }
            source.write_text(json.dumps(original), encoding="utf-8")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

            report = rescore_saved_report(source)

            self.assertFalse(report["inference_performed"])
            self.assertEqual(report["source_report_sha256"], source_hash)
            self.assertEqual(report["source_run_timestamp_utc"], original["timestamp_utc"])
            self.assertEqual(report["model_load_duration_ms"], 123.4)
            self.assertEqual(
                report["tokenizer"],
                {
                    "package": "tokenizers",
                    "package_version": "fixture-tokenizer",
                    "checkpoint_revision": "fixture-revision",
                },
            )
            self.assertEqual(
                [item["rescored_status"] for item in report["cases"]],
                ["needs_review", "failed", "failed"],
            )
            self.assertEqual(
                report["cases"][0]["original_status"],
                "failed",
            )
            self.assertEqual(
                report["cases"][0]["machine_checks"]["identifies_missing_information"],
                True,
            )
            self.assertEqual(
                report["cases"][1]["dimension_scores"]["calculation_correctness"],
                "passed",
            )
            self.assertEqual(
                report["cases"][2]["completion_assessment"],
                "likely_truncated_at_limit_termination_metadata_unavailable",
            )
            self.assertEqual(
                report["cases"][2]["failure_category"],
                "analysis_plan_requirement_missing",
            )
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
            self.assertFalse(output.exists())

    def test_report_output_refuses_to_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.json"
            target.write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                _write_report({"run_id": "fixture"}, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
