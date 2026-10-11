import copy
import math
import unittest
from dataclasses import replace

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
    def test_default_registry_contains_only_implemented_da_components(self):
        operations = DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
        self.assertEqual(
            [item.operation_id for item in operations],
            [
                "da.missing_value_handling",
                "da.numeric_outlier_handling",
                "da.numeric_scaling",
                "da.string_normalization",
            ],
        )
        self.assertEqual({item.status for item in operations}, {"implemented"})
        self.assertEqual({item.category for item in operations}, {"DA"})
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
