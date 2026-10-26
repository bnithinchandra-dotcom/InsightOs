from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from training.evaluation import ba_da_benchmark as benchmark


class BaDaBenchmarkTests(unittest.TestCase):
    def test_benchmark_has_sixty_cases_balanced_categories_and_splits(self):
        first = benchmark.validate_benchmark()
        second = benchmark.validate_benchmark()

        self.assertEqual(first, second)
        self.assertEqual(first["case_count"], 60)
        self.assertEqual(set(first["category_counts"].values()), {10})
        self.assertEqual(first["split_counts"], {
            "development": 20,
            "validation": 20,
            "hidden_test": 20,
        })
        self.assertEqual(first["reference_count"], 60)
        self.assertEqual(first["family_count"], 51)
        self.assertEqual(first["review_status_counts"], {
            "approved": 25,
            "proposed_reference": 35,
            "revised": 0,
            "not_required": 0,
            "pending": 0,
        })
        cases, references = benchmark.load_documents()
        case_dimensions = {
            case["case_id"]: set(case["scoring_dimensions"])
            for case in cases["cases"]
        }
        for reference in references["references"]:
            with self.subTest(case=reference["case_id"]):
                self.assertEqual(
                    set(reference["dimension_rubric"]),
                    case_dimensions[reference["case_id"]],
                )
                if reference["review_status"] == "proposed_reference":
                    self.assertEqual(
                        set(reference["operational_scoring"]),
                        case_dimensions[reference["case_id"]],
                    )

    def test_schema_declares_data_contract_and_case_fields(self):
        schema = json.loads(benchmark.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["benchmark_version"]["const"],
                         "insightos-ba-da-diagnostic-v2")
        self.assertIn("diagnostic_pairs", schema["required"])
        self.assertIn("case_id", schema["$defs"]["case"]["required"])
        self.assertIn("family_id", schema["$defs"]["case"]["required"])
        self.assertIn("split", schema["$defs"]["case"]["required"])
        self.assertEqual(schema["properties"]["cases"]["maxItems"], 60)
        dimensions = set(schema["$defs"]["case"]["properties"]["scoring_dimensions"]["items"]["enum"])
        self.assertTrue(benchmark.SCORING_DIMENSIONS.issubset(dimensions))
        self.assertIn("referenceDocument", schema["$defs"])
        self.assertIn("review_status", schema["$defs"]["reference"]["required"])
        self.assertEqual(
            schema["$defs"]["referenceDocument"]["properties"]["reference_version"]["const"],
            "insightos-ba-da-references-v3",
        )
        self.assertIn("operational_scoring", schema["$defs"]["reference"]["properties"])
        self.assertIn(
            "proposed_reference",
            schema["$defs"]["reference"]["properties"]["review_status"]["enum"],
        )
        self.assertEqual(
            set(
                schema["$defs"]["operationalScoringPolicy"]["properties"][
                    "disposition"
                ]["enum"]
            ),
            {
                "deterministic_component",
                "not_assessed_by_deterministic_scorer",
            },
        )

    def test_current_proposal_report_covers_each_proposed_case_once(self):
        cases, references = benchmark.load_documents()
        case_by_id = {item["case_id"]: item for item in cases["cases"]}
        proposals = {
            item["case_id"]: item
            for item in references["references"]
            if item["review_status"] == "proposed_reference"
        }
        report = (
            benchmark.ROOT / "BA_DA_FINAL_35_CASE_REVIEW.md"
        ).read_text(encoding="utf-8")
        heading_ids = re.findall(
            r"^### ([a-z0-9_]+) - proposed reference$",
            report,
            re.MULTILINE,
        )
        self.assertEqual(len(heading_ids), 35)
        self.assertEqual(set(heading_ids), set(proposals))
        report_sections = {
            match.group(1): match.group(2)
            for match in re.finditer(
                r"^### ([a-z0-9_]+) - proposed reference\n"
                r"(.*?)(?=^### [a-z0-9_]+ - proposed reference$|^## |\Z)",
                report,
                re.MULTILINE | re.DOTALL,
            )
        }
        for case_id, reference in proposals.items():
            with self.subTest(case=case_id):
                self.assertEqual(heading_ids.count(case_id), 1)
                section = report_sections[case_id]
                self.assertIn(case_by_id[case_id]["prompt"], section)
                self.assertIn(
                    json.dumps(
                        case_by_id[case_id]["inputs"],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    section,
                )
                self.assertIn(
                    f"**E. Exact recommended reference answer.** "
                    f"{reference['reference_answer']}",
                    section,
                )
                for dimension in reference["dimension_rubric"]:
                    self.assertIn(f"| `{dimension}` |", section)

    def test_near_duplicate_prompts_share_a_family_and_split(self):
        findings = benchmark.duplicate_prompt_pairs()
        self.assertTrue(findings)
        self.assertTrue(all(not finding["cross_split"] for finding in findings))
        self.assertTrue(all(finding["same_family"] for finding in findings))

    def test_validation_rejects_identical_prompts_across_splits(self):
        payload, references = benchmark.load_documents()
        first = payload["cases"][0]
        second = next(
            case for case in payload["cases"]
            if case["split"] != first["split"]
        )
        second["prompt"] = first["prompt"]
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            cases_path = directory / "cases.json"
            references_path = directory / "references.json"
            cases_path.write_text(json.dumps(payload), encoding="utf-8")
            references_path.write_text(json.dumps(references), encoding="utf-8")
            with self.assertRaisesRegex(
                benchmark.BenchmarkValidationError,
                "Near-duplicate prompts",
            ):
                benchmark.validate_benchmark(cases_path, references_path)

    def test_related_families_cannot_cross_splits(self):
        payload, references = benchmark.load_documents()
        payload["cases"][1]["family_id"] = payload["cases"][0]["family_id"]
        payload["cases"][1]["split"] = (
            "validation"
            if payload["cases"][0]["split"] != "validation"
            else "development"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            cases_path = directory / "cases.json"
            references_path = directory / "references.json"
            cases_path.write_text(json.dumps(payload), encoding="utf-8")
            references_path.write_text(json.dumps(references), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "family"):
                benchmark.validate_benchmark(cases_path, references_path)

    def test_model_input_projection_excludes_reference_and_split_metadata(self):
        cases, _ = benchmark.load_documents()
        forbidden_keys = {
            "split",
            "reference_answer",
            "reference_calculation",
            "tolerances",
            "dimension_rubric",
            "human_review_required",
            "review_status",
            "previous_review_status",
            "operational_scoring",
            "expected_values",
            "expected_rows",
            "expected_error",
            "expected_status",
            "expected_tool",
            "expected_object",
            "expected_arguments",
            "scorer",
            "failure_categories",
            "likely_failure_categories",
            "diagnostic_pairs",
            "diagnostic_question",
            "case_id",
            "family_id",
        }
        for case in cases["cases"]:
            for mode in case["modes"]:
                with self.subTest(case=case["case_id"], mode=mode):
                    projection = benchmark.model_input(case["case_id"], mode)
                    self.assertEqual(set(projection), {
                        "mode",
                        "prompt",
                        "inputs",
                    } | (
                        {"context_cards"} if mode == "context_assisted" else set()
                    ) | (
                        {"tool_contracts", "observed_tool_trace"}
                        if mode == "tool_assisted"
                        and case.get("visible_tool_trace") is not None
                        else {"tool_contracts"}
                        if mode == "tool_assisted"
                        else set()
                    ))
                    stack = [projection]
                    while stack:
                        current = stack.pop()
                        if isinstance(current, dict):
                            self.assertTrue(forbidden_keys.isdisjoint(current))
                            stack.extend(current.values())
                        elif isinstance(current, list):
                            stack.extend(current)
                    if mode != "context_assisted":
                        self.assertNotIn("context_cards", projection)
                    if mode != "tool_assisted":
                        self.assertNotIn("tool_contracts", projection)
                        self.assertNotIn("observed_tool_trace", projection)

    def test_context_and_tool_material_are_restricted_to_allowed_modes(self):
        payload, _ = benchmark.load_documents()
        with self.assertRaises(ValueError):
            benchmark.model_input("ba_requirements_vs_solution", "tool_assisted")
        base = benchmark.model_input("ba_revenue_vs_profit", "base")
        context = benchmark.model_input("ba_revenue_vs_profit", "context_assisted")
        self.assertNotIn("context_cards", base)
        self.assertEqual(
            [card["id"] for card in context["context_cards"]],
            ["business_metrics"],
        )
        self.assertEqual(len(payload["tool_contracts"]), 3)
        self.assertGreaterEqual(len(payload["diagnostic_pairs"]), 5)

    def test_decimal_reference_calculations_and_tolerance_boundaries(self):
        self.assertEqual(benchmark.validate_numeric_references(), 25)
        on_boundary = benchmark.score_reference_values(
            "calc_margin",
            {"margin_percent": "15.0001"},
        )
        beyond_boundary = benchmark.score_reference_values(
            "calc_margin",
            {"margin_percent": "15.0002"},
        )
        self.assertEqual(on_boundary["status"], "needs_review")
        self.assertEqual(on_boundary["dimension_scores"]["numerical_correctness"], 2)
        self.assertTrue(on_boundary["semantic_review_required"])
        revised_values = benchmark._reference("data_simpsons_paradox")["scorer"][
            "expected_values"
        ]
        revised = benchmark.score_reference_values(
            "data_simpsons_paradox",
            revised_values,
        )
        self.assertEqual(revised["status"], "needs_review")
        self.assertEqual(revised["human_review_status"], "pending")
        self.assertEqual(beyond_boundary["status"], "failed")
        self.assertEqual(beyond_boundary["dimension_scores"]["numerical_correctness"], 0)

    def test_unscored_semantic_dimensions_require_human_review(self):
        numeric = benchmark.score_reference_values(
            "calc_profit",
            {"profit": "125000"},
        )
        self.assertEqual(numeric["status"], "needs_review")
        self.assertEqual(numeric["human_review_status"], "pending")
        self.assertTrue(numeric["semantic_review_required"])
        self.assertIn("formula_method_selection", numeric["unscored_rubric_dimensions"])

        arguments = benchmark.score_tool_arguments(
            "tool_arguments_valid",
            "calculator",
            {"expression": "(18 / 600) * 100"},
        )
        self.assertEqual(arguments["status"], "needs_review")
        self.assertEqual(arguments["human_review_status"], "pending")
        self.assertTrue(arguments["semantic_review_required"])

    def test_requested_reference_answers_include_requested_working(self):
        references = {
            item["case_id"]: item
            for item in benchmark.load_documents()[1]["references"]
        }
        self.assertIn("80,000", references["calc_pct_change"]["reference_answer"])
        self.assertIn("× 100", references["calc_pct_change"]["reference_answer"])
        self.assertIn("1,000 kilograms per tonne", references["calc_units"]["reference_answer"])

    def test_formula_equivalence_accepts_rearranged_ratio_and_rejects_coincidence(self):
        for expression in (
            "(18 / 600) * 100",
            "100 * (18 / 600)",
            "18 * 100 / 600",
            "100 * 18 / 600",
            "18 / (600 / 100)",
        ):
            with self.subTest(expression=expression):
                self.assertTrue(
                    benchmark._matches_requested_percentage_formula(expression)
                )
                result = benchmark.score_tool_arguments(
                    "tool_arguments_valid",
                    "calculator",
                    {"expression": expression},
                )
                self.assertEqual(
                    result["dimension_scores"]["formula_method_selection"],
                    2,
                )
        for expression in ("1 + 2", "3", "18 + 100 - 115"):
            with self.subTest(expression=expression):
                self.assertFalse(
                    benchmark._matches_requested_percentage_formula(expression)
                )
        coincidental_result = benchmark.score_tool_arguments(
            "tool_arguments_valid",
            "calculator",
            {"expression": "1 + 2"},
        )
        self.assertEqual(
            coincidental_result["dimension_scores"]["tool_use_correctness"],
            2,
        )
        self.assertEqual(
            coincidental_result["dimension_scores"]["numerical_correctness"],
            2,
        )
        self.assertEqual(
            coincidental_result["dimension_scores"]["formula_method_selection"],
            0,
        )
        self.assertEqual(
            coincidental_result["failure_category"],
            "formula_selection_error",
        )

    def test_proposed_references_preserve_provenance_without_claiming_approval(self):
        _, references = benchmark.load_documents()
        proposals = [
            reference
            for reference in references["references"]
            if reference["review_status"] == "proposed_reference"
        ]
        self.assertEqual(len(proposals), 35)
        self.assertEqual(
            {
                status: sum(
                    reference["previous_review_status"] == status
                    for reference in proposals
                )
                for status in ("revised", "not_required")
            },
            {"revised": 19, "not_required": 16},
        )
        approved = [
            reference
            for reference in references["references"]
            if reference["review_status"] == "approved"
        ]
        self.assertEqual(len(approved), 25)
        for reference in proposals:
            self.assertNotEqual(reference["review_status"], "approved")
            self.assertEqual(
                set(reference["operational_scoring"]),
                set(reference["dimension_rubric"]),
            )
            for dimension, policy in reference["operational_scoring"].items():
                with self.subTest(case=reference["case_id"], dimension=dimension):
                    self.assertIn(
                        policy["disposition"],
                        {
                            "deterministic_component",
                            "not_assessed_by_deterministic_scorer",
                        },
                    )
                    self.assertTrue(policy["rationale"])

    def test_response_scoring_is_separate_from_reference_lifecycle(self):
        valid_json = benchmark.score_case(
            "tool_json_only",
            '{"status":"ok","count":3}',
        )
        self.assertEqual(
            valid_json["reference_validation"]["lifecycle_status"],
            "proposed_reference",
        )
        self.assertFalse(valid_json["reference_validation"]["human_certified"])
        self.assertTrue(valid_json["reference_validation"]["operationally_usable"])
        self.assertEqual(valid_json["model_response_score"]["status"], "scored")
        self.assertEqual(
            valid_json["model_response_score"]["dimension_scores"],
            {"output_instruction_compliance": 2},
        )

        semantic = benchmark.score_case(
            "ba_requirements_vs_solution",
            "The red button will improve the business.",
        )
        self.assertEqual(
            semantic["model_response_score"]["status"],
            "partially_scored",
        )
        self.assertEqual(
            semantic["model_response_score"]["dimension_scores"],
            {},
        )
        self.assertEqual(
            set(
                semantic["model_response_score"]["dimension_dispositions"].values()
            ),
            {"not_assessed_by_deterministic_scorer"},
        )
        self.assertEqual(
            set(semantic["model_response_score"]["not_assessed_dimensions"]),
            {
                "conceptual_correctness",
                "business_relevance",
                "evidence_uncertainty",
            },
        )
        numeric = benchmark.score_reference_values("calc_margin", {"margin_percent": "15"})
        self.assertEqual(
            numeric["reference_validation"]["lifecycle_status"],
            "proposed_reference",
        )
        self.assertEqual(numeric["model_response_score"]["status"], "partially_scored")
        self.assertIn(
            "formula_method_selection",
            numeric["model_response_score"]["not_assessed_dimensions"],
        )

    def test_offline_baseline_readiness_gate_does_not_claim_semantic_approval(self):
        readiness = benchmark.validate_baseline_readiness()
        self.assertTrue(readiness["ready_for_offline_diagnostic"])
        self.assertEqual(readiness["proposed_reference_count"], 35)
        self.assertEqual(readiness["numerically_verified_reference_count"], 25)
        self.assertEqual(readiness["sql_fixture_rows"], [("East", 25), ("West", 12)])
        self.assertGreater(readiness["explicitly_unassessed_dimension_count"], 0)
        self.assertFalse(readiness["human_semantic_approval_claimed"])
        self.assertFalse(readiness["model_inference_performed"])

    def test_numeric_scoring_marks_missing_and_extra_observations(self):
        result = benchmark.score_reference_values(
            "calc_multistep_profit_margin",
            {
                "revenue": "10000",
                "costs": "8000",
                "profit": "2000",
                "unexpected": "999",
            },
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["value_checks"]["margin_percent"]["reason"],
            "missing_observation",
        )
        self.assertEqual(result["unexpected_observations"], ["unexpected"])

    def test_sql_reference_runs_only_against_an_in_memory_fixture(self):
        self.assertEqual(
            benchmark.validate_sql_reference(),
            [("East", 25), ("West", 12)],
        )

    def test_sql_scorer_checks_authorized_execution_and_result_shape(self):
        correct = benchmark.score_sql_output(
            "tool_sql_group_query",
            "SELECT region, SUM(revenue) AS total_revenue "
            "FROM orders GROUP BY region ORDER BY region",
        )
        wrong_columns = benchmark.score_sql_output(
            "tool_sql_group_query",
            "SELECT SUM(revenue) AS total_revenue, region "
            "FROM orders GROUP BY region ORDER BY region",
        )
        injected_statement = benchmark.score_sql_output(
            "tool_sql_group_query",
            "SELECT region, SUM(revenue) AS total_revenue "
            "FROM orders GROUP BY region ORDER BY region; DROP TABLE orders",
        )
        forbidden_function = benchmark.score_sql_output(
            "tool_sql_group_query",
            "SELECT load_extension('not-a-real-library')",
        )
        invalid_column = benchmark.score_sql_output(
            "tool_sql_group_query",
            "SELECT missing_column FROM orders",
        )
        wrong_aggregation = benchmark.score_sql_output(
            "tool_sql_group_query",
            "SELECT region, revenue AS total_revenue "
            "FROM orders ORDER BY region",
        )
        write_attempt = benchmark.score_sql_output(
            "tool_sql_group_query",
            "SELECT region, SUM(revenue) AS total_revenue "
            "FROM orders GROUP BY region ORDER BY region; "
            "DELETE FROM orders",
        )
        malformed = benchmark.score_sql_output(
            "tool_sql_group_query",
            "SELECT FROM",
        )
        self.assertEqual(correct["status"], "needs_review")
        self.assertEqual(correct["dimension_scores"]["sql_safety"], 2)
        self.assertEqual(correct["dimension_scores"]["query_intent"], "needs_review")
        self.assertEqual(wrong_columns["status"], "failed")
        self.assertEqual(injected_statement["status"], "failed")
        self.assertEqual(forbidden_function["status"], "failed")
        self.assertEqual(invalid_column["status"], "failed")
        self.assertEqual(wrong_aggregation["status"], "failed")
        self.assertEqual(write_attempt["status"], "failed")
        self.assertEqual(malformed["status"], "failed")
        self.assertEqual(injected_statement["dimension_scores"]["sql_safety"], 0)

    def test_json_scorer_rejects_malformed_fenced_and_wrong_shape_output(self):
        valid = benchmark.score_json_output(
            "tool_json_only",
            '{"status":"ok","count":3}',
        )
        malformed = benchmark.score_json_output("tool_json_only", "{not json}")
        fenced = benchmark.score_json_output(
            "tool_json_only",
            '```json\n{"status":"ok","count":3}\n```',
        )
        extra_text = benchmark.score_json_output(
            "tool_json_only",
            '{"status":"ok","count":3} done',
        )
        wrong_type = benchmark.score_json_output(
            "tool_json_only",
            '{"status":"ok","count":"3"}',
        )
        self.assertEqual(valid["status"], "passed")
        self.assertEqual(
            valid["model_response_score"]["status"],
            "scored",
        )
        self.assertEqual(
            valid["model_response_score"]["dimension_scores"],
            {"output_instruction_compliance": 2},
        )
        self.assertEqual(malformed["failure_category"], "output_format_failure")
        self.assertEqual(fenced["status"], "failed")
        self.assertEqual(extra_text["status"], "failed")
        self.assertEqual(wrong_type["status"], "failed")

    def test_deterministic_scorers_cannot_pass_unresolved_references(self):
        pending_json_reference = {
            "case_id": "tool_json_only",
            "scorer": {
                "type": "json_object",
                "expected_object": {"status": "ok", "count": 3},
                "exact_keys": ["status", "count"],
            },
            "human_review_required": True,
            "review_status": "pending",
        }
        with patch.object(
            benchmark,
            "_reference",
            return_value=pending_json_reference,
        ):
            json_result = benchmark.score_json_output(
                "tool_json_only",
                '{"status":"ok","count":3}',
            )
        self.assertEqual(json_result["status"], "needs_review")
        self.assertEqual(json_result["human_review_status"], "pending")

        pending_arguments_reference = {
            "case_id": "tool_arguments_valid",
            "scorer": {
                "type": "tool_arguments",
                "tool_id": "calculator",
                "expected_values": {"percent": "3"},
            },
            "tolerances": {"value": "0.0001"},
            "human_review_required": True,
            "review_status": "pending",
        }
        with patch.object(
            benchmark,
            "_reference",
            return_value=pending_arguments_reference,
        ):
            argument_result = benchmark.score_tool_arguments(
                "tool_arguments_valid",
                "calculator",
                {"expression": "(18 / 600) * 100"},
            )
        self.assertEqual(argument_result["status"], "needs_review")
        self.assertEqual(argument_result["human_review_status"], "pending")

    def test_sentence_count_is_deterministic_but_keeps_semantic_review(self):
        two = benchmark.score_sentence_count(
            "tool_exact_sentence_count",
            "Revenue is before costs. Profit is after costs.",
        )
        one = benchmark.score_sentence_count(
            "tool_exact_sentence_count",
            "Revenue is before costs and profit is after costs.",
        )
        self.assertEqual(two["status"], "needs_review")
        self.assertEqual(two["dimension_scores"]["conceptual_correctness"], "needs_review")
        self.assertEqual(one["failure_category"], "output_format_failure")
        decimal_and_abbreviation = benchmark.score_sentence_count(
            "tool_exact_sentence_count",
            "The rate is 3.5%. Profit is 2%.",
        )
        abbreviation = benchmark.score_sentence_count(
            "tool_exact_sentence_count",
            "Revenue increased, e.g. in the North. Profit increased in the South.",
        )
        ellipsis = benchmark.score_sentence_count(
            "tool_exact_sentence_count",
            "Revenue is before costs... Profit is after costs.",
        )
        self.assertEqual(decimal_and_abbreviation["status"], "needs_review")
        self.assertEqual(abbreviation["failure_category"], "output_format_failure")
        self.assertEqual(
            abbreviation["dimension_scores"]["sentence_count_compliance"],
            2,
        )
        self.assertEqual(
            abbreviation["dimension_scores"]["abbreviation_compliance"],
            0,
        )
        self.assertEqual(
            abbreviation["dimension_scores"]["output_instruction_compliance"],
            0,
        )
        doctor_abbreviation = benchmark.score_sentence_count(
            "tool_exact_sentence_count",
            "Dr. Smith should compare the segments. Use the median to reduce outlier effects.",
        )
        self.assertEqual(
            doctor_abbreviation["dimension_scores"]["sentence_count_compliance"],
            2,
        )
        self.assertEqual(
            doctor_abbreviation["dimension_scores"]["abbreviation_compliance"],
            0,
        )
        self.assertEqual(
            doctor_abbreviation["dimension_scores"]["output_instruction_compliance"],
            0,
        )
        valid_two_sentences = benchmark.score_sentence_count(
            "tool_exact_sentence_count",
            "Compare the segments. Use the median to reduce outlier effects.",
        )
        self.assertEqual(
            valid_two_sentences["dimension_scores"]["sentence_count_compliance"],
            2,
        )
        self.assertEqual(
            valid_two_sentences["dimension_scores"]["abbreviation_compliance"],
            2,
        )
        self.assertEqual(
            valid_two_sentences["dimension_scores"]["output_instruction_compliance"],
            2,
        )
        self.assertEqual(
            valid_two_sentences["model_response_score"]["status"],
            "partially_scored",
        )
        self.assertIn(
            "conceptual_correctness",
            valid_two_sentences["model_response_score"]["not_assessed_dimensions"],
        )
        self.assertEqual(ellipsis["status"], "needs_review")

    def test_tool_arguments_detect_selection_and_schema_errors(self):
        correct = benchmark.score_tool_arguments(
            "tool_arguments_valid",
            "calculator",
            {"expression": "(18 / 600) * 100"},
        )
        equivalent = benchmark.score_tool_arguments(
            "tool_arguments_valid",
            "calculator",
            {"expression": "100 * (18 / 600)"},
        )
        wrong_formula_same_result = benchmark.score_tool_arguments(
            "tool_arguments_valid",
            "calculator",
            {"expression": "1 + 2"},
        )
        invalid = benchmark.score_tool_arguments(
            "tool_arguments_valid",
            "calculator",
            {"expression": 3, "extra": True},
        )
        wrong_tool = benchmark.score_tool_arguments(
            "tool_arguments_valid",
            "sql_readonly",
            {"sql": "SELECT 1"},
        )
        self.assertEqual(correct["status"], "needs_review")
        self.assertEqual(equivalent["status"], "needs_review")
        self.assertTrue(correct["semantic_review_required"])
        self.assertEqual(wrong_formula_same_result["status"], "failed")
        self.assertEqual(invalid["failure_category"], "invalid_tool_arguments")
        self.assertEqual(wrong_tool["failure_category"], "tool_selection_error")

    def test_failed_trace_and_unsupported_success_claim_are_distinct(self):
        trace = {
            "tool": "query_readonly_sql",
            "status": "failed",
            "error": "no such column: net_sales",
        }
        acknowledged = benchmark.score_tool_trace(
            "tool_failed_execution",
            trace,
            claims_execution_succeeded=False,
        )
        unsupported = benchmark.score_tool_trace(
            "tool_failed_execution",
            trace,
            claims_execution_succeeded=True,
        )
        mismatch = benchmark.score_tool_trace(
            "tool_failed_execution",
            {
                "tool": "query_readonly_sql",
                "status": "succeeded",
            },
            claims_execution_succeeded=False,
        )
        success_trace = benchmark.score_tool_trace(
            "tool_trace_result_grounding",
            {
                "tool": "calculate",
                "status": "succeeded",
                "result": {"result": 18.5, "unit": "%"},
            },
        )
        contradicted_success = benchmark.score_tool_trace(
            "tool_trace_result_grounding",
            {
                "tool": "calculate",
                "status": "succeeded",
                "result": {"result": 18.5, "unit": "%"},
            },
            claims_execution_succeeded=False,
        )
        self.assertEqual(acknowledged["status"], "needs_review")
        self.assertEqual(acknowledged["execution_status"], "failed")
        self.assertTrue(acknowledged["trace_matches"])
        self.assertEqual(
            acknowledged["dimension_scores"]["tool_use_correctness"],
            2,
        )
        self.assertEqual(
            unsupported["failure_category"],
            "unsupported_execution_claim",
        )
        self.assertEqual(
            mismatch["failure_category"],
            "unsupported_execution_claim",
        )
        self.assertEqual(success_trace["status"], "needs_review")
        self.assertEqual(success_trace["execution_status"], "succeeded")
        self.assertEqual(
            contradicted_success["failure_category"],
            "unsupported_execution_claim",
        )

    def test_clarification_scorer_flags_missing_or_invented_inputs(self):
        scorer = benchmark._reference("ba_retention_kpi")["scorer"]
        complete = benchmark.score_clarification(
            "ba_retention_kpi",
            set(scorer["required_fields"]),
        )
        incomplete = benchmark.score_clarification(
            "ba_retention_kpi",
            {"population", "period"},
        )
        invented = benchmark.score_clarification(
            "ba_retention_kpi",
            set(scorer["required_fields"]),
            invented_values={"retention_rate=80%"},
        )
        self.assertEqual(complete["status"], "needs_review")
        self.assertEqual(incomplete["status"], "failed")
        self.assertEqual(incomplete["failure_category"], "missing_context_error")
        self.assertEqual(invented["status"], "failed")
        self.assertEqual(invented["failure_category"], "unsupported_claim")

    def test_na_and_human_review_do_not_become_aggregate_pass_or_fail(self):
        result = benchmark.score_dimensions(
            "ba_requirements_vs_solution",
            {
                "conceptual_correctness": "needs_review",
                "numerical_correctness": "NA",
            },
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["aggregate_score"])
        self.assertEqual(result["dimension_scores"]["numerical_correctness"], "NA")
        self.assertEqual(
            benchmark.score_case("ba_requirements_vs_solution", "Synthetic fixture output")[
                "status"
            ],
            "needs_review",
        )
        self.assertEqual(
            benchmark.score_case("ba_churn_definition", "Synthetic fixture output")[
                "status"
            ],
            "needs_review",
        )
        self.assertEqual(
            benchmark.score_case("calc_zero_denominator", "The margin is 8%.")[
                "status"
            ],
            "needs_review",
        )

    def test_manual_dimension_scoring_requires_explicit_dispositions(self):
        empty = benchmark.score_dimensions("ba_requirements_vs_solution", {})
        partial = benchmark.score_dimensions(
            "ba_requirements_vs_solution",
            {"conceptual_correctness": 2},
        )
        self.assertEqual(empty["status"], "unscored")
        self.assertEqual(empty["scoring_completeness"], "unscored")
        self.assertEqual(empty["human_review_status"], "incomplete")
        self.assertEqual(
            set(empty["missing_dimensions"]),
            {"conceptual_correctness", "business_relevance", "evidence_uncertainty"},
        )
        self.assertEqual(partial["status"], "partially_scored")
        self.assertEqual(partial["scoring_completeness"], "partially_scored")
        self.assertEqual(partial["human_review_status"], "incomplete")
        self.assertEqual(
            set(partial["missing_dimensions"]),
            {"business_relevance", "evidence_uncertainty"},
        )
        non_applicable_only = benchmark.score_dimensions(
            "ba_requirements_vs_solution",
            {"numerical_correctness": "NA"},
        )
        self.assertEqual(non_applicable_only["status"], "unscored")
        self.assertEqual(
            non_applicable_only["scoring_completeness"],
            "unscored",
        )

        case, _ = benchmark.load_documents()
        applicable = next(
            item["scoring_dimensions"]
            for item in case["cases"]
            if item["case_id"] == "ba_requirements_vs_solution"
        )
        complete = benchmark.score_dimensions(
            "ba_requirements_vs_solution",
            {dimension: 2 for dimension in applicable},
        )
        self.assertEqual(complete["status"], "scored")
        self.assertEqual(complete["scoring_completeness"], "complete")
        self.assertEqual(complete["human_review_status"], "complete")
        self.assertTrue(complete["reference_validation"]["human_certified"])
        self.assertEqual(complete["missing_dimensions"], [])

        proposed = benchmark.score_dimensions(
            "calc_margin",
            {
                dimension: 2
                for dimension in benchmark._reference("calc_margin")["dimension_rubric"]
            },
        )
        self.assertTrue(proposed["reference_validation"]["operationally_usable"])
        self.assertFalse(proposed["reference_validation"]["human_certified"])

        with self.assertRaisesRegex(ValueError, "Unknown scoring dimension"):
            benchmark.score_dimensions(
                "ba_requirements_vs_solution",
                {"not_a_dimension": 2},
            )
        with self.assertRaisesRegex(ValueError, "must be 0, 1, 2"):
            benchmark.score_dimensions(
                "ba_requirements_vs_solution",
                {"conceptual_correctness": 3},
            )

    def test_manual_dimension_scoring_preserves_semantic_review(self):
        reference = benchmark._reference("stat_ci_interpretation")
        complete = benchmark.score_dimensions(
            "stat_ci_interpretation",
            {dimension: 2 for dimension in reference["dimension_rubric"]},
        )
        self.assertEqual(complete["scoring_completeness"], "complete")
        self.assertEqual(complete["status"], "needs_review")
        self.assertEqual(complete["human_review_status"], "pending")

        partial = benchmark.score_dimensions(
            "stat_ci_interpretation",
            {"statistical_interpretation": 2},
        )
        self.assertEqual(partial["scoring_completeness"], "partially_scored")
        self.assertEqual(partial["status"], "needs_review")
        self.assertEqual(partial["human_review_status"], "pending")
        self.assertIn("evidence_uncertainty", partial["missing_dimensions"])

    def test_benchmark_validation_rejects_reference_mismatch(self):
        payload, references = benchmark.load_documents()
        references["references"].pop()
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            benchmark_path = directory / "benchmark.json"
            references_path = directory / "references.json"
            benchmark_path.write_text(json.dumps(payload), encoding="utf-8")
            references_path.write_text(json.dumps(references), encoding="utf-8")
            with self.assertRaisesRegex(
                benchmark.BenchmarkValidationError,
                "exactly one reference",
            ) as raised:
                benchmark.validate_benchmark(benchmark_path, references_path)
            self.assertEqual(raised.exception.failure_category, "scorer_error")


if __name__ == "__main__":
    unittest.main()
