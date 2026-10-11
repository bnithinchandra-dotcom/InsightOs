import copy
import os
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from app import main
from app.quality_catalog import (
    QUALITY_RULE_CATALOG_VERSION,
    QUALITY_RULE_CATEGORIES,
    QUALITY_RULE_STATUSES,
    get_quality_rule_catalog_entry,
    list_quality_rule_catalog,
    validate_quality_rule_configuration,
)
from app.quality_rules import (
    DEFAULT_QUALITY_RULES,
    QUALITY_RULE_ENGINE_CONFIGURATION,
    QualityRule,
    QualityRuleConfigurationError,
    QualityRuleRegistry,
    run_quality_rules,
)


class QualityRuleCatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_catalog_entries_have_required_fields_unique_ids_and_stable_order(self):
        first = list_quality_rule_catalog()
        second = list_quality_rule_catalog()
        required_fields = {
            "rule_id",
            "version",
            "name",
            "description",
            "category",
            "purpose",
            "use_cases",
            "scope",
            "severity",
            "required_inputs",
            "prerequisites",
            "configuration_schema",
            "default_configuration",
            "method",
            "supported_operations",
            "planned_operations",
            "required_approvals",
            "risks",
            "limitations",
            "status",
            "default_enabled",
            "implementation_reference",
        }

        self.assertEqual(first, second)
        self.assertEqual([item["rule_id"] for item in first], sorted(
            {item["rule_id"] for item in first}
        ))
        for entry in first:
            self.assertEqual(set(entry), required_fields)
            self.assertRegex(entry["rule_id"], r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
            self.assertRegex(entry["version"], r"^\d+(\.\d+){0,2}$")
            self.assertIn(entry["category"], QUALITY_RULE_CATEGORIES)
            self.assertIn(entry["status"], QUALITY_RULE_STATUSES)
            self.assertTrue(entry["name"])
            self.assertTrue(entry["description"])
            if entry["status"] in {"planned", "partial"}:
                self.assertEqual(entry["supported_operations"], [])
                self.assertTrue(entry["planned_operations"])
            elif entry["status"] == "unsupported":
                self.assertEqual(entry["supported_operations"], [])
                self.assertEqual(entry["planned_operations"], [])

    def test_catalog_executor_metadata_matches_the_default_engine_registry(self):
        entries = {
            entry["rule_id"]: entry for entry in list_quality_rule_catalog()
        }
        implemented_ids = {
            rule_id
            for rule_id, entry in entries.items()
            if entry["status"] == "implemented"
        }
        self.assertEqual(
            implemented_ids,
            {rule.rule_id for rule in DEFAULT_QUALITY_RULES},
        )
        for rule in DEFAULT_QUALITY_RULES:
            entry = entries[rule.rule_id]
            self.assertEqual(entry["implementation_reference"]["executor_id"], rule.rule_id)
            self.assertEqual(entry["version"], rule.version)
            self.assertEqual(entry["scope"], rule.scope)
            self.assertEqual(entry["severity"], rule.severity)
            self.assertEqual(entry["prerequisites"], list(rule.prerequisites))
            self.assertEqual(
                entry["default_configuration"],
                rule.configuration_defaults,
            )

    def test_default_enabled_catalog_rules_execute_through_existing_engine(self):
        arrow_table = pa.table(
            {
                "label": ["north", " north", "south"],
                "amount": [1, 2, 100],
            }
        )
        output = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
        path = output.name
        output.close()
        self.addCleanup(os.unlink, path)
        pq.write_table(arrow_table, path)

        normalization_result = {
            "tables": [
                {
                    "table_index": 0,
                    "table_name": "orders",
                    "row_count": arrow_table.num_rows,
                    "schema": [
                        {"name": field.name, "type": str(field.type)}
                        for field in arrow_table.schema
                    ],
                    "null_value_counts": [0, 0],
                    "empty_value_counts": [0, 0],
                }
            ],
            "_profile_result": {
                "tables": [
                    {
                        "name": "orders",
                        "row_count": arrow_table.num_rows,
                        "columns": [
                            {
                                "name": "label",
                                "position": 0,
                                "physical_types": ["string"],
                                "missing_value_count": 0,
                                "empty_value_count": 0,
                                "distinct_value_count": 3,
                                "distinct_count_exact": True,
                            },
                            {
                                "name": "amount",
                                "position": 1,
                                "physical_types": ["integer"],
                                "missing_value_count": 0,
                                "empty_value_count": 0,
                                "distinct_value_count": 3,
                                "distinct_count_exact": True,
                            },
                        ],
                        "duplicate_row_count": 0,
                        "duplicate_row_count_exact": True,
                    }
                ]
            },
            "_parquet_paths": {0: path},
        }

        results = run_quality_rules(normalization_result)
        self.assertEqual(
            {result.rule_id for result in results},
            set(QUALITY_RULE_ENGINE_CONFIGURATION["enabled_rules"]),
        )
        self.assertTrue(all(result.status == "Completed" for result in results))

    def test_planned_rules_are_visible_but_not_executable(self):
        planned = next(
            entry
            for entry in list_quality_rule_catalog()
            if entry["status"] == "planned"
        )
        self.assertNotIn(planned["rule_id"], QUALITY_RULE_ENGINE_CONFIGURATION["enabled_rules"])
        with self.assertRaises(QualityRuleConfigurationError):
            run_quality_rules(
                {"tables": []},
                configuration={
                    **copy.deepcopy(QUALITY_RULE_ENGINE_CONFIGURATION),
                    "enabled_rules": [planned["rule_id"]],
                    "rule_versions": {planned["rule_id"]: planned["version"]},
                    "rules": {planned["rule_id"]: {}},
                },
            )
        with self.assertRaises(QualityRuleConfigurationError):
            QualityRuleRegistry(
                (
                    QualityRule(
                        rule_id=planned["rule_id"],
                        version=planned["version"],
                        scope="column",
                        severity="info",
                        prerequisites=("fixture",),
                        configuration_defaults={},
                        validate_configuration=lambda value: value,
                        unsupported_reason=lambda _data, _config: None,
                        evaluate=lambda _data, _config, _budget: (),
                    ),
                )
            )

    def test_catalog_filters_reject_unknown_values_and_return_deterministic_results(self):
        da_rules = list_quality_rule_catalog(category="DA")
        implemented = list_quality_rule_catalog(status="implemented")
        self.assertTrue(all(entry["category"] == "DA" for entry in da_rules))
        self.assertTrue(all(entry["status"] == "implemented" for entry in implemented))
        with self.assertRaises(ValueError):
            list_quality_rule_catalog(category="unknown")
        with self.assertRaises(ValueError):
            list_quality_rule_catalog(status="ready")
        self.assertIsNone(get_quality_rule_catalog_entry("not.a.rule"))

    def test_executable_configuration_schema_rejects_invalid_shapes(self):
        rule_id = "generic.numeric_outlier_candidates"
        with self.assertRaises(ValueError):
            validate_quality_rule_configuration(
                rule_id,
                {
                    "method": "iqr",
                    "threshold": 0,
                    "min_samples": 4,
                },
            )
        with self.assertRaises(ValueError):
            validate_quality_rule_configuration(
                rule_id,
                {
                    "method": "iqr",
                    "threshold": 1.5,
                    "min_samples": 4,
                    "unregistered": True,
                },
            )

    def test_catalog_api_supports_list_detail_filters_and_validation(self):
        response = self.client.get("/api/v1/quality-rules")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["catalog_version"], QUALITY_RULE_CATALOG_VERSION)
        self.assertEqual(
            body["rules"],
            self.client.get("/api/v1/quality-rules").json()["rules"],
        )

        filtered = self.client.get(
            "/api/v1/quality-rules",
            params={"category": "statistical", "status": "implemented"},
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(
            [entry["rule_id"] for entry in filtered.json()["rules"]],
            ["generic.numeric_outlier_candidates"],
        )
        detail = self.client.get(
            "/api/v1/quality-rules/generic.numeric_outlier_candidates"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            detail.json()["rule"]["rule_id"],
            "generic.numeric_outlier_candidates",
        )
        self.assertEqual(
            self.client.get("/api/v1/quality-rules/unknown.rule").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/quality-rules",
                params={"status": "ready"},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/quality-rules",
                params={"category": "unknown"},
            ).status_code,
            422,
        )
