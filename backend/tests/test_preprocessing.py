import copy
import json
import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import pyarrow as pa

from app.preprocessing import (
    DEFAULT_TRANSFORMATION_REGISTRY,
    TRANSFORMATION_CATEGORIES,
    TransformationOperation,
    TransformationRegistry,
    TransformationRegistryError,
    _StringNormalization,
)


class FixtureExecutor:
    def fit(self, data, *, configuration, context=None):
        _ = (data, configuration, context)
        return None

    def transform(self, data, *, configuration, context=None):
        _ = (configuration, context)
        return data


def operation(operation_id, **overrides):
    values = {
        "operation_id": operation_id,
        "version": "1",
        "category": "DA",
        "status": "implemented",
        "input_requirements": ("normalized dataset",),
        "output_requirements": ("validated normalized dataset",),
        "prerequisites": ("normalized_dataset",),
        "configuration_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "configuration_defaults": {},
        "requires_fit": False,
        "changes_rows": False,
        "changes_columns": False,
        "changes_values": True,
        "required_approvals": (),
        "resource_limits": {"max_rows": 1000},
        "catalog_reference": None,
    }
    values.update(overrides)
    return TransformationOperation(**values)


class TransformationRegistryTests(unittest.TestCase):
    def test_default_registry_contains_only_implemented_operations(self):
        operations = DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
        self.assertEqual(
            [item.operation_id for item in operations],
            [
                "ba.business_rule_filter",
                "ba.business_rule_validation",
                "ba.category_mapping",
                "ba.derived_business_metric",
                "da.missing_value_handling",
                "da.numeric_outlier_handling",
                "da.numeric_scaling",
                "da.string_normalization",
            ],
        )
        self.assertEqual({item.status for item in operations}, {"implemented"})
        self.assertEqual(
            {item.category for item in operations},
            {"BA", "DA"},
        )
        self.assertEqual(
            TRANSFORMATION_CATEGORIES,
            frozenset({"BA", "DA", "structural"}),
        )

    def test_registered_components_dispatch_with_explicit_prerequisites_and_approval(self):
        for operation in DEFAULT_TRANSFORMATION_REGISTRY.list_operations():
            with self.subTest(operation_id=operation.operation_id):
                executor = DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
                    operation.operation_id,
                    configuration=operation.configuration_defaults,
                    available_prerequisites=operation.prerequisites,
                    approved_approvals=operation.required_approvals,
                )
                self.assertTrue(callable(executor.fit))
                self.assertTrue(callable(executor.transform))

        with self.assertRaisesRegex(
            TransformationRegistryError,
            "Unknown transformation",
        ):
            DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor("da.unknown")

    def test_registered_components_require_approval_and_reject_invalid_configuration(self):
        operation = next(
            item
            for item in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
            if item.operation_id == "da.string_normalization"
        )
        with self.assertRaisesRegex(
            TransformationRegistryError,
            "approvals are missing",
        ):
            DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
                operation.operation_id,
                configuration=operation.configuration_defaults,
                available_prerequisites=operation.prerequisites,
                approved_approvals=(),
            )
        with self.assertRaisesRegex(
            TransformationRegistryError,
            "configuration does not match",
        ):
            DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
                operation.operation_id,
                configuration={"unknown": True},
                available_prerequisites=operation.prerequisites,
                approved_approvals=operation.required_approvals,
            )


class DATransformationTests(unittest.TestCase):
    @staticmethod
    def operation(operation_id):
        return next(
            item
            for item in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
            if item.operation_id == operation_id
        )

    def executor(self, operation_id, configuration):
        operation = self.operation(operation_id)
        return DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
            operation_id,
            configuration=configuration,
            available_prerequisites=operation.prerequisites,
            approved_approvals=operation.required_approvals,
        )

    def test_string_normalization_is_explicit_deterministic_and_non_mutating(self):
        table = pa.table(
            {
                "category": [" Retail ", None, "", "WHOLESALE"],
                "customer_id": [1, 2, 3, 4],
            }
        )
        original = table
        executor = self.executor(
            "da.string_normalization",
            {
                "columns": ["category"],
                "trim": True,
                "case": "lower",
                "empty_strings_as_missing": True,
            },
        )

        executor.fit(
            table,
            configuration={
                "columns": ["category"],
                "trim": True,
                "case": "lower",
                "empty_strings_as_missing": True,
            },
        )
        first = executor.transform(
            table,
            configuration={
                "columns": ["category"],
                "trim": True,
                "case": "lower",
                "empty_strings_as_missing": True,
            },
        )
        second = executor.transform(
            table,
            configuration={
                "columns": ["category"],
                "trim": True,
                "case": "lower",
                "empty_strings_as_missing": True,
            },
        )
        self.assertEqual(
            first.column("category").to_pylist(),
            ["retail", None, None, "wholesale"],
        )
        self.assertEqual(first, second)
        self.assertEqual(first.column("customer_id").to_pylist(), [1, 2, 3, 4])
        self.assertEqual(table, original)
        self.assertEqual(
            table.column("category").to_pylist(),
            [" Retail ", None, "", "WHOLESALE"],
        )

        upper_config = {"columns": ["category"], "case": "upper"}
        upper = self.executor("da.string_normalization", upper_config)
        self.assertEqual(
            upper.transform(
                table,
                configuration=upper_config,
            ).column("category").to_pylist(),
            [" RETAIL ", None, "", "WHOLESALE"],
        )

    def test_string_normalization_rejects_missing_columns_and_non_string_types(self):
        table = pa.table({"amount": [1, 2]})
        executor = self.executor(
            "da.string_normalization",
            {"columns": ["amount"]},
        )
        with self.assertRaisesRegex(TransformationRegistryError, "string type"):
            executor.transform(table, configuration={"columns": ["amount"]})

        missing = self.executor(
            "da.string_normalization",
            {"columns": ["absent"]},
        )
        with self.assertRaisesRegex(TransformationRegistryError, "not found"):
            missing.transform(table, configuration={"columns": ["absent"]})

    def test_missing_value_leave_drop_and_constant_strategies(self):
        table = pa.table(
            {
                "amount": pa.array([1.0, None, 3.0]),
                "category": ["Retail", "", None],
            }
        )
        leave_config = {"columns": ["amount"], "strategy": "leave"}
        leave = self.executor("da.missing_value_handling", leave_config)
        leave_state = leave.fit(table, configuration=leave_config)
        self.assertEqual(
            leave.transform(
                table,
                configuration=leave_config,
                context=leave_state,
            ),
            table,
        )

        drop_config = {
            "columns": ["amount", "category"],
            "strategy": "drop_rows",
            "empty_strings_as_missing": True,
        }
        drop = self.executor("da.missing_value_handling", drop_config)
        drop_state = drop.fit(table, configuration=drop_config)
        dropped = drop.transform(
            table,
            configuration=drop_config,
            context=drop_state,
        )
        self.assertEqual(dropped.num_rows, 1)
        self.assertEqual(dropped.column("amount").to_pylist(), [1.0])

        constant_config = {
            "columns": ["amount"],
            "strategy": "fill_constant",
            "constant_number": 0,
        }
        constant = self.executor("da.missing_value_handling", constant_config)
        constant_state = constant.fit(table, configuration=constant_config)
        filled = constant.transform(
            table,
            configuration=constant_config,
            context=constant_state,
        )
        self.assertEqual(filled.column("amount").to_pylist(), [1.0, 0.0, 3.0])

        string_config = {
            "columns": ["category"],
            "strategy": "fill_constant",
            "empty_strings_as_missing": True,
            "constant_string": "unknown",
        }
        string_fill = self.executor("da.missing_value_handling", string_config)
        string_state = string_fill.fit(table, configuration=string_config)
        self.assertEqual(
            string_fill.transform(
                table,
                configuration=string_config,
                context=string_state,
            ).column("category").to_pylist(),
            ["Retail", "unknown", "unknown"],
        )

    def test_numeric_imputation_learns_only_from_training_data(self):
        training = pa.table({"amount": pa.array([100.0, None, 120.0])})
        evaluation = pa.table({"amount": pa.array([None, 105.0])})
        config = {"columns": ["amount"], "strategy": "mean"}
        executor = self.executor("da.missing_value_handling", config)
        fitted = executor.fit(training, configuration=config)
        repeated_fit = executor.fit(training, configuration=config)
        self.assertEqual(fitted, repeated_fit)
        self.assertEqual(fitted["parameters"], {"amount": 110.0})
        executor.fit(
            pa.table({"amount": pa.array([1000.0, 2000.0])}),
            configuration=config,
        )

        first = executor.transform(
            evaluation,
            configuration=config,
            context=fitted,
        )
        second = executor.transform(
            evaluation,
            configuration=config,
            context=fitted,
        )
        self.assertEqual(first.column("amount").to_pylist(), [110.0, 105.0])
        self.assertEqual(first, second)
        self.assertEqual(training.column("amount").to_pylist(), [100.0, None, 120.0])
        self.assertEqual(evaluation.column("amount").to_pylist(), [None, 105.0])
        mutated_state = copy.deepcopy(fitted)
        mutated_state["parameters"]["amount"] = 1500.0
        with self.assertRaisesRegex(TransformationRegistryError, "does not match"):
            executor.transform(
                evaluation,
                configuration=config,
                context=mutated_state,
            )

    def test_median_and_most_frequent_imputation_are_deterministic(self):
        training = pa.table(
            {
                "amount": pa.array([1.0, 4.0, 9.0, None]),
                "category": ["z", "a", "z", None],
            }
        )
        evaluation = pa.table(
            {
                "amount": pa.array([None], type=pa.float64()),
                "category": pa.array([None], type=pa.string()),
            }
        )
        median_config = {"columns": ["amount"], "strategy": "median"}
        median = self.executor("da.missing_value_handling", median_config)
        median_state = median.fit(training, configuration=median_config)
        self.assertEqual(median_state["parameters"]["amount"], 4.0)
        self.assertEqual(
            median.transform(
                evaluation,
                configuration=median_config,
                context=median_state,
            ).column("amount").to_pylist(),
            [4.0],
        )

        mode_config = {"columns": ["category"], "strategy": "most_frequent"}
        mode = self.executor("da.missing_value_handling", mode_config)
        mode_state = mode.fit(training, configuration=mode_config)
        self.assertEqual(mode_state["parameters"]["category"], "z")
        self.assertEqual(
            mode.transform(
                evaluation,
                configuration=mode_config,
                context=mode_state,
            ).column("category").to_pylist(),
            ["z"],
        )

    def test_imputation_rejects_unfitted_empty_all_null_and_unsupported_inputs(self):
        config = {"columns": ["amount"], "strategy": "mean"}
        executor = self.executor("da.missing_value_handling", config)
        evaluation = pa.table({"amount": pa.array([None], type=pa.float64())})
        with self.assertRaisesRegex(TransformationRegistryError, "fitted"):
            executor.transform(evaluation, configuration=config)
        for training in (
            pa.table({"amount": pa.array([], type=pa.float64())}),
            pa.table({"amount": pa.array([None], type=pa.float64())}),
        ):
            with self.subTest(rows=training.num_rows):
                with self.assertRaisesRegex(TransformationRegistryError, "no observed"):
                    executor.fit(training, configuration=config)

        string_table = pa.table({"amount": ["1", None]})
        with self.assertRaisesRegex(TransformationRegistryError, "integer or floating"):
            executor.fit(string_table, configuration=config)

        mode_config = {"columns": ["amount"], "strategy": "most_frequent"}
        mode = self.executor("da.missing_value_handling", mode_config)
        with self.assertRaisesRegex(TransformationRegistryError, "string or boolean"):
            mode.fit(pa.table({"amount": [1, 2]}), configuration=mode_config)

        empty_mode_config = {
            "columns": ["category"],
            "strategy": "most_frequent",
            "empty_strings_as_missing": True,
        }
        empty_mode = self.executor(
            "da.missing_value_handling",
            empty_mode_config,
        )
        all_missing = pa.table(
            {"category": pa.array(["", None], type=pa.string())}
        )
        with self.assertRaisesRegex(TransformationRegistryError, "no observed"):
            empty_mode.fit(all_missing, configuration=empty_mode_config)

    def test_imputation_rejects_missing_or_incompatible_constant_config(self):
        table = pa.table({"amount": pa.array([None], type=pa.float64())})
        missing_constant = {
            "columns": ["amount"],
            "strategy": "fill_constant",
        }
        executor = self.executor("da.missing_value_handling", missing_constant)
        with self.assertRaisesRegex(TransformationRegistryError, "exactly one"):
            executor.fit(table, configuration=missing_constant)

        incompatible = {
            "columns": ["amount"],
            "strategy": "fill_constant",
            "constant_string": "zero",
        }
        executor = self.executor("da.missing_value_handling", incompatible)
        with self.assertRaisesRegex(TransformationRegistryError, "incompatible"):
            executor.fit(table, configuration=incompatible)

        fractional_integer = {
            "columns": ["amount"],
            "strategy": "fill_constant",
            "constant_number": 0.5,
        }
        integer_table = pa.table({"amount": pa.array([None], type=pa.int64())})
        executor = self.executor("da.missing_value_handling", fractional_integer)
        with self.assertRaisesRegex(TransformationRegistryError, "incompatible"):
            executor.fit(integer_table, configuration=fractional_integer)

    def test_outlier_detection_is_not_implicit_and_clipping_is_explicit(self):
        table = pa.table({"amount": pa.array([1.0, 100.0, None])})
        leave_config = {"columns": ["amount"], "strategy": "leave"}
        leave = self.executor("da.numeric_outlier_handling", leave_config)
        leave_state = leave.fit(table, configuration=leave_config)
        self.assertEqual(
            leave.transform(
                table,
                configuration=leave_config,
                context=leave_state,
            ),
            table,
        )

        config = {
            "columns": ["amount"],
            "strategy": "clip_bounds",
            "lower_bound": 0,
            "upper_bound": 10,
        }
        executor = self.executor("da.numeric_outlier_handling", config)
        state = executor.fit(table, configuration=config)
        clipped = executor.transform(table, configuration=config, context=state)
        self.assertEqual(clipped.column("amount").to_pylist(), [1.0, 10.0, None])
        self.assertEqual(table.column("amount").to_pylist(), [1.0, 100.0, None])

    def test_iqr_bounds_are_fit_on_training_and_reused_on_evaluation(self):
        training = pa.table(
            {"amount": pa.array([0.0, 1.0, 2.0, 3.0, 100.0])}
        )
        evaluation = pa.table({"amount": pa.array([100.0, None])})
        config = {
            "columns": ["amount"],
            "strategy": "iqr_clip",
            "iqr_multiplier": 1.5,
        }
        executor = self.executor("da.numeric_outlier_handling", config)
        state = executor.fit(training, configuration=config)
        self.assertEqual(
            state["parameters"]["amount"],
            {"lower": -2.0, "upper": 6.0},
        )
        first = executor.transform(evaluation, configuration=config, context=state)
        second = executor.transform(evaluation, configuration=config, context=state)
        self.assertEqual(first.column("amount").to_pylist(), [6.0, None])
        self.assertEqual(first, second)
        self.assertEqual(
            training.column("amount").to_pylist(),
            [0.0, 1.0, 2.0, 3.0, 100.0],
        )

    def test_outlier_handling_rejects_bad_bounds_insufficient_rows_and_nonfinite_values(self):
        invalid_bounds = {
            "columns": ["amount"],
            "strategy": "clip_bounds",
            "lower_bound": 5,
            "upper_bound": 2,
        }
        executor = self.executor("da.numeric_outlier_handling", invalid_bounds)
        with self.assertRaisesRegex(TransformationRegistryError, "lower < upper"):
            executor.fit(pa.table({"amount": [1, 2]}), configuration=invalid_bounds)

        config = {
            "columns": ["amount"],
            "strategy": "iqr_clip",
            "iqr_multiplier": 1.5,
        }
        executor = self.executor("da.numeric_outlier_handling", config)
        with self.assertRaisesRegex(TransformationRegistryError, "at least four"):
            executor.fit(pa.table({"amount": [1.0, 2.0, 3.0]}), configuration=config)
        with self.assertRaisesRegex(TransformationRegistryError, "non-finite"):
            executor.fit(
                pa.table({"amount": [1.0, 2.0, float("inf"), 4.0]}),
                configuration=config,
            )

    def test_standard_and_min_max_scaling_reuse_training_parameters(self):
        training = pa.table({"amount": pa.array([1.0, 2.0, 3.0, None])})
        evaluation = pa.table({"amount": pa.array([4.0, None])})
        standard_config = {"columns": ["amount"], "strategy": "standard"}
        standard = self.executor("da.numeric_scaling", standard_config)
        state = standard.fit(training, configuration=standard_config)
        self.assertEqual(state["parameters"]["amount"]["mean"], 2.0)
        transformed = standard.transform(
            evaluation,
            configuration=standard_config,
            context=state,
        )
        self.assertAlmostEqual(transformed.column("amount")[0].as_py(), math.sqrt(6))
        self.assertIsNone(transformed.column("amount")[1].as_py())
        self.assertEqual(transformed.schema.field("amount").type, pa.float64())

        minmax_config = {
            "columns": ["amount"],
            "strategy": "min_max",
            "feature_min": -1.0,
            "feature_max": 1.0,
        }
        minmax = self.executor("da.numeric_scaling", minmax_config)
        minmax_state = minmax.fit(
            pa.table({"amount": pa.array([10.0, 20.0])}),
            configuration=minmax_config,
        )
        scaled = minmax.transform(
            pa.table({"amount": pa.array([30.0, None])}),
            configuration=minmax_config,
            context=minmax_state,
        )
        self.assertEqual(scaled.column("amount").to_pylist(), [3.0, None])

    def test_scaling_constant_columns_and_invalid_training_data(self):
        constant = pa.table({"amount": pa.array([5.0, 5.0, None])})
        for strategy, expected in (
            ("standard", [0.0, 0.0, None]),
            ("min_max", [0.0, 0.0, None]),
        ):
            config = {"columns": ["amount"], "strategy": strategy}
            executor = self.executor("da.numeric_scaling", config)
            state = executor.fit(constant, configuration=config)
            self.assertEqual(
                executor.transform(
                    constant,
                    configuration=config,
                    context=state,
                ).column("amount").to_pylist(),
                expected,
            )

        config = {"columns": ["amount"], "strategy": "standard"}
        executor = self.executor("da.numeric_scaling", config)
        for invalid_training in (
            pa.table({"amount": pa.array([], type=pa.float64())}),
            pa.table({"amount": pa.array([None], type=pa.float64())}),
        ):
            with self.subTest(rows=invalid_training.num_rows):
                with self.assertRaisesRegex(TransformationRegistryError, "no observed"):
                    executor.fit(invalid_training, configuration=config)
        with self.assertRaisesRegex(TransformationRegistryError, "integer or floating"):
            executor.fit(pa.table({"amount": ["x"]}), configuration=config)

    def test_fitted_context_cannot_be_reused_with_other_configuration_or_schema(self):
        config = {"columns": ["amount"], "strategy": "standard"}
        executor = self.executor("da.numeric_scaling", config)
        training = pa.table({"amount": [1.0, 2.0]})
        state = executor.fit(training, configuration=config)
        with self.assertRaisesRegex(TransformationRegistryError, "does not match"):
            executor.transform(
                training,
                configuration={"columns": ["amount"], "strategy": "min_max"},
                context=state,
            )
        with self.assertRaisesRegex(TransformationRegistryError, "does not match"):
            executor.transform(
                pa.table({"other": [1.0, 2.0]}),
                configuration=config,
                context=state,
            )

    def test_transformation_input_limits_are_enforced(self):
        operation = replace(
            self.operation("da.string_normalization"),
            resource_limits={
                "max_input_bytes": 1024,
                "max_rows": 1,
                "max_columns": 1,
            },
        )
        executor = _StringNormalization()
        executor.operation = operation
        table = pa.table({"category": ["a", "b"]})
        with self.assertRaisesRegex(TransformationRegistryError, "exceeds"):
            executor.transform(table, configuration={"columns": ["category"]})

    def test_most_frequent_rejects_decoded_string_budget_before_list_conversion(self):
        config = {
            "columns": ["category"],
            "strategy": "most_frequent",
        }
        table = pa.table({"category": ["a", "b"]})
        executor = self.executor("da.missing_value_handling", config)
        with (
            patch(
                "app.preprocessing.estimate_decoded_python_string_bytes",
                return_value=64 * 1024 * 1024 + 1,
            ),
            patch(
                "app.preprocessing._arrow_to_pylist",
                side_effect=AssertionError("Python values were materialized"),
            ),
        ):
            with self.assertRaisesRegex(
                TransformationRegistryError,
                "decoded string values",
            ):
                executor.fit(table, configuration=config)

    def test_most_frequent_decoded_budget_is_cumulative_across_selected_columns(self):
        config = {
            "columns": ["category", "other"],
            "strategy": "most_frequent",
        }
        table = pa.table({"category": ["a"], "other": ["b"]})
        executor = self.executor("da.missing_value_handling", config)
        with (
            patch(
                "app.preprocessing.estimate_decoded_python_string_bytes",
                side_effect=[40 * 1024 * 1024, 40 * 1024 * 1024],
            ),
            patch(
                "app.preprocessing._arrow_to_pylist",
                return_value=["a"],
            ) as materialize,
        ):
            with self.assertRaisesRegex(
                TransformationRegistryError,
                "decoded string values",
            ):
                executor.fit(table, configuration=config)
        self.assertEqual(materialize.call_count, 1)

    def test_invalid_configuration_and_non_table_inputs_fail_clearly(self):
        executor = self.executor(
            "da.string_normalization",
            {"columns": ["category"]},
        )
        with self.assertRaisesRegex(TransformationRegistryError, "configuration"):
            executor.transform(
                pa.table({"category": ["a"]}),
                configuration={"columns": ["category"], "unexpected": True},
            )
        with self.assertRaisesRegex(TransformationRegistryError, "PyArrow Table"):
            executor.transform(
                {"category": ["a"]},
                configuration={"columns": ["category"]},
            )

class BABusinessTransformationTests(unittest.TestCase):
    @staticmethod
    def operation(operation_id):
        return next(
            item
            for item in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
            if item.operation_id == operation_id
        )

    def executor(self, operation_id, configuration):
        operation = self.operation(operation_id)
        return DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
            operation_id,
            configuration=configuration,
            available_prerequisites=operation.prerequisites,
            approved_approvals=operation.required_approvals,
        )

    @staticmethod
    def latest_trace(table):
        return json.loads(
            table.schema.metadata[b"insightos.transformation_lineage"]
        )[-1]

    def test_filter_requires_approval_and_applies_only_explicit_status_predicate(self):
        operation = self.operation("ba.business_rule_filter")
        configuration = {
            "predicates": [
                {
                    "column": "status",
                    "operator": "eq",
                    "value_string": "completed",
                }
            ],
            "composition": "all",
            "null_policy": "exclude",
            "reason": "This analysis explicitly includes completed orders only.",
        }
        with self.assertRaisesRegex(
            TransformationRegistryError,
            "approvals are missing",
        ):
            DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
                operation.operation_id,
                configuration=configuration,
                available_prerequisites=operation.prerequisites,
                approved_approvals=(),
            )

        table = pa.table(
            {
                "order_id": ["O001", "O002", "O003", "O004"],
                "gross_revenue": [100.0, 0.0, 200.0, 150.0],
                "status": ["completed", "completed", "pending", "cancelled"],
            }
        )
        executor = self.executor(operation.operation_id, configuration)
        executor.fit(table, configuration=configuration)
        first = executor.transform(table, configuration=configuration)
        second = executor.transform(table, configuration=configuration)
        self.assertEqual(first.column("order_id").to_pylist(), ["O001", "O002"])
        self.assertEqual(first.column("gross_revenue").to_pylist(), [100.0, 0.0])
        self.assertEqual(first, second)
        self.assertEqual(table.num_rows, 4)
        summary = self.latest_trace(first)["summary"]
        self.assertEqual(summary["rows_before"], 4)
        self.assertEqual(summary["rows_retained"], 2)
        self.assertEqual(summary["rows_filtered"], 2)
        self.assertEqual(summary["reason"], configuration["reason"])

    def test_filter_composition_null_policy_and_review_counts_are_explicit(self):
        table = pa.table(
            {
                "status": pa.array(["completed", None, "pending"], type=pa.string()),
                "amount": [0.0, 5.0, 10.0],
            }
        )
        config = {
            "predicates": [
                {
                    "column": "status",
                    "operator": "eq",
                    "value_string": "completed",
                },
                {"column": "amount", "operator": "gt", "value_number": 7.0},
            ],
            "composition": "any",
            "null_policy": "include",
            "reason": "Keep completed or high-value rows, retaining uncertain rows for review.",
        }
        executor = self.executor("ba.business_rule_filter", config)
        result = executor.transform(table, configuration=config)
        self.assertEqual(result.num_rows, 3)
        summary = self.latest_trace(result)["summary"]
        self.assertEqual(summary["rows_requiring_review"], 1)
        self.assertEqual(summary["rows_filtered"], 0)

        config["null_policy"] = "exclude"
        excluded = self.executor("ba.business_rule_filter", config).transform(
            table,
            configuration=config,
        )
        self.assertEqual(excluded.column("status").to_pylist(), ["completed", "pending"])
        self.assertEqual(
            self.latest_trace(excluded)["summary"]["rows_requiring_review"],
            1,
        )

    def test_filter_supports_membership_null_and_numeric_predicates(self):
        table = pa.table(
            {
                "status": pa.array(["completed", "pending", None, "cancelled"]),
                "amount": [2.0, 4.0, 6.0, 8.0],
            }
        )
        config = {
            "predicates": [
                {
                    "column": "status",
                    "operator": "in",
                    "values_string": ["completed", "pending"],
                },
                {
                    "column": "amount",
                    "operator": "lte",
                    "value_number": 4.0,
                },
            ],
            "composition": "all",
            "null_policy": "exclude",
            "reason": "Explicit allowlist and numeric threshold.",
        }
        filtered = self.executor("ba.business_rule_filter", config).transform(
            table,
            configuration=config,
        )
        self.assertEqual(filtered.column("status").to_pylist(), ["completed", "pending"])

        null_config = {
            "predicates": [{"column": "status", "operator": "is_null"}],
            "composition": "all",
            "null_policy": "exclude",
            "reason": "Select rows with a missing status for review.",
        }
        null_rows = self.executor("ba.business_rule_filter", null_config).transform(
            table,
            configuration=null_config,
        )
        self.assertEqual(null_rows.column("status").to_pylist(), [None])

    def test_filter_membership_preserves_unknown_nulls_for_explicit_null_policy(self):
        table = pa.table(
            {"status": pa.array(["completed", None, "pending"], type=pa.string())}
        )
        for operator, include_expected, exclude_expected in (
            ("in", ["completed", None], ["completed"]),
            ("not_in", [None, "pending"], ["pending"]),
        ):
            base_config = {
                "predicates": [
                    {
                        "column": "status",
                        "operator": operator,
                        "values_string": ["completed"],
                    }
                ],
                "composition": "all",
                "reason": "Apply an explicit membership condition.",
            }
            include_config = {**base_config, "null_policy": "include"}
            include_result = self.executor(
                "ba.business_rule_filter",
                include_config,
            ).transform(table, configuration=include_config)
            self.assertEqual(
                include_result.column("status").to_pylist(),
                include_expected,
            )
            self.assertEqual(
                self.latest_trace(include_result)["summary"][
                    "rows_requiring_review"
                ],
                1,
            )

            exclude_config = {**base_config, "null_policy": "exclude"}
            exclude_result = self.executor(
                "ba.business_rule_filter",
                exclude_config,
            ).transform(table, configuration=exclude_config)
            self.assertEqual(
                exclude_result.column("status").to_pylist(),
                exclude_expected,
            )

    def test_filter_rejects_bad_predicates_types_and_empty_input_safely(self):
        table = pa.table({"status": ["completed"], "amount": [1]})
        invalid_configs = (
            {
                "predicates": [],
                "composition": "all",
                "null_policy": "exclude",
                "reason": "No predicates.",
            },
            {
                "predicates": [
                    {"column": "missing", "operator": "eq", "value_string": "x"}
                ],
                "composition": "all",
                "null_policy": "exclude",
                "reason": "Unknown column.",
            },
            {
                "predicates": [
                    {"column": "amount", "operator": "eq", "value_string": "1"}
                ],
                "composition": "all",
                "null_policy": "exclude",
                "reason": "Wrong comparison type.",
            },
            {
                "predicates": [
                    {"column": "amount", "operator": "gt", "value_number": 1.5}
                ],
                "composition": "all",
                "null_policy": "exclude",
                "reason": "Non-integral threshold for integer field.",
            },
        )
        for configuration in invalid_configs:
            with self.subTest(configuration=configuration):
                executor = self.executor(
                    "ba.business_rule_filter",
                    configuration,
                )
                with self.assertRaises(TransformationRegistryError):
                    executor.transform(table, configuration=configuration)

        empty = pa.table(
            {
                "status": pa.array([], type=pa.string()),
                "amount": pa.array([], type=pa.float64()),
            }
        )
        valid_config = {
            "predicates": [{"column": "status", "operator": "is_not_null"}],
            "composition": "all",
            "null_policy": "exclude",
            "reason": "Keep known statuses.",
        }
        result = self.executor(
            "ba.business_rule_filter",
            valid_config,
        ).transform(empty, configuration=valid_config)
        self.assertEqual(result.num_rows, 0)
        self.assertEqual(self.latest_trace(result)["summary"]["rows_filtered"], 0)

    def test_category_mapping_preserves_unmapped_and_null_values_without_leaking_values(self):
        table = pa.table(
            {
                "category": pa.array(
                    ["Retail", "Wholesale", "Other", None],
                    type=pa.string(),
                ),
                "amount": [100, 200, 300, 400],
            }
        )
        config = {
            "column": "category",
            "mappings": [
                {"from": "Retail", "to": "Consumer"},
                {"from": "Wholesale", "to": "Trade"},
            ],
        }
        executor = self.executor("ba.category_mapping", config)
        result = executor.transform(table, configuration=config)
        self.assertEqual(
            result.column("category").to_pylist(),
            ["Consumer", "Trade", "Other", None],
        )
        trace = self.latest_trace(result)
        self.assertEqual(trace["summary"]["rows_mapped"], 2)
        self.assertEqual(
            trace["summary"]["rows_unmapped_preserved_or_replaced"],
            1,
        )
        self.assertEqual(trace["summary"]["rows_null_preserved_or_replaced"], 1)
        self.assertNotIn("Retail", json.dumps(trace))
        self.assertNotIn("Consumer", json.dumps(trace))
        self.assertEqual(table.column("category").to_pylist(), ["Retail", "Wholesale", "Other", None])

    def test_category_mapping_explicit_unknown_and_null_policies(self):
        table = pa.table(
            {"category": pa.array(["Retail", "Other", None], type=pa.string())}
        )
        config = {
            "column": "category",
            "mappings": [{"from": "Retail", "to": "Consumer"}],
            "unmapped_policy": "constant",
            "unknown_value": "Unclassified",
            "null_policy": "constant",
            "null_value": "Missing",
        }
        result = self.executor("ba.category_mapping", config).transform(
            table,
            configuration=config,
        )
        self.assertEqual(
            result.column("category").to_pylist(),
            ["Consumer", "Unclassified", "Missing"],
        )

        for policy_config, error in (
            (
                {
                    "column": "category",
                    "mappings": [{"from": "Retail", "to": "Consumer"}],
                    "unmapped_policy": "error",
                },
                "unmapped rows",
            ),
            (
                {
                    "column": "category",
                    "mappings": [{"from": "Retail", "to": "Consumer"}],
                    "null_policy": "error",
                },
                "null rows",
            ),
            (
                {
                    "column": "category",
                    "mappings": [{"from": "Retail", "to": "Consumer"}],
                    "unmapped_policy": "constant",
                },
                "requires unknown_value",
            ),
        ):
            executor = self.executor("ba.category_mapping", policy_config)
            with self.assertRaisesRegex(TransformationRegistryError, error):
                executor.transform(table, configuration=policy_config)

    def test_category_mapping_rejects_decoded_string_budget_before_list_conversion(self):
        config = {
            "column": "category",
            "mappings": [{"from": "Retail", "to": "Consumer"}],
        }
        table = pa.table({"category": ["Retail", "Wholesale"]})
        executor = self.executor("ba.category_mapping", config)
        with (
            patch(
                "app.preprocessing.estimate_decoded_python_string_bytes",
                return_value=64 * 1024 * 1024 + 1,
            ),
            patch(
                "app.preprocessing._arrow_to_pylist",
                side_effect=AssertionError("Python values were materialized"),
            ),
        ):
            with self.assertRaisesRegex(
                TransformationRegistryError,
                "decoded string values",
            ):
                executor.transform(table, configuration=config)

    def test_category_mapping_rejects_conflicts_missing_columns_and_types(self):
        table = pa.table({"category": ["Retail", "Retail"], "amount": [1, 2]})
        duplicate_sources = {
            "column": "category",
            "mappings": [
                {"from": "Retail", "to": "Consumer"},
                {"from": "Retail", "to": "Retail"},
            ],
        }
        executor = self.executor("ba.category_mapping", duplicate_sources)
        with self.assertRaisesRegex(TransformationRegistryError, "duplicate or conflicting"):
            executor.fit(table, configuration=duplicate_sources)

        for column, error in (("absent", "not found"), ("amount", "string type")):
            config = {
                "column": column,
                "mappings": [{"from": "Retail", "to": "Consumer"}],
            }
            executor = self.executor("ba.category_mapping", config)
            with self.assertRaisesRegex(TransformationRegistryError, error):
                executor.transform(table, configuration=config)

        empty_mapping = {"column": "category", "mappings": []}
        executor = self.executor("ba.category_mapping", empty_mapping)
        with self.assertRaisesRegex(TransformationRegistryError, "at least one"):
            executor.transform(table, configuration=empty_mapping)

    def test_derived_metric_uses_safe_arithmetic_and_records_formula_metadata(self):
        table = pa.table(
            {
                "order_id": ["O001", "O002", "O003"],
                "gross_revenue": [100.0, 0.0, 200.0],
                "discount": pa.array([10.0, 0.0, None], type=pa.float64()),
            }
        )
        config = {
            "left_column": "gross_revenue",
            "operator": "subtract",
            "right_column": "discount",
            "output_column": "net_revenue",
        }
        executor = self.executor("ba.derived_business_metric", config)
        executor.fit(table, configuration=config)
        first = executor.transform(table, configuration=config)
        second = executor.transform(table, configuration=config)
        self.assertEqual(
            first.column("net_revenue").to_pylist(),
            [90.0, 0.0, None],
        )
        self.assertEqual(first, second)
        self.assertEqual(first.num_rows, table.num_rows)
        self.assertEqual(
            first.column_names,
            ["order_id", "gross_revenue", "discount", "net_revenue"],
        )
        self.assertEqual(
            self.latest_trace(first)["summary"]["formula"],
            {
                "left_column": "gross_revenue",
                "operator": "subtract",
                "right_column": "discount",
                "output_column": "net_revenue",
            },
        )
        self.assertEqual(table.column_names, ["order_id", "gross_revenue", "discount"])

    def test_derived_metric_divide_by_zero_null_and_error_policies(self):
        table = pa.table(
            {
                "profit": [10.0, 20.0, None],
                "revenue": [2.0, 0.0, 5.0],
            }
        )
        config = {
            "left_column": "profit",
            "operator": "divide",
            "right_column": "revenue",
            "output_column": "margin",
            "divide_by_zero": "null",
        }
        result = self.executor("ba.derived_business_metric", config).transform(
            table,
            configuration=config,
        )
        self.assertEqual(result.column("margin").to_pylist(), [5.0, None, None])
        self.assertEqual(
            self.latest_trace(result)["summary"]["null_result_count"],
            2,
        )

        config["divide_by_zero"] = "error"
        executor = self.executor("ba.derived_business_metric", config)
        with self.assertRaisesRegex(TransformationRegistryError, "zero denominators"):
            executor.transform(table, configuration=config)

    def test_derived_metric_validates_columns_types_collisions_and_empty_data(self):
        table = pa.table({"left": [1.0], "right": ["2"], "metric": [7.0]})
        for config, error in (
            (
                {
                    "left_column": "absent",
                    "operator": "add",
                    "right_column": "right",
                    "output_column": "new_metric",
                },
                "not found",
            ),
            (
                {
                    "left_column": "left",
                    "operator": "add",
                    "right_column": "right",
                    "output_column": "new_metric",
                },
                "integer or floating",
            ),
            (
                {
                    "left_column": "left",
                    "operator": "add",
                    "right_column": "left",
                    "output_column": "metric",
                },
                "already exists",
            ),
        ):
            executor = self.executor("ba.derived_business_metric", config)
            with self.assertRaisesRegex(TransformationRegistryError, error):
                executor.transform(table, configuration=config)

        empty = pa.table(
            {
                "left": pa.array([], type=pa.float64()),
                "right": pa.array([], type=pa.float64()),
            }
        )
        config = {
            "left_column": "left",
            "operator": "multiply",
            "right_column": "right",
            "output_column": "product",
        }
        result = self.executor("ba.derived_business_metric", config).transform(
            empty,
            configuration=config,
        )
        self.assertEqual(result.num_rows, 0)
        self.assertEqual(result.schema.field("product").type, pa.float64())

    def test_business_validation_reports_without_removing_or_rewriting_rows(self):
        table = pa.table(
            {
                "status": pa.array(
                    ["completed", "other", "", None],
                    type=pa.string(),
                ),
                "amount": pa.array([10.0, -1.0, 5.0, None], type=pa.float64()),
                "discount": pa.array([2.0, 3.0, 8.0, 1.0], type=pa.float64()),
            }
        )
        original = table
        config = {
            "empty_strings_are_missing": True,
            "rules": [
                {"column": "status", "check": "required"},
                {
                    "column": "status",
                    "check": "allowed_values",
                    "values": ["completed", "pending"],
                },
                {
                    "column": "amount",
                    "check": "numeric_range",
                    "minimum": 0.0,
                    "maximum": 100.0,
                },
                {
                    "column": "discount",
                    "check": "less_or_equal",
                    "other_column": "amount",
                },
            ],
        }
        executor = self.executor("ba.business_rule_validation", config)
        result = executor.transform(table, configuration=config)
        trace = self.latest_trace(result)
        self.assertEqual(trace["summary"]["rows_checked"], 4)
        self.assertFalse(trace["summary"]["data_modified"])
        self.assertEqual(result.num_rows, table.num_rows)
        self.assertEqual(result.column_names, table.column_names)
        self.assertEqual(result.column("status").to_pylist(), original.column("status").to_pylist())
        findings = trace["summary"]["validation_results"]
        self.assertEqual(
            [(item["check"], item["affected_count"], item["review_count"]) for item in findings],
            [
                ("required", 2, 0),
                ("allowed_values", 1, 2),
                ("numeric_range", 1, 1),
                ("less_or_equal", 2, 1),
            ],
        )

    def test_business_validation_handles_empty_input_and_rejects_invalid_rules(self):
        empty = pa.table(
            {
                "status": pa.array([], type=pa.string()),
                "amount": pa.array([], type=pa.float64()),
            }
        )
        empty_config = {"rules": [{"column": "status", "check": "required"}]}
        result = self.executor("ba.business_rule_validation", empty_config).transform(
            empty,
            configuration=empty_config,
        )
        self.assertEqual(self.latest_trace(result)["summary"]["rows_checked"], 0)

        table = pa.table({"status": ["completed"], "amount": [1.0]})
        invalid_configs = (
            {"rules": []},
            {"rules": [{"column": "absent", "check": "required"}]},
            {"rules": [{"column": "amount", "check": "allowed_values", "values": ["1"]}]},
            {"rules": [{"column": "status", "check": "numeric_range", "minimum": 0}]},
            {"rules": [{"column": "amount", "check": "numeric_range"}]},
            {"rules": [{"column": "amount", "check": "less_than", "other_column": "absent"}]},
            {"rules": [{"column": "status", "check": "less_than", "other_column": "amount"}]},
        )
        for config in invalid_configs:
            with self.subTest(configuration=config):
                executor = self.executor("ba.business_rule_validation", config)
                with self.assertRaises(TransformationRegistryError):
                    executor.transform(table, configuration=config)

    def test_ba_metadata_lineage_is_bounded_and_existing_metadata_is_preserved(self):
        table = pa.table(
            {"status": ["pending"]},
            schema=pa.schema(
                [pa.field("status", pa.string())],
                metadata={b"source": b"fixture"},
            ),
        )
        config = {
            "predicates": [
                {"column": "status", "operator": "is_not_null"}
            ],
            "composition": "all",
            "null_policy": "exclude",
            "reason": "Check a known status.",
        }
        first = self.executor("ba.business_rule_filter", config).transform(
            table,
            configuration=config,
        )
        second = self.executor("ba.business_rule_filter", config).transform(
            first,
            configuration=config,
        )
        self.assertEqual(second.schema.metadata[b"source"], b"fixture")
        self.assertEqual(len(json.loads(second.schema.metadata[b"insightos.transformation_lineage"])), 2)
        full_lineage = [
            {
                "operation_id": "ba.business_rule_filter",
                "version": "1",
                "configuration_sha256": "a" * 64,
                "summary": {},
            }
            for _ in range(32)
        ]
        metadata_table = table.replace_schema_metadata(
            {
                **table.schema.metadata,
                b"insightos.transformation_lineage": json.dumps(full_lineage).encode(),
            }
        )
        with self.assertRaisesRegex(TransformationRegistryError, "exceeds 32"):
            self.executor("ba.business_rule_filter", config).transform(
                metadata_table,
                configuration=config,
            )


class TransformationRegistryContractTests(unittest.TestCase):
    def test_discovery_is_deterministic_and_filterable(self):
        registrations = (
            (operation("da.second"), FixtureExecutor()),
            (
                operation(
                    "ba.first",
                    category="BA",
                    status="planned",
                    configuration_schema=None,
                    configuration_defaults=None,
                ),
                None,
            ),
            (
                operation(
                    "da.partial",
                    status="partial",
                    configuration_schema=None,
                    configuration_defaults=None,
                ),
                None,
            ),
        )
        first = TransformationRegistry(registrations)
        second = TransformationRegistry(tuple(reversed(registrations)))

        self.assertEqual(first.list_operations(), second.list_operations())
        self.assertEqual(
            [item.operation_id for item in first.list_operations()],
            ["ba.first", "da.partial", "da.second"],
        )
        self.assertEqual(
            [item.operation_id for item in first.list_operations(status="planned")],
            ["ba.first"],
        )
        self.assertEqual(
            [item.operation_id for item in first.list_operations(category="DA")],
            ["da.partial", "da.second"],
        )
        with self.assertRaises(TransformationRegistryError):
            first.list_operations(category="unknown")
        with self.assertRaises(TransformationRegistryError):
            first.list_operations(status="ready")

    def test_duplicate_operation_ids_are_rejected(self):
        with self.assertRaises(TransformationRegistryError):
            TransformationRegistry(
                (
                    (operation("da.same"), FixtureExecutor()),
                    (operation("da.same"), FixtureExecutor()),
                )
            )

    def test_invalid_metadata_and_executor_combinations_are_rejected(self):
        invalid_operations = (
            operation("bad operation"),
            operation("da.invalid", version="v1"),
            operation("da.invalid", category="statistical"),
            operation("da.invalid", status="ready"),
            operation("da.invalid", input_requirements=()),
            operation(
                "da.invalid",
                changes_rows=False,
                changes_columns=False,
                changes_values=False,
            ),
            operation("da.invalid", resource_limits={"max_rows": True}),
            operation(
                "da.invalid",
                configuration_schema=None,
                configuration_defaults=None,
            ),
            operation(
                "da.invalid",
                configuration_schema={
                    "type": "object",
                    "properties": {"limit": "integer"},
                    "required": [],
                    "additionalProperties": False,
                },
                configuration_defaults={},
            ),
            operation(
                "da.invalid",
                configuration_schema={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1}},
                    "required": ["limit"],
                    "additionalProperties": False,
                },
                configuration_defaults={},
            ),
            operation(
                "da.invalid",
                catalog_reference="da.string_normalization",
                status="implemented",
            ),
        )
        for item in invalid_operations:
            with self.subTest(operation=item.operation_id, status=item.status):
                with self.assertRaises(TransformationRegistryError):
                    TransformationRegistry(((item, FixtureExecutor()),))

        with self.assertRaises(TransformationRegistryError):
            TransformationRegistry(((operation("da.missing_executor"), None),))
        with self.assertRaises(TransformationRegistryError):
            TransformationRegistry(
                (
                    (
                        operation(
                            "da.planned_executor",
                            status="planned",
                            configuration_schema=None,
                            configuration_defaults=None,
                        ),
                        FixtureExecutor(),
                    ),
                )
            )

    def test_catalog_reference_must_match_existing_transformation_capability(self):
        planned = operation(
            "da.string_normalization",
            status="planned",
            configuration_schema=None,
            configuration_defaults=None,
            catalog_reference="da.string_normalization",
        )
        registry = TransformationRegistry(((planned, None),))
        self.assertEqual(registry.list_operations()[0].catalog_reference, planned.operation_id)

        for reference in ("da.schema_drift", "not.a.rule"):
            invalid = operation(
                "da.invalid",
                status="planned",
                configuration_schema=None,
                configuration_defaults=None,
                catalog_reference=reference,
            )
            with self.subTest(reference=reference), self.assertRaises(
                TransformationRegistryError
            ):
                TransformationRegistry(((invalid, None),))

    def test_resolve_executor_requires_valid_configuration_prerequisites_and_approval(self):
        executor = FixtureExecutor()
        registry = TransformationRegistry(
            ((operation("da.test", required_approvals=("user_approved",)), executor),)
        )

        with self.assertRaisesRegex(
            TransformationRegistryError,
            "approvals are missing",
        ):
            registry.resolve_executor(
                "da.test",
                available_prerequisites=("normalized_dataset",),
            )
        with self.assertRaisesRegex(
            TransformationRegistryError,
            "prerequisites are not satisfied",
        ):
            registry.resolve_executor(
                "da.test",
                approved_approvals=("user_approved",),
            )
        with self.assertRaises(TransformationRegistryError):
            registry.resolve_executor(
                "da.test",
                available_prerequisites=("normalized_dataset",),
                approved_approvals=("user_approved",),
                configuration={"unexpected": True},
            )

        resolved = registry.resolve_executor(
            "da.test",
            available_prerequisites=("normalized_dataset",),
            approved_approvals=("user_approved",),
        )
        self.assertIs(resolved, executor)

    def test_partial_and_planned_operations_cannot_be_dispatched(self):
        registrations = tuple(
            (
                operation(
                    operation_id,
                    status=status,
                    configuration_schema=None,
                    configuration_defaults=None,
                ),
                None,
            )
            for operation_id, status in (
                ("da.partial", "partial"),
                ("da.planned", "planned"),
                ("da.unsupported", "unsupported"),
            )
        )
        registry = TransformationRegistry(registrations)
        for operation_id in (
            "da.partial",
            "da.planned",
            "da.unsupported",
        ):
            with self.subTest(operation_id=operation_id), self.assertRaisesRegex(
                TransformationRegistryError,
                "cannot be dispatched",
            ):
                registry.resolve_executor(operation_id)

        with self.assertRaisesRegex(
            TransformationRegistryError,
            "Unknown transformation",
        ):
            registry.resolve_executor("da.missing")


if __name__ == "__main__":
    unittest.main()
