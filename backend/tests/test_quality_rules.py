import copy
import os
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from app.quality_rules import (
    BUSINESS_KEY_DUPLICATE_RULE,
    CONSTANT_COLUMN_CANDIDATE_RULE,
    DEFAULT_QUALITY_RULES,
    MISSING_VALUE_CANDIDATE_RULE,
    NUMERIC_OUTLIER_CANDIDATE_RULE,
    NUMERIC_VALIDITY_RULE,
    QUALITY_RULE_ENGINE_CONFIGURATION,
    STRING_CONSISTENCY_RULE,
    TYPE_CONSISTENCY_RULE,
    QualityRule,
    QualityRuleConfigurationError,
    QualityRuleRegistry,
    RuleBudget,
    RuleFinding,
    run_quality_rules,
)


def normalization_result():
    return {
        "tables": [
            {
                "table_index": 0,
                "table_name": "orders",
                "row_count": 4,
                "schema": [
                    {"name": "region", "type": "string"},
                    {"name": "amount", "type": "double"},
                ],
                "null_value_counts": [1, 0],
                "empty_value_counts": [1, 0],
            }
        ]
    }


def parquet_rule_data(
    columns,
    *,
    physical_types,
    row_count=None,
    duplicate_row_count=0,
):
    arrow_table = pa.table(columns)
    output = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    path = output.name
    output.close()
    pq.write_table(arrow_table, path)
    actual_rows = arrow_table.num_rows if row_count is None else row_count
    schema = [
        {"name": field.name, "type": str(field.type)}
        for field in arrow_table.schema
    ]
    column_profiles = []
    null_counts = []
    empty_counts = []
    for position, field in enumerate(arrow_table.schema):
        values = arrow_table.column(position).to_pylist()
        missing_count = sum(value is None for value in values)
        empty_count = sum(
            isinstance(value, str) and value == "" for value in values
        )
        null_counts.append(missing_count)
        empty_counts.append(empty_count)
        column_profiles.append(
            {
                "name": field.name,
                "position": position,
                "physical_types": physical_types[field.name],
                "missing_value_count": missing_count,
                "empty_value_count": empty_count,
                "distinct_value_count": len(
                    {
                        (type(value).__name__, repr(value))
                        for value in values
                        if value is not None
                    }
                ),
                "distinct_count_exact": True,
            }
        )
    data = {
        "tables": [
            {
                "table_index": 0,
                "table_name": "orders",
                "row_count": actual_rows,
                "schema": schema,
                "null_value_counts": null_counts,
                "empty_value_counts": empty_counts,
            }
        ],
        "_profile_result": {
            "tables": [
                {
                    "name": "orders",
                    "row_count": actual_rows,
                    "columns": column_profiles,
                    "duplicate_row_count": duplicate_row_count,
                    "duplicate_row_count_exact": True,
                }
            ]
        },
        "_parquet_paths": {0: path},
    }
    return data, path


def configuration(rule_configurations=None, **limits):
    result = copy.deepcopy(QUALITY_RULE_ENGINE_CONFIGURATION)
    if rule_configurations is not None:
        result["enabled_rules"] = sorted(rule_configurations)
        result["rules"] = rule_configurations
        result["rule_versions"] = {
            rule_id: "1" for rule_id in rule_configurations
        }
    result.update(limits)
    return result


def fixture_finding():
    return RuleFinding(
        classification="unusual_candidate",
        title="Fixture candidate",
        description="A deterministic fixture result.",
        scope={
            "table_index": 0,
            "table_name": "orders",
            "column_position": 0,
            "column_name": "region",
        },
        affected_count=1,
        denominator_basis="rows",
        denominator_count=4,
        denominator_reason=None,
        evidence={"observed_count": 1},
        confidence=0.5,
        confidence_basis="Synthetic fixture evidence.",
        recommended_investigation="Review the row without changing source data.",
    )


def empty_config(configuration):
    if configuration != {}:
        raise QualityRuleConfigurationError("Expected an empty configuration.")
    return {}


def fixture_rule(
    rule_id,
    evaluate,
    unsupported_reason=lambda _data, _configuration: None,
):
    return QualityRule(
        rule_id=rule_id,
        version="1",
        scope="column",
        severity="low",
        prerequisites=("fixture_metadata",),
        configuration_defaults={},
        validate_configuration=empty_config,
        unsupported_reason=unsupported_reason,
        evaluate=evaluate,
    )


class QualityRuleEngineTests(unittest.TestCase):
    def test_constant_columns_are_reported_from_exact_profile_counts(self):
        data, path = parquet_rule_data(
            {
                "fixed": pa.array(["one", "one", None, ""], type=pa.string()),
                "varies": pa.array([1, 2, 3, 4], type=pa.int64()),
                "all_missing": pa.array([None, None, None, None], type=pa.string()),
            },
            physical_types={
                "fixed": ["string"],
                "varies": ["integer"],
                "all_missing": ["null"],
            },
        )
        self.addCleanup(os.unlink, path)

        first = run_quality_rules(data)
        second = run_quality_rules(data)
        self.assertEqual(first, second)
        result = next(
            item
            for item in first
            if item.rule_id == CONSTANT_COLUMN_CANDIDATE_RULE.rule_id
        )
        self.assertEqual(result.status, "Completed")
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertEqual(finding.classification, "unusual_candidate")
        self.assertEqual(finding.scope["column_name"], "fixed")
        self.assertEqual(finding.affected_count, 2)
        self.assertEqual(finding.denominator_basis, "non_empty_non_null_values")
        self.assertEqual(finding.denominator_count, 2)
        self.assertEqual(
            finding.evidence["distinct_non_empty_non_null_value_count"],
            1,
        )
        self.assertNotIn("one", str(finding.evidence))

    def test_constant_column_rule_does_not_flag_varying_or_single_observations(self):
        data, path = parquet_rule_data(
            {
                "varies": pa.array(["a", "b", "a"], type=pa.string()),
                "single_observation": pa.array(["a", None, None], type=pa.string()),
                "empty_only": pa.array(["", "", None], type=pa.string()),
            },
            physical_types={
                "varies": ["string"],
                "single_observation": ["string"],
                "empty_only": ["string"],
            },
        )
        self.addCleanup(os.unlink, path)
        rule_id = CONSTANT_COLUMN_CANDIDATE_RULE.rule_id
        result = run_quality_rules(
            data,
            configuration=configuration({rule_id: {}}),
        )[0]
        self.assertEqual(result.status, "Completed")
        self.assertEqual(result.findings, ())

    def test_constant_column_rule_requires_exact_aligned_profile_metadata(self):
        data, path = parquet_rule_data(
            {"fixed": pa.array(["x", "x"], type=pa.string())},
            physical_types={"fixed": ["string"]},
        )
        self.addCleanup(os.unlink, path)
        rule_id = CONSTANT_COLUMN_CANDIDATE_RULE.rule_id

        for profile in (
            None,
            {
                **data["_profile_result"],
                "tables": [
                    {
                        **data["_profile_result"]["tables"][0],
                        "columns": [
                            {
                                **data["_profile_result"]["tables"][0]["columns"][0],
                                "distinct_count_exact": False,
                            }
                        ],
                    }
                ],
            },
        ):
            candidate_data = dict(data)
            if profile is None:
                candidate_data.pop("_profile_result")
            else:
                candidate_data["_profile_result"] = profile
            result = run_quality_rules(
                candidate_data,
                configuration=configuration({rule_id: {}}),
            )[0]
            self.assertEqual(result.status, "NotApplicable")
            self.assertEqual(result.findings, ())

        misaligned = copy.deepcopy(data)
        misaligned["_profile_result"]["tables"][0]["columns"][0]["name"] = "other"
        result = run_quality_rules(
            misaligned,
            configuration=configuration({rule_id: {}}),
        )[0]
        self.assertEqual(result.status, "NotApplicable")

    def test_constant_column_rule_obeys_shared_comparison_budget(self):
        data, path = parquet_rule_data(
            {
                "fixed_a": pa.array(["x", "x"], type=pa.string()),
                "fixed_b": pa.array(["y", "y"], type=pa.string()),
            },
            physical_types={"fixed_a": ["string"], "fixed_b": ["string"]},
        )
        self.addCleanup(os.unlink, path)
        result = run_quality_rules(
            data,
            configuration=configuration(
                {CONSTANT_COLUMN_CANDIDATE_RULE.rule_id: {}},
                candidate_comparisons=1,
            ),
        )[0]
        self.assertEqual(result.status, "BudgetExhausted")
        self.assertEqual(result.findings, ())

    def test_default_rule_reports_missing_values_as_candidates(self):
        result = run_quality_rules(normalization_result())
        missing_result = next(
            item for item in result if item.rule_id == "generic.missing_value_candidates"
        )
        self.assertEqual(missing_result.rule_version, "1")
        self.assertEqual(missing_result.status, "Completed")
        self.assertEqual(missing_result.budget_used, 2)
        self.assertEqual(missing_result.affected_count, 2)
        self.assertEqual(missing_result.denominator_count, 4)
        self.assertEqual(len(missing_result.findings), 1)
        finding = missing_result.findings[0]
        self.assertEqual(finding.classification, "unusual_candidate")
        self.assertEqual(finding.evidence, {
            "null_count": 1,
            "empty_string_count": 1,
        })
        self.assertEqual(finding.denominator_count, 4)
        self.assertIn("depends on the intended use", finding.description)

    def test_missing_rule_counts_tagged_json_source_nulls_and_empty_strings(self):
        data = {
            "tables": [
                {
                    "table_index": 0,
                    "table_name": None,
                    "row_count": 3,
                    "schema": [{"name": "value", "type": "string"}],
                    "null_value_counts": [0],
                    "empty_value_counts": [0],
                    "logical_null_value_counts": [2],
                    "logical_empty_value_counts": [1],
                }
            ]
        }
        result = run_quality_rules(
            data,
            configuration=configuration(
                {
                    MISSING_VALUE_CANDIDATE_RULE.rule_id: {
                        "include_empty_strings": True,
                        "include_null_values": True,
                    }
                },
            ),
        )[0]
        self.assertEqual(result.status, "Completed")
        self.assertEqual(result.findings[0].affected_count, 3)
        self.assertEqual(
            result.findings[0].evidence,
            {"null_count": 2, "empty_string_count": 1},
        )

    def test_default_config_is_explicit_and_execution_is_deterministic(self):
        self.assertEqual(
            tuple(rule.rule_id for rule in DEFAULT_QUALITY_RULES),
            tuple(sorted(rule.rule_id for rule in DEFAULT_QUALITY_RULES)),
        )
        data = normalization_result()
        first = run_quality_rules(data)
        second = run_quality_rules(data)
        self.assertEqual(first, second)
        self.assertEqual(
            MISSING_VALUE_CANDIDATE_RULE.configuration_defaults,
            {
                "include_empty_strings": True,
                "include_null_values": True,
            },
        )

    def test_rules_report_deterministic_type_string_and_numeric_candidates(self):
        data, path = parquet_rule_data(
            {
                "region": pa.array(["North", " North ", "x  y", None], type=pa.string()),
                "amount": pa.array([1.0, -2.0, float("inf"), 4.5]),
                "key": pa.array(["a", "a", None, "b"], type=pa.string()),
            },
            physical_types={
                "region": ["integer", "string"],
                "amount": ["number"],
                "key": ["string"],
            },
        )
        self.addCleanup(os.unlink, path)
        first = run_quality_rules(data)
        second = run_quality_rules(data)
        self.assertEqual(first, second)
        results = {item.rule_id: item for item in first}

        type_result = results[TYPE_CONSISTENCY_RULE.rule_id]
        self.assertEqual(type_result.status, "Completed")
        self.assertEqual(type_result.findings[0].classification, "suspected_issue")
        self.assertEqual(
            type_result.findings[0].evidence["source_physical_types"],
            "integer, string",
        )

        string_result = results[STRING_CONSISTENCY_RULE.rule_id]
        string_finding = string_result.findings[0]
        self.assertEqual(string_finding.classification, "unusual_candidate")
        self.assertEqual(string_finding.affected_count, 2)
        self.assertEqual(string_finding.denominator_count, 4)
        self.assertEqual(
            string_finding.evidence["leading_or_trailing_whitespace_count"],
            1,
        )
        self.assertEqual(string_finding.evidence["repeated_whitespace_count"], 1)

        numeric_result = results[NUMERIC_VALIDITY_RULE.rule_id]
        numeric_finding = numeric_result.findings[0]
        self.assertEqual(numeric_finding.classification, "suspected_issue")
        self.assertEqual(numeric_finding.affected_count, 1)
        self.assertEqual(numeric_finding.evidence["non_finite_count"], 1)
        self.assertEqual(numeric_finding.denominator_count, 4)
        self.assertNotIn(-2, numeric_finding.evidence.values())

    def test_valid_values_do_not_flag_negative_numbers_or_regular_strings(self):
        data, path = parquet_rule_data(
            {
                "label": pa.array(["north", "south"], type=pa.string()),
                "amount": pa.array([-2.0, 3.5]),
            },
            physical_types={
                "label": ["string"],
                "amount": ["number"],
            },
            row_count=2,
        )
        self.addCleanup(os.unlink, path)
        results = {item.rule_id: item for item in run_quality_rules(data)}
        self.assertEqual(results[STRING_CONSISTENCY_RULE.rule_id].findings, ())
        self.assertEqual(results[NUMERIC_VALIDITY_RULE.rule_id].findings, ())
        self.assertEqual(results[TYPE_CONSISTENCY_RULE.rule_id].findings, ())

    def test_iqr_outlier_candidates_are_deterministic_and_advisory(self):
        data, path = parquet_rule_data(
            {"amount": pa.array([1, 2, 3, 4, 100], type=pa.int64())},
            physical_types={"amount": ["integer"]},
            row_count=5,
        )
        self.addCleanup(os.unlink, path)
        first = run_quality_rules(data)
        second = run_quality_rules(data)
        self.assertEqual(first, second)
        result = next(
            item
            for item in first
            if item.rule_id == NUMERIC_OUTLIER_CANDIDATE_RULE.rule_id
        )
        self.assertEqual(result.status, "Completed")
        finding = result.findings[0]
        self.assertEqual(finding.classification, "unusual_candidate")
        self.assertEqual(result.severity, "info")
        self.assertEqual(finding.affected_count, 1)
        self.assertEqual(finding.denominator_count, 5)
        self.assertEqual(finding.evidence["method"], "iqr")
        self.assertEqual(finding.evidence["example_refs"], [{"row_index": 4}])

    def test_z_score_is_explicit_and_small_or_constant_samples_are_not_flagged(self):
        data, path = parquet_rule_data(
            {"amount": pa.array([0, 0, 0, 0, 20], type=pa.int64())},
            physical_types={"amount": ["integer"]},
            row_count=5,
        )
        self.addCleanup(os.unlink, path)
        rule_id = NUMERIC_OUTLIER_CANDIDATE_RULE.rule_id
        z_score_config = {
            "method": "z_score",
            "threshold": 1.5,
            "min_samples": 4,
        }
        result = run_quality_rules(
            data,
            configuration=configuration({rule_id: z_score_config}),
        )[0]
        self.assertEqual(result.findings[0].evidence["method"], "z_score")
        self.assertEqual(result.findings[0].affected_count, 1)

        small_data, small_path = parquet_rule_data(
            {"amount": pa.array([0, 20], type=pa.int64())},
            physical_types={"amount": ["integer"]},
            row_count=2,
        )
        self.addCleanup(os.unlink, small_path)
        small = run_quality_rules(
            small_data,
            configuration=configuration({rule_id: z_score_config}),
        )[0]
        self.assertEqual(small.findings, ())

        constant_data, constant_path = parquet_rule_data(
            {"amount": pa.array([7, 7, 7, 7], type=pa.int64())},
            physical_types={"amount": ["integer"]},
            row_count=4,
        )
        self.addCleanup(os.unlink, constant_path)
        constant = run_quality_rules(
            constant_data,
            configuration=configuration({rule_id: z_score_config}),
        )[0]
        self.assertEqual(constant.findings, ())

    def test_numeric_outlier_configuration_is_validated(self):
        rule_id = NUMERIC_OUTLIER_CANDIDATE_RULE.rule_id
        for invalid in (
            {"method": "iqr", "threshold": 0, "min_samples": 4},
            {"method": "z_score", "threshold": float("nan"), "min_samples": 4},
            {"method": "mad", "threshold": 3, "min_samples": 4},
            {"method": "iqr", "threshold": 1.5, "min_samples": True},
            {"method": "iqr", "threshold": 1.5, "min_samples": 4, "extra": 1},
        ):
            with self.subTest(configuration=invalid), self.assertRaises(
                QualityRuleConfigurationError
            ):
                run_quality_rules(
                    normalization_result(),
                    configuration=configuration({rule_id: invalid}),
                )

    def test_iqr_storage_obeys_shared_memory_budget(self):
        data, path = parquet_rule_data(
            {"amount": pa.array([1, 2, 3, 4, 100], type=pa.int64())},
            physical_types={"amount": ["integer"]},
            row_count=5,
        )
        self.addCleanup(os.unlink, path)
        with patch("app.quality_rules.MAX_RULE_MEMORY_BYTES", 1):
            result = run_quality_rules(
                data,
                configuration=configuration(
                    {
                        NUMERIC_OUTLIER_CANDIDATE_RULE.rule_id: (
                            NUMERIC_OUTLIER_CANDIDATE_RULE.configuration_defaults
                        )
                    }
                ),
            )[0]
        self.assertEqual(result.status, "BudgetExhausted")
        self.assertEqual(result.findings, ())

    def test_type_conflicts_are_reported_against_normalized_string_types(self):
        data, path = parquet_rule_data(
            {"value": pa.array(["42"], type=pa.string())},
            physical_types={"value": ["integer"]},
            row_count=1,
        )
        self.addCleanup(os.unlink, path)
        result = run_quality_rules(
            data,
            configuration=configuration({TYPE_CONSISTENCY_RULE.rule_id: {}}),
        )[0]
        self.assertEqual(result.status, "Completed")
        self.assertEqual(result.findings[0].classification, "suspected_issue")

    def test_exact_duplicates_use_the_bounded_normalized_row_scan(self):
        data, path = parquet_rule_data(
            {"label": pa.array(["x", "x"], type=pa.string())},
            physical_types={"label": ["string"]},
            row_count=2,
            duplicate_row_count=1,
        )
        self.addCleanup(os.unlink, path)
        data.pop("_profile_result")
        results = {item.rule_id: item for item in run_quality_rules(data)}
        duplicate = results["generic.exact_duplicate_rows"]
        self.assertEqual(duplicate.status, "Completed")
        self.assertEqual(duplicate.findings[0].classification, "unusual_candidate")
        self.assertEqual(duplicate.findings[0].affected_count, 1)

        data["_parquet_paths"] = {}
        unsupported = {item.rule_id: item for item in run_quality_rules(data)}
        self.assertEqual(unsupported["generic.exact_duplicate_rows"].status, "NotApplicable")

    def test_business_key_duplicates_require_an_explicit_key_and_ignore_missing_keys(self):
        data, path = parquet_rule_data(
            {
                "key": pa.array(["a", "a", None, "b"], type=pa.string()),
                "amount": pa.array([1, 2, 3, 4], type=pa.int64()),
            },
            physical_types={"key": ["string"], "amount": ["integer"]},
        )
        self.addCleanup(os.unlink, path)
        rule_id = BUSINESS_KEY_DUPLICATE_RULE.rule_id
        unconfigured = run_quality_rules(
            data,
            configuration=configuration({rule_id: {"keys": []}}),
        )
        self.assertEqual(unconfigured[0].status, "NotApplicable")

        configured = run_quality_rules(
            data,
            configuration=configuration(
                {rule_id: {"keys": [{"table_index": 0, "columns": ["key"]}]}}
            ),
        )[0]
        self.assertEqual(configured.status, "Completed")
        finding = configured.findings[0]
        self.assertEqual(finding.affected_count, 1)
        self.assertEqual(finding.denominator_count, 3)
        self.assertEqual(finding.evidence["rows_with_null_key_values"], 1)
        self.assertEqual(finding.classification, "unusual_candidate")

    def test_invalid_business_key_configuration_is_rejected(self):
        with self.assertRaises(QualityRuleConfigurationError):
            run_quality_rules(
                normalization_result(),
                configuration=configuration(
                    {
                        BUSINESS_KEY_DUPLICATE_RULE.rule_id: {
                            "keys": [{"table_index": 0, "columns": ["key", "key"]}]
                        }
                    }
                ),
            )

    def test_business_key_with_unknown_columns_is_not_applicable(self):
        data, path = parquet_rule_data(
            {
                "key": pa.array(["a"], type=pa.string()),
                "amount": pa.array([1], type=pa.int64()),
            },
            physical_types={"key": ["string"], "amount": ["integer"]},
            row_count=1,
        )
        self.addCleanup(os.unlink, path)
        result = run_quality_rules(
            data,
            configuration=configuration(
                {
                    BUSINESS_KEY_DUPLICATE_RULE.rule_id: {
                        "keys": [
                            {"table_index": 0, "columns": ["not_in_schema"]}
                        ]
                    }
                }
            ),
        )[0]
        self.assertEqual(result.status, "NotApplicable")
        self.assertIn("column does not exist", result.reason)

    def test_value_scanning_rules_are_unsupported_without_verified_parquet(self):
        data = normalization_result()
        data["_parquet_paths"] = {}
        results = {item.rule_id: item for item in run_quality_rules(data)}
        self.assertEqual(results[STRING_CONSISTENCY_RULE.rule_id].status, "NotApplicable")
        self.assertEqual(results[NUMERIC_VALIDITY_RULE.rule_id].status, "NotApplicable")

    def test_value_scanning_stops_at_the_shared_comparison_budget(self):
        data, path = parquet_rule_data(
            {"label": pa.array(["one", "two"], type=pa.string())},
            physical_types={"label": ["string"]},
            row_count=2,
        )
        self.addCleanup(os.unlink, path)
        rule_id = STRING_CONSISTENCY_RULE.rule_id
        result = run_quality_rules(
            data,
            configuration=configuration(
                {rule_id: {}},
                candidate_comparisons=1,
            ),
        )[0]
        self.assertEqual(result.status, "BudgetExhausted")
        self.assertEqual(result.findings, ())

    def test_oversized_parquet_is_not_scanned(self):
        data, path = parquet_rule_data(
            {"label": pa.array(["one"], type=pa.string())},
            physical_types={"label": ["string"]},
            row_count=1,
        )
        self.addCleanup(os.unlink, path)
        with patch("app.quality_rules.MAX_RULE_INPUT_BYTES", 1):
            result = run_quality_rules(
                data,
                configuration=configuration(
                    {STRING_CONSISTENCY_RULE.rule_id: {}}
                ),
            )[0]
        self.assertEqual(result.status, "NotApplicable")
        self.assertIn("input limit", result.reason)

    def test_registry_orders_rules_independently_of_registration_order(self):
        no_findings = lambda _data, _configuration, _budget: ()
        registry = QualityRuleRegistry(
            (
                fixture_rule("fixture.zeta", no_findings),
                fixture_rule("fixture.alpha", no_findings),
            )
        )
        result = run_quality_rules(
            normalization_result(),
            registry=registry,
            configuration=configuration({"fixture.zeta": {}, "fixture.alpha": {}}),
        )
        self.assertEqual(
            [item.rule_id for item in result],
            ["fixture.alpha", "fixture.zeta"],
        )

    def test_empty_selection_is_explicit_and_runs_no_rules(self):
        selected = configuration({})
        self.assertEqual(
            run_quality_rules(
                normalization_result(),
                configuration=selected,
            ),
            [],
        )

    def test_unsupported_prerequisites_are_explicit(self):
        rule = fixture_rule(
            "fixture.unsupported",
            lambda _data, _configuration, _budget: (),
            unsupported_reason=lambda _data, _configuration: (
                "The fixture input is unsupported."
            ),
        )
        result = run_quality_rules(
            normalization_result(),
            registry=QualityRuleRegistry((rule,)),
            configuration=configuration({"fixture.unsupported": {}}),
        )
        self.assertEqual(result[0].status, "NotApplicable")
        self.assertEqual(
            result[0].reason,
            "The fixture input is unsupported.",
        )

    def test_rule_failure_is_reported_without_aborting_other_rules(self):
        def fail(_data, _configuration, _budget):
            raise RuntimeError("fixture input value must not leak")

        registry = QualityRuleRegistry(
            (
                fixture_rule("fixture.failure", fail),
                fixture_rule(
                    "fixture.success",
                    lambda _data, _configuration, _budget: (fixture_finding(),),
                ),
            )
        )
        results = run_quality_rules(
            normalization_result(),
            registry=registry,
            configuration=configuration(
                {"fixture.failure": {}, "fixture.success": {}}
            ),
        )
        self.assertEqual(
            [(item.rule_id, item.status) for item in results],
            [
                ("fixture.failure", "Failed"),
                ("fixture.success", "Completed"),
            ],
        )
        self.assertNotIn("fixture input value", results[0].reason)
        self.assertEqual(len(results[1].findings), 1)

    def test_invalid_rule_configuration_is_rejected_before_execution(self):
        invalid_configs = (
            {"include_null_values": 1, "include_empty_strings": True},
            {"include_null_values": True},
            {"include_null_values": True, "include_empty_strings": True, "extra": False},
        )
        for invalid in invalid_configs:
            with self.subTest(configuration=invalid), self.assertRaises(
                QualityRuleConfigurationError
            ):
                run_quality_rules(
                    normalization_result(),
                    configuration=configuration(
                        {
                            "generic.missing_value_candidates": invalid,
                        }
                    ),
                )

    def test_comparison_budget_exhaustion_discards_partial_rule_results(self):
        def over_budget(_data, _configuration, budget: RuleBudget):
            budget.consume_comparisons(2)
            yield fixture_finding()

        rule = fixture_rule("fixture.bounded", over_budget)
        result = run_quality_rules(
            normalization_result(),
            registry=QualityRuleRegistry((rule,)),
            configuration=configuration(
                {"fixture.bounded": {}},
                candidate_comparisons=1,
            ),
        )
        self.assertEqual(result[0].status, "BudgetExhausted")
        self.assertEqual(result[0].findings, ())

    def test_finding_and_result_size_limits_are_enforced(self):
        rule = fixture_rule(
            "fixture.result_limit",
            lambda _data, _configuration, _budget: (fixture_finding(),),
        )
        for limits in (
            {"max_findings": 0},
            {"max_report_bytes": 1},
        ):
            with self.subTest(limits=limits):
                result = run_quality_rules(
                    normalization_result(),
                    registry=QualityRuleRegistry((rule,)),
                    configuration=configuration(
                        {"fixture.result_limit": {}},
                        **limits,
                    ),
                )
                self.assertEqual(result[0].status, "BudgetExhausted")
                self.assertEqual(result[0].findings, ())


if __name__ == "__main__":
    unittest.main()
