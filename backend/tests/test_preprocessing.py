import unittest

from app.preprocessing import (
    DEFAULT_TRANSFORMATION_REGISTRY,
    TRANSFORMATION_CATEGORIES,
    TransformationOperation,
    TransformationRegistry,
    TransformationRegistryError,
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
    def test_empty_default_registry_does_not_advertise_unimplemented_work(self):
        self.assertEqual(len(DEFAULT_TRANSFORMATION_REGISTRY), 0)
        self.assertEqual(DEFAULT_TRANSFORMATION_REGISTRY.list_operations(), [])
        self.assertEqual(
            TRANSFORMATION_CATEGORIES,
            frozenset({"BA", "DA", "structural"}),
        )

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
