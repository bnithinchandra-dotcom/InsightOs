import unittest
from dataclasses import replace
from unittest.mock import patch

import pyarrow as pa

from app import preprocessing as preprocessing_module
from app.preprocessing import (
    DEFAULT_TRANSFORMATION_REGISTRY,
    PreprocessingApproval,
    PreprocessingOrchestrationError,
    PreprocessingStep,
    TransformationOperation,
    TransformationRegistry,
    orchestrate_preprocessing,
)


class FixtureExecutor:
    def __init__(self, *, fail_transform=False, output=None):
        self.events = []
        self.fail_transform = fail_transform
        self.output = output

    def fit(self, data, *, configuration, context=None):
        _ = data
        self.events.append(("fit", configuration["value"], context))
        return None

    def transform(self, data, *, configuration, context=None):
        self.events.append(("transform", configuration["value"], context))
        if self.fail_transform:
            raise RuntimeError("sensitive row content must not be returned")
        if self.output is not None:
            return self.output
        return data


def fixture_operation(operation_id, executor, *, requires_fit=False):
    return TransformationOperation(
        operation_id=operation_id,
        version="1",
        category="DA",
        status="implemented",
        input_requirements=("bounded PyArrow table",),
        output_requirements=("bounded PyArrow table",),
        prerequisites=("pyarrow_table",),
        configuration_schema={
            "type": "object",
            "properties": {"value": {"type": "integer", "minimum": 0}},
            "required": [],
            "additionalProperties": False,
        },
        configuration_defaults={"value": 0},
        requires_fit=requires_fit,
        changes_rows=False,
        changes_columns=False,
        changes_values=True,
        required_approvals=(f"approved_{operation_id.split('.')[-1]}",),
        resource_limits={
            "max_input_bytes": 128 * 1024 * 1024,
            "max_rows": 2_000_000,
            "max_columns": 512,
        },
    ), executor


def fixture_registry(*executors):
    return TransformationRegistry(
        tuple(
            fixture_operation(
                f"da.fixture_{index}",
                executor,
            )
            for index, executor in enumerate(executors, start=1)
        )
    )


def approvals_for(steps, registry):
    operations = {
        operation.operation_id: operation
        for operation in registry.list_operations()
    }
    return [
        PreprocessingApproval(
            step_id=step.step_id,
            operation_id=step.operation_id,
            configuration=step.configuration,
            approved_approvals=operations[step.operation_id].required_approvals,
        )
        for step in steps
    ]


class PreprocessingOrchestratorTests(unittest.TestCase):
    def test_prerequisite_generator_is_bounded_and_normalized(self):
        tokens = preprocessing_module._orchestration_tokens(
            (token for token in ("zeta", "alpha")),
            "available prerequisites",
        )
        self.assertEqual(tokens, ("alpha", "zeta"))

    def test_oversized_prerequisite_iterable_is_rejected(self):
        maximum = preprocessing_module._MAX_ORCHESTRATION_PREREQUISITES
        values = (f"prerequisite_{index}" for index in range(maximum + 1))

        with self.assertRaisesRegex(
            PreprocessingOrchestrationError,
            f"more than {maximum} identifiers",
        ):
            preprocessing_module._orchestration_tokens(
                values,
                "available prerequisites",
            )

    def test_infinite_prerequisite_generator_is_rejected_after_limit(self):
        maximum = preprocessing_module._MAX_ORCHESTRATION_PREREQUISITES
        consumed = 0

        def infinite_prerequisites():
            nonlocal consumed
            while True:
                consumed += 1
                yield f"prerequisite_{consumed}"

        with self.assertRaisesRegex(
            PreprocessingOrchestrationError,
            f"more than {maximum} identifiers",
        ):
            preprocessing_module._orchestration_tokens(
                infinite_prerequisites(),
                "available prerequisites",
            )

        self.assertEqual(consumed, maximum + 1)

    def test_runs_da_and_ba_steps_in_configured_order(self):
        data = pa.table(
            {
                "category": [" Retail ", "Food", " Retail "],
                "amount": [12.0, 30.0, 18.0],
                "discount": [2.0, 5.0, 3.0],
            }
        )
        steps = [
            PreprocessingStep(
                "normalize",
                "da.string_normalization",
                {"columns": ["category"], "trim": True},
            ),
            PreprocessingStep(
                "filter",
                "ba.business_rule_filter",
                {
                    "predicates": [
                        {
                            "column": "category",
                            "operator": "eq",
                            "value_string": "Retail",
                        }
                    ],
                    "reason": "Keep the explicitly requested category.",
                },
            ),
            PreprocessingStep(
                "net_amount",
                "ba.derived_business_metric",
                {
                    "left_column": "amount",
                    "operator": "subtract",
                    "right_column": "discount",
                    "output_column": "net_amount",
                },
            ),
        ]
        result = orchestrate_preprocessing(
            data,
            steps,
            approvals=approvals_for(
                steps,
                DEFAULT_TRANSFORMATION_REGISTRY,
            ),
            available_prerequisites=(
                "explicit_column_selection",
                "explicit_business_rule_definition",
            ),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.execution_order, ("normalize", "filter", "net_amount"))
        self.assertEqual(result.completed_steps, result.execution_order)
        self.assertEqual(result.unexecuted_steps, ())
        self.assertEqual(result.steps[0].operation_version, "1")
        self.assertEqual(len(result.steps[0].configuration_sha256), 64)
        self.assertEqual(result.final_table.column("category").to_pylist(), ["Retail", "Retail"])
        self.assertEqual(result.final_table.column("net_amount").to_pylist(), [10.0, 15.0])
        self.assertEqual(result.steps[1].summary_metadata["rows_filtered"], 1)
        self.assertNotIn("reason", result.steps[1].summary_metadata)
        self.assertEqual(
            [(step.rows_before, step.rows_after) for step in result.steps],
            [(3, 3), (3, 2), (2, 2)],
        )

    def test_preserves_requested_order_when_order_changes_results(self):
        data = pa.table({"category": [" Retail ", "Food"]})
        normalize = PreprocessingStep(
            "normalize",
            "da.string_normalization",
            {"columns": ["category"], "trim": True},
        )
        filter_step = PreprocessingStep(
            "filter",
            "ba.business_rule_filter",
            {
                "predicates": [
                    {
                        "column": "category",
                        "operator": "eq",
                        "value_string": "Retail",
                    }
                ],
                "reason": "Keep the explicitly requested category.",
            },
        )
        approvals = approvals_for(
            [normalize, filter_step],
            DEFAULT_TRANSFORMATION_REGISTRY,
        )
        prerequisites = (
            "explicit_column_selection",
            "explicit_business_rule_definition",
        )

        normalize_then_filter = orchestrate_preprocessing(
            data,
            [normalize, filter_step],
            approvals=approvals,
            available_prerequisites=prerequisites,
        )
        filter_then_normalize = orchestrate_preprocessing(
            data,
            [filter_step, normalize],
            approvals=approvals,
            available_prerequisites=prerequisites,
        )

        self.assertEqual(normalize_then_filter.final_table.num_rows, 1)
        self.assertEqual(filter_then_normalize.final_table.num_rows, 0)
        self.assertEqual(filter_then_normalize.execution_order, ("filter", "normalize"))

    def test_unknown_operation_is_rejected_before_any_executor_runs(self):
        executor = FixtureExecutor()
        registry = fixture_registry(executor)
        with self.assertRaisesRegex(
            PreprocessingOrchestrationError,
            "Unknown transformation operation",
        ):
            orchestrate_preprocessing(
                pa.table({"value": [1]}),
                [PreprocessingStep("unknown", "da.unknown")],
                registry=registry,
            )
        self.assertEqual(executor.events, [])

    def test_invalid_later_configuration_is_rejected_before_execution(self):
        first, second = FixtureExecutor(), FixtureExecutor()
        registry = fixture_registry(first, second)
        steps = [
            PreprocessingStep("first", "da.fixture_1", {"value": 1}),
            PreprocessingStep("second", "da.fixture_2", {"value": -1}),
        ]
        with self.assertRaisesRegex(
            PreprocessingOrchestrationError,
            "invalid configuration",
        ):
            orchestrate_preprocessing(
                pa.table({"value": [1]}),
                steps,
                approvals=approvals_for(steps, registry),
                registry=registry,
            )
        self.assertEqual(first.events, [])
        self.assertEqual(second.events, [])

    def test_missing_approval_is_rejected_before_execution(self):
        executor = FixtureExecutor()
        registry = fixture_registry(executor)
        with self.assertRaisesRegex(PreprocessingOrchestrationError, "Approval is required"):
            orchestrate_preprocessing(
                pa.table({"value": [1]}),
                [PreprocessingStep("one", "da.fixture_1", {"value": 1})],
                registry=registry,
            )
        self.assertEqual(executor.events, [])

    def test_missing_operation_prerequisite_is_rejected_before_execution(self):
        step = PreprocessingStep(
            "normalize",
            "da.string_normalization",
            {"columns": ["value"]},
        )
        with self.assertRaisesRegex(
            PreprocessingOrchestrationError,
            "prerequisites are not satisfied",
        ):
            orchestrate_preprocessing(
                pa.table({"value": ["x"]}),
                [step],
                approvals=approvals_for(
                    [step],
                    DEFAULT_TRANSFORMATION_REGISTRY,
                ),
            )

    def test_approval_must_match_exact_step_operation_and_effective_configuration(self):
        executor = FixtureExecutor()
        registry = fixture_registry(executor)
        step = PreprocessingStep("one", "da.fixture_1", {"value": 1})
        mismatched_approval = PreprocessingApproval(
            step_id="one",
            operation_id="da.fixture_1",
            configuration={"value": 2},
            approved_approvals=("approved_fixture_1",),
        )
        with self.assertRaisesRegex(
            PreprocessingOrchestrationError,
            "does not match its effective configuration",
        ):
            orchestrate_preprocessing(
                pa.table({"value": [1]}),
                [step],
                approvals=[mismatched_approval],
                registry=registry,
            )
        for invalid_approval, expected_error in (
            (
                PreprocessingApproval(
                    "different_step",
                    "da.fixture_1",
                    {"value": 1},
                    ("approved_fixture_1",),
                ),
                "unknown step",
            ),
            (
                PreprocessingApproval(
                    "one",
                    "da.fixture_2",
                    {"value": 1},
                    ("approved_fixture_1",),
                ),
                "different operation",
            ),
        ):
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(
                    PreprocessingOrchestrationError,
                    expected_error,
                ):
                    orchestrate_preprocessing(
                        pa.table({"value": [1]}),
                        [step],
                        approvals=[invalid_approval],
                        registry=registry,
                    )
        self.assertEqual(executor.events, [])

    def test_duplicate_step_ids_and_malformed_plans_are_rejected(self):
        executor = FixtureExecutor()
        registry = fixture_registry(executor)
        duplicate_steps = [
            PreprocessingStep("same", "da.fixture_1", {"value": 1}),
            PreprocessingStep("same", "da.fixture_1", {"value": 1}),
        ]
        with self.assertRaisesRegex(PreprocessingOrchestrationError, "must be unique"):
            orchestrate_preprocessing(
                pa.table({"value": [1]}),
                duplicate_steps,
                approvals=approvals_for(duplicate_steps, registry),
                registry=registry,
            )
        with self.assertRaisesRegex(PreprocessingOrchestrationError, "ordered list or tuple"):
            orchestrate_preprocessing(
                pa.table({"value": [1]}),
                iter(()),
                registry=registry,
            )
        with self.assertRaisesRegex(PreprocessingOrchestrationError, "bounded step ID"):
            orchestrate_preprocessing(
                pa.table({"value": [1]}),
                [PreprocessingStep("invalid id", "da.fixture_1", {"value": 1})],
                registry=registry,
            )
        self.assertEqual(executor.events, [])

    def test_failure_stops_pipeline_and_does_not_expose_intermediate_table(self):
        first = FixtureExecutor()
        failing = FixtureExecutor(fail_transform=True)
        later = FixtureExecutor()
        registry = fixture_registry(first, failing, later)
        steps = [
            PreprocessingStep(f"step{index}", f"da.fixture_{index}", {"value": index})
            for index in range(1, 4)
        ]

        result = orchestrate_preprocessing(
            pa.table({"value": [1]}),
            steps,
            approvals=approvals_for(steps, registry),
            registry=registry,
        )

        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.final_table)
        self.assertEqual(result.failure_step_id, "step2")
        self.assertIn("RuntimeError", result.error_description)
        self.assertNotIn("sensitive row content", result.error_description)
        self.assertEqual(result.completed_steps, ("step1",))
        self.assertEqual(result.unexecuted_steps, ("step3",))
        self.assertEqual(
            [step.status for step in result.steps],
            ["completed", "failed", "not_executed"],
        )
        self.assertEqual(result.provenance["status"], "failed")
        self.assertEqual(result.provenance["outputs"], [])
        self.assertEqual(result.steps[0].provenance["status"], "completed")
        self.assertEqual(result.steps[1].provenance["status"], "failed")
        self.assertIsNone(result.steps[1].output_fingerprint)
        self.assertEqual(
            result.provenance["parent_operation_ids"],
            [
                result.steps[0].provenance["operation_id"],
                result.steps[1].provenance["operation_id"],
            ],
        )
        self.assertEqual(first.events[-1][0], "transform")
        self.assertEqual(failing.events[-1][0], "transform")
        self.assertEqual(later.events, [])

    def test_original_input_table_is_unchanged(self):
        data = pa.table({"category": [" Retail ", "Food"]})
        original = data
        step = PreprocessingStep(
            "normalize",
            "da.string_normalization",
            {"columns": ["category"], "trim": True},
        )
        result = orchestrate_preprocessing(
            data,
            [step],
            approvals=approvals_for([step], DEFAULT_TRANSFORMATION_REGISTRY),
            available_prerequisites=("explicit_column_selection",),
        )
        self.assertIs(data, original)
        self.assertEqual(data.column("category").to_pylist(), [" Retail ", "Food"])
        self.assertEqual(
            result.final_table.column("category").to_pylist(),
            ["Retail", "Food"],
        )

    def test_missing_required_column_fails_the_step_without_a_final_table(self):
        step = PreprocessingStep(
            "normalize",
            "da.string_normalization",
            {"columns": ["missing_column"]},
        )
        result = orchestrate_preprocessing(
            pa.table({"category": ["Retail"]}),
            [step],
            approvals=approvals_for([step], DEFAULT_TRANSFORMATION_REGISTRY),
            available_prerequisites=("explicit_column_selection",),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_step_id, "normalize")
        self.assertIn("was not found", result.error_description)
        self.assertIsNone(result.final_table)

    def test_fitted_context_is_reused_without_refitting_execution_data(self):
        operation_id = "da.numeric_scaling"
        configuration = {"columns": ["amount"], "strategy": "standard"}
        operation = next(
            item
            for item in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
            if item.operation_id == operation_id
        )
        executor = DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
            operation_id,
            configuration=configuration,
            available_prerequisites=operation.prerequisites,
            approved_approvals=operation.required_approvals,
        )
        training = pa.table({"amount": [0.0, 2.0, 4.0]})
        fit_context = executor.fit(training, configuration=configuration)
        evaluation = pa.table({"amount": [6.0]})
        step = PreprocessingStep(
            "scale",
            operation_id,
            configuration,
            fit_context,
        )

        result = orchestrate_preprocessing(
            evaluation,
            [step],
            approvals=approvals_for([step], DEFAULT_TRANSFORMATION_REGISTRY),
            available_prerequisites=("explicit_column_selection",),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.final_table.column("amount").to_pylist(), [2.449489742783178])
        self.assertEqual(evaluation.column("amount").to_pylist(), [6.0])

    def test_missing_or_incompatible_fit_context_is_rejected_in_preflight(self):
        first = FixtureExecutor()
        step = PreprocessingStep("needs_fit", "da.fixture_1", {"value": 1})
        with self.assertRaisesRegex(PreprocessingOrchestrationError, "requires a fitted context"):
            orchestrate_preprocessing(
                pa.table({"value": [1]}),
                [step],
                approvals=approvals_for([step], fixture_registry(first)),
                registry=TransformationRegistry(
                    (
                        fixture_operation(
                            "da.fixture_1",
                            first,
                            requires_fit=True,
                        ),
                    )
                ),
            )
        self.assertEqual(first.events, [])

        scaling_configuration = {"columns": ["amount"], "strategy": "standard"}
        scaling_operation = next(
            item
            for item in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
            if item.operation_id == "da.numeric_scaling"
        )
        scaling_executor = DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
            scaling_operation.operation_id,
            configuration=scaling_configuration,
            available_prerequisites=scaling_operation.prerequisites,
            approved_approvals=scaling_operation.required_approvals,
        )
        context = scaling_executor.fit(
            pa.table({"amount": [1.0, 2.0]}),
            configuration=scaling_configuration,
        )
        tracking_step = PreprocessingStep(
            "tracking",
            "da.string_normalization",
            {"columns": []},
        )
        mismatched_step = PreprocessingStep(
            "scale",
            "da.numeric_scaling",
            {"columns": ["other"], "strategy": "standard"},
            context,
        )
        string_operation = next(
            item
            for item in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
            if item.operation_id == tracking_step.operation_id
        )
        tracking_approval = PreprocessingApproval(
            tracking_step.step_id,
            tracking_step.operation_id,
            tracking_step.configuration,
            string_operation.required_approvals,
        )
        scaling_approval = PreprocessingApproval(
            "scale",
            "da.numeric_scaling",
            {"columns": ["other"], "strategy": "standard"},
            scaling_operation.required_approvals,
        )
        string_executor = DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
            tracking_step.operation_id,
            configuration=tracking_step.configuration,
            available_prerequisites=string_operation.prerequisites,
            approved_approvals=string_operation.required_approvals,
        )
        with patch.object(
            string_executor,
            "fit",
            wraps=string_executor.fit,
        ) as fit_mock:
            with self.assertRaisesRegex(
                PreprocessingOrchestrationError,
                "incompatible fitted context",
            ):
                orchestrate_preprocessing(
                    pa.table({"amount": [1.0, 2.0], "other": [1.0, 2.0]}),
                    [tracking_step, mismatched_step],
                    approvals=[tracking_approval, scaling_approval],
                    available_prerequisites=("explicit_column_selection",),
                    registry=DEFAULT_TRANSFORMATION_REGISTRY,
                )
        fit_mock.assert_not_called()

    def test_later_step_failure_reports_unexecuted_step_and_no_final_table(self):
        first = FixtureExecutor()
        failing = FixtureExecutor(fail_transform=True)
        registry = fixture_registry(first, failing)
        steps = [
            PreprocessingStep("first", "da.fixture_1", {"value": 1}),
            PreprocessingStep("second", "da.fixture_2", {"value": 2}),
        ]
        result = orchestrate_preprocessing(
            pa.table({"value": [1]}),
            steps,
            approvals=approvals_for(steps, registry),
            registry=registry,
        )
        self.assertEqual(result.steps[1].status, "failed")
        self.assertIsNone(result.final_table)

    def test_output_resource_limits_are_checked(self):
        executor = FixtureExecutor(
            output=pa.table({"value": [1], "extra": [2]})
        )
        registry = fixture_registry(executor)
        step = PreprocessingStep("too_wide", "da.fixture_1", {"value": 1})
        with patch.object(
            preprocessing_module,
            "_MAX_TRANSFORMATION_COLUMNS",
            1,
        ):
            result = orchestrate_preprocessing(
                pa.table({"value": [1]}),
                [step],
                approvals=approvals_for([step], registry),
                registry=registry,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_step_id, "too_wide")
        self.assertIn("limit", result.error_description)
        self.assertIsNone(result.final_table)

    def test_input_resource_limits_are_checked_before_executor_dispatch(self):
        executor = FixtureExecutor()
        operation, _ = fixture_operation("da.fixture_1", executor)
        operation = replace(
            operation,
            resource_limits={
                **operation.resource_limits,
                "max_rows": 1,
            },
        )
        registry = TransformationRegistry(((operation, executor),))
        step = PreprocessingStep("oversized_input", "da.fixture_1", {"value": 1})
        result = orchestrate_preprocessing(
            pa.table({"value": [1, 2]}),
            [step],
            approvals=approvals_for([step], registry),
            registry=registry,
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_step_id, "oversized_input")
        self.assertEqual(executor.events, [])
        self.assertIsNone(result.final_table)

    def test_global_output_row_column_and_byte_limits_are_enforced(self):
        input_table = pa.table({"value": [1]})
        cases = (
            (
                "_MAX_TRANSFORMATION_ROWS",
                1,
                pa.table({"value": [1, 2]}),
            ),
            (
                "_MAX_TRANSFORMATION_COLUMNS",
                1,
                pa.table({"value": [1], "extra": [2]}),
            ),
            (
                "_MAX_TRANSFORMATION_INPUT_BYTES",
                input_table.nbytes,
                pa.table({"value": ["a value larger than the input limit"]}),
            ),
        )
        for limit_name, limit, output in cases:
            with self.subTest(limit=limit_name):
                executor = FixtureExecutor(output=output)
                registry = fixture_registry(executor)
                step = PreprocessingStep(
                    "over_global_limit",
                    "da.fixture_1",
                    {"value": 1},
                )
                original_values = input_table.column("value").to_pylist()
                with patch.object(
                    preprocessing_module,
                    limit_name,
                    limit,
                ):
                    result = orchestrate_preprocessing(
                        input_table,
                        [step],
                        approvals=approvals_for([step], registry),
                        registry=registry,
                    )

                self.assertEqual(result.status, "failed")
                self.assertEqual(result.failure_step_id, step.step_id)
                self.assertEqual(result.steps[0].status, "failed")
                self.assertIsNone(result.final_table)
                self.assertIn("global", result.error_description)
                self.assertEqual(
                    input_table.column("value").to_pylist(),
                    original_values,
                )

    def test_non_table_output_fails_schema_validation(self):
        executor = FixtureExecutor(output={"not": "a table"})
        registry = fixture_registry(executor)
        step = PreprocessingStep("invalid_output", "da.fixture_1", {"value": 1})
        result = orchestrate_preprocessing(
            pa.table({"value": [1]}),
            [step],
            approvals=approvals_for([step], registry),
            registry=registry,
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_step_id, "invalid_output")
        self.assertIn("output must be a PyArrow Table", result.error_description)
        self.assertIsNone(result.final_table)

    def test_fitted_context_schema_is_checked_against_step_input(self):
        operation_id = "da.numeric_scaling"
        configuration = {"columns": ["amount"], "strategy": "standard"}
        operation = next(
            item
            for item in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
            if item.operation_id == operation_id
        )
        executor = DEFAULT_TRANSFORMATION_REGISTRY.resolve_executor(
            operation_id,
            configuration=configuration,
            available_prerequisites=operation.prerequisites,
            approved_approvals=operation.required_approvals,
        )
        context = executor.fit(
            pa.table({"amount": [1.0, 2.0]}),
            configuration=configuration,
        )
        step = PreprocessingStep("scale", operation_id, configuration, context)
        result = orchestrate_preprocessing(
            pa.table({"amount": [3.0], "other": [5.0]}),
            [step],
            approvals=approvals_for(
                [step],
                DEFAULT_TRANSFORMATION_REGISTRY,
            ),
            available_prerequisites=("explicit_column_selection",),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_step_id, "scale")
        self.assertIn("input schema", result.error_description)
        self.assertIsNone(result.final_table)

    def test_repeated_execution_is_deterministic_and_empty_plan_is_successful(self):
        data = pa.table({"amount": [10.0, 3.0], "discount": [1.0, 2.0]})
        step = PreprocessingStep(
            "net",
            "ba.derived_business_metric",
            {
                "left_column": "amount",
                "operator": "subtract",
                "right_column": "discount",
                "output_column": "net_amount",
            },
        )
        arguments = {
            "approvals": approvals_for(
                [step],
                DEFAULT_TRANSFORMATION_REGISTRY,
            ),
            "available_prerequisites": ("explicit_business_rule_definition",),
        }
        first = orchestrate_preprocessing(data, [step], **arguments)
        second = orchestrate_preprocessing(data, [step], **arguments)
        self.assertEqual(first.status, "completed")
        self.assertEqual(first.final_table, second.final_table)
        self.assertEqual(first.steps[0].operation_id, second.steps[0].operation_id)
        self.assertEqual(
            first.steps[0].input_fingerprint,
            second.steps[0].input_fingerprint,
        )
        self.assertEqual(
            first.steps[0].output_fingerprint,
            second.steps[0].output_fingerprint,
        )
        self.assertEqual(
            first.provenance["operation_id"],
            second.provenance["operation_id"],
        )
        self.assertEqual(first.provenance["status"], "completed")
        self.assertEqual(first.provenance["verification"]["status"], "unverifiable")
        self.assertEqual(
            first.steps[0].provenance["outputs"][0]["sha256"],
            first.steps[0].output_fingerprint["sha256"],
        )
        self.assertEqual(
            first.provenance["parent_operation_ids"],
            [first.steps[0].provenance["operation_id"]],
        )

        empty = orchestrate_preprocessing(data, [])
        self.assertEqual(empty.status, "completed")
        self.assertIs(empty.final_table, data)
        self.assertEqual(empty.execution_order, ())
        self.assertEqual(
            empty.provenance["inputs"],
            empty.provenance["outputs"],
        )

    def test_invalid_input_table_is_rejected(self):
        with self.assertRaisesRegex(PreprocessingOrchestrationError, "PyArrow Table"):
            orchestrate_preprocessing(None, [])


if __name__ == "__main__":
    unittest.main()
