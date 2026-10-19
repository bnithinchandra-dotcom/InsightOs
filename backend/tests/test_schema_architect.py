import unittest
from datetime import datetime
from unittest.mock import patch

import pyarrow as pa

from app.schema_architect import (
    SchemaAnalysisError,
    SchemaTableInput,
    analyze_schema,
)


def table_input(
    file_id: int,
    name: str,
    data: dict[str, list],
    *,
    schema: pa.Schema | None = None,
    profile: dict | None = None,
) -> SchemaTableInput:
    arrow_table = pa.table(data, schema=schema)
    return SchemaTableInput(
        identity={
            "dataset_id": 1,
            "file_id": file_id,
            "filename": f"{name}.parquet",
            "table_index": 0,
            "table_name": name,
        },
        schema=arrow_table.schema,
        iter_batches=lambda: iter(arrow_table.to_batches(max_chunksize=2)),
        source_profile=profile,
    )


class SchemaArchitectTests(unittest.TestCase):
    def test_profiles_nulls_duplicates_candidate_key_and_type_ambiguity(self):
        profile = {
            "tables": [
                {
                    "name": "items",
                    "columns": [
                        {
                            "name": "item_id",
                            "position": 0,
                            "physical_types": ["integer"],
                        },
                        {
                            "name": "category",
                            "position": 1,
                            "physical_types": ["string", "integer"],
                        },
                        {
                            "name": "empty",
                            "position": 2,
                            "physical_types": ["null"],
                        },
                    ],
                }
            ]
        }
        result = analyze_schema(
            [
                table_input(
                    1,
                    "items",
                    {
                        "item_id": [1, 2, 3],
                        "category": ["a", "a", "b"],
                        "empty": [None, None, None],
                    },
                    profile=profile,
                )
            ]
        )
        columns = result["schema"]["tables"][0]["columns"]
        self.assertEqual(columns[0]["candidate_key_status"], "candidate")
        self.assertEqual(columns[0]["distinct_count"], 3)
        self.assertEqual(columns[1]["duplicate_non_null_rows"], 1)
        self.assertEqual(columns[1]["candidate_key_status"], "not_candidate")
        self.assertIn("source_type_inconsistency", columns[1]["warnings"])
        self.assertEqual(columns[2]["null_count"], 3)
        self.assertEqual(columns[2]["null_rate"], 1.0)
        self.assertIn("all_values_null", columns[2]["warnings"])

    def test_many_to_one_relationship_has_exact_coverage_and_no_fanout_warning(self):
        result = analyze_schema(
            [
                table_input(
                    1,
                    "orders",
                    {
                        "customer_id": [1, 1, 2],
                        "amount": [12.5, 7.0, 3.0],
                    },
                ),
                table_input(
                    2,
                    "customers",
                    {
                        "customer_id": [1, 2, 3],
                        "name": ["a", "b", "c"],
                    },
                ),
            ]
        )
        relationship = result["relationships"][0]
        self.assertEqual(relationship["status"], "inferred")
        self.assertEqual(relationship["cardinality_hypothesis"], "many_to_one")
        self.assertEqual(
            relationship["evidence"]["source_referential_coverage"],
            1.0,
        )
        self.assertTrue(relationship["evidence"]["target_candidate_key"])
        self.assertFalse(
            any("multiply" in risk for risk in relationship["risks"])
        )
        self.assertEqual(
            relationship["evidence_origin"],
            "observed_normalized_values",
        )

    def test_orientation_and_structural_star_recommendation_are_deterministic(self):
        result = analyze_schema(
            [
                table_input(
                    1,
                    "customers",
                    {"customer_id": [1, 2], "label": ["a", "b"]},
                ),
                table_input(
                    2,
                    "products",
                    {"product_id": [10, 20], "label": ["x", "y"]},
                ),
                table_input(
                    3,
                    "orders",
                    {
                        "order_id": [100, 101, 102],
                        "customer_id": [1, 2, 1],
                        "product_id": [10, 20, 20],
                        "amount": [5.0, 6.0, 5.0],
                    },
                ),
            ]
        )
        repeated = analyze_schema(
            [
                table_input(
                    1,
                    "customers",
                    {"customer_id": [1, 2], "label": ["a", "b"]},
                ),
                table_input(
                    2,
                    "products",
                    {"product_id": [10, 20], "label": ["x", "y"]},
                ),
                table_input(
                    3,
                    "orders",
                    {
                        "order_id": [100, 101, 102],
                        "customer_id": [1, 2, 1],
                        "product_id": [10, 20, 20],
                        "amount": [5.0, 6.0, 5.0],
                    },
                ),
            ]
        )
        self.assertEqual(result, repeated)
        star = next(
            proposal
            for proposal in result["model_recommendations"]
            if proposal["model_type"] == "star"
        )
        self.assertEqual(star["fact_table_candidate"]["table_name"], "orders")
        self.assertEqual(star["status"], "conditional")
        self.assertEqual(star["grain"]["status"], "unconfirmed")
        self.assertEqual(star["candidate_numeric_measures"], ["amount"])
        self.assertIn("business", star["risks"][0])
        order_columns = next(
            table["columns"]
            for table in result["schema"]["tables"]
            if table["identity"]["table_name"] == "orders"
        )
        roles = {column["name"]: column["role_candidates"] for column in order_columns}
        self.assertIn("degenerate_dimension", roles["order_id"])
        self.assertIn("numeric_measure", roles["amount"])

    def test_hybrid_recommendation_requires_fact_and_dimension_hierarchy_evidence(self):
        result = analyze_schema(
            [
                table_input(
                    1,
                    "customers",
                    {
                        "customer_id": [1, 2, 3],
                        "region_id": [7, 7, 8],
                    },
                ),
                table_input(2, "regions", {"region_id": [7, 8]}),
                table_input(3, "products", {"product_id": [3, 4]}),
                table_input(
                    4,
                    "orders",
                    {
                        "customer_id": [1, 2, 3, 1],
                        "region_id": [7, 8, 8, 7],
                        "product_id": [3, 4, 3, 4],
                        "amount": [10, 20, 30, 40],
                    },
                ),
            ]
        )
        hybrid = next(
            proposal
            for proposal in result["model_recommendations"]
            if proposal["model_type"] == "hybrid"
        )
        self.assertEqual(
            hybrid["fact_table_candidate"]["table_name"],
            "orders",
        )
        self.assertTrue(hybrid["relationship_evidence"]["dimension_hierarchies"])
        self.assertIn(
            "unverified",
            " ".join(hybrid["risks"]),
        )

    def test_no_supported_dimensional_structure_preserves_normalized_tables(self):
        result = analyze_schema(
            [
                table_input(
                    1,
                    "observations",
                    {"label": ["one", "one", "two"]},
                )
            ]
        )
        self.assertEqual(
            result["model_recommendations"][0]["model_type"],
            "normalized",
        )
        self.assertEqual(
            result["model_recommendations"][0]["status"],
            "conservative",
        )

    def test_unsupported_column_type_and_uncertain_semantics_are_explicit(self):
        schema = pa.schema(
            [
                pa.field("complex_value", pa.list_(pa.int64())),
                pa.field("event_time", pa.timestamp("us")),
                pa.field("description", pa.string()),
            ]
        )
        arrow_table = pa.Table.from_arrays(
            [
                pa.array([[1], [2]], type=pa.list_(pa.int64())),
                pa.array(
                    [
                        datetime(2026, 1, 1),
                        datetime(2026, 1, 2),
                    ],
                    type=pa.timestamp("us"),
                ),
                pa.array(["first", "second"]),
            ],
            schema=schema,
        )
        result = analyze_schema(
            [
                SchemaTableInput(
                    identity={"file_id": 1, "table_index": 0},
                    schema=arrow_table.schema,
                    iter_batches=lambda: iter(arrow_table.to_batches()),
                )
            ]
        )
        columns = result["schema"]["tables"][0]["columns"]
        self.assertIn("unsupported_type", columns[0]["warnings"])
        self.assertEqual(columns[0]["distinct_count"], None)
        self.assertEqual(columns[0]["semantic_role_status"], "uncertain")
        self.assertIn("date_or_time_attribute", columns[1]["role_candidates"])
        self.assertIn("descriptive_attribute", columns[2]["role_candidates"])

    def test_duplicate_target_values_warn_about_join_multiplication(self):
        result = analyze_schema(
            [
                table_input(1, "orders", {"customer_id": [1, 1, 2]}),
                table_input(2, "customer_aliases", {"customer_id": [1, 1, 2]}),
            ]
        )
        relationship = result["relationships"][0]
        self.assertEqual(relationship["status"], "ambiguous")
        self.assertEqual(relationship["cardinality_hypothesis"], "many_to_many")
        self.assertTrue(
            any("multiply" in risk for risk in relationship["risks"])
        )
        self.assertIn(
            "The target column is not an observed non-null unique candidate key.",
            relationship["risks"],
        )

    def test_same_name_without_value_overlap_is_rejected(self):
        result = analyze_schema(
            [
                table_input(1, "source", {"region_code": [1, 2]}),
                table_input(2, "target", {"region_code": [8, 9]}),
            ]
        )
        relationship = result["relationships"][0]
        self.assertEqual(relationship["status"], "rejected")
        self.assertEqual(
            relationship["evidence"]["non_null_overlap_count"],
            0,
        )

    def test_incompatible_type_candidate_is_rejected_with_type_evidence(self):
        result = analyze_schema(
            [
                table_input(1, "numeric", {"account_id": [1, 2]}),
                table_input(2, "text", {"account_id": ["1", "2"]}),
            ]
        )
        relationship = result["relationships"][0]
        self.assertEqual(relationship["status"], "rejected")
        self.assertEqual(
            relationship["evidence"]["type_compatibility"],
            "incompatible",
        )
        self.assertIn(
            "The normalized data types are incompatible for a safe equality join.",
            relationship["risks"],
        )

    def test_distinct_tracking_bound_is_visible(self):
        with patch("app.schema_architect.MAX_RETAINED_VALUES", 1):
            result = analyze_schema(
                [table_input(1, "large", {"id": [1, 2, 3]})]
            )
        column = result["schema"]["tables"][0]["columns"][0]
        self.assertFalse(column["distinct_count_exact"])
        self.assertIsNone(column["distinct_count"])
        self.assertEqual(column["candidate_key_status"], "not_evaluated")
        self.assertIn("distinct_tracking_limit", result["warnings"])

    def test_total_row_limit_rejects_instead_of_returning_partial_profiles(self):
        with patch("app.schema_architect.MAX_TOTAL_ROWS", 2):
            with self.assertRaises(SchemaAnalysisError) as raised:
                analyze_schema(
                    [
                        table_input(1, "one", {"id": [1, 2]}),
                        table_input(2, "two", {"id": [3]}),
                    ]
                )
        self.assertEqual(raised.exception.code, "resource_limit")

    def test_relationship_comparison_limit_is_reported(self):
        with patch("app.schema_architect.MAX_RELATIONSHIP_COMPARISONS", 0):
            result = analyze_schema(
                [
                    table_input(1, "source", {"account_id": [1, 2]}),
                    table_input(2, "target", {"account_id": [1, 2]}),
                ]
            )
        self.assertEqual(result["relationships"], [])
        self.assertIn("relationship_search_limit", result["warnings"])

    def test_output_size_limit_rejects_oversized_result(self):
        with patch("app.schema_architect.MAX_RESULT_BYTES", 1):
            with self.assertRaises(SchemaAnalysisError) as raised:
                analyze_schema(
                    [table_input(1, "small", {"id": [1]})]
                )
        self.assertEqual(raised.exception.code, "resource_limit")

    def test_empty_table_and_empty_selection_are_explicit(self):
        empty = analyze_schema([table_input(1, "empty", {"id": []})])
        self.assertEqual(empty["schema"]["tables"][0]["row_count"], 0)
        self.assertEqual(
            empty["schema"]["tables"][0]["columns"][0]["distinct_count"],
            0,
        )
        self.assertEqual(
            empty["schema"]["tables"][0]["schema_warnings"],
            ["empty_table"],
        )
        with self.assertRaises(SchemaAnalysisError) as raised:
            analyze_schema([])
        self.assertEqual(raised.exception.code, "invalid_selection")

    def test_duplicate_column_names_remain_positioned_and_are_warned(self):
        schema = pa.schema(
            [
                pa.field("duplicate", pa.int64()),
                pa.field("duplicate", pa.string()),
            ]
        )
        arrow_table = pa.Table.from_arrays(
            [pa.array([1, 2]), pa.array(["a", "b"])],
            schema=schema,
        )
        result = analyze_schema(
            [
                SchemaTableInput(
                    identity={"file_id": 1, "table_index": 0},
                    schema=arrow_table.schema,
                    iter_batches=lambda: iter(arrow_table.to_batches()),
                )
            ]
        )
        columns = result["schema"]["tables"][0]["columns"]
        self.assertEqual([column["position"] for column in columns], [0, 1])
        self.assertTrue(
            all("duplicate_column_name" in column["warnings"] for column in columns)
        )
        self.assertIn(
            "duplicate_column_names",
            result["schema"]["tables"][0]["schema_warnings"],
        )

    def test_float_zero_sign_does_not_create_a_false_unique_key(self):
        result = analyze_schema(
            [table_input(1, "measurements", {"reading": [0.0, -0.0]})]
        )
        column = result["schema"]["tables"][0]["columns"][0]
        self.assertEqual(column["distinct_count"], 1)
        self.assertEqual(column["candidate_key_status"], "not_candidate")

    def test_numeric_join_values_match_across_compatible_physical_types(self):
        result = analyze_schema(
            [
                table_input(1, "integer_codes", {"account_id": [1, 2]}),
                table_input(2, "decimal_codes", {"account_id": [1.0, 2.0]}),
            ]
        )
        relationship = result["relationships"][0]
        self.assertEqual(relationship["status"], "inferred")
        self.assertEqual(
            relationship["evidence"]["non_null_overlap_count"],
            2,
        )
        self.assertEqual(
            relationship["evidence"]["type_compatibility"],
            "compatible",
        )

    def test_null_join_values_are_included_in_join_loss_risks(self):
        result = analyze_schema(
            [
                table_input(1, "orders", {"customer_id": [1, 1, None]}),
                table_input(2, "customers", {"customer_id": [1, 2]}),
            ]
        )
        relationship = result["relationships"][0]
        self.assertEqual(relationship["status"], "inferred")
        self.assertEqual(
            relationship["evidence"]["source_null_rate"],
            1 / 3,
        )
        self.assertTrue(
            any("lose unmatched" in risk for risk in relationship["risks"])
        )


if __name__ == "__main__":
    unittest.main()
