import unittest

import pyarrow as pa

from app.multi_dataset_analytics import (
    AnalyticsSource,
    JoinKey,
    JoinStep,
    Metric,
    MultiDatasetAnalyticsError,
    execute_join_aggregation,
    recommend_joins,
)


def source(alias, dataset_id, file_id, values, checksum=None):
    return AnalyticsSource(
        alias=alias,
        dataset_id=dataset_id,
        file_id=file_id,
        table_index=0,
        table=values if isinstance(values, pa.Table) else pa.table(values),
        checksum=checksum or f"{dataset_id:064x}",
    )


def key(alias, column):
    return JoinKey(alias, column)


def join(left_alias, left_column, right_alias, right_column, *, kind="inner", allow=False):
    return JoinStep(
        left=key(left_alias, left_column),
        right=key(right_alias, right_column),
        join_type=kind,
        allow_many_to_many=allow,
    )


class MultiDatasetAnalyticsTests(unittest.TestCase):
    def test_recommendations_explain_names_types_overlap_and_cardinality(self):
        customers = source(
            "customers",
            1,
            11,
            {"Customer ID": [1, 2, 3], "region": ["west", "east", "west"]},
        )
        sales = source(
            "sales",
            2,
            22,
            {"customer_id": [1, 1, 3], "amount": [10.0, 5.0, 7.0]},
        )

        result = recommend_joins([customers, sales])
        recommendation = next(
            item
            for item in result["recommendations"]
            if item["left"]["column"] == "Customer ID"
            and item["right"]["column"] == "customer_id"
        )

        self.assertEqual(recommendation["cardinality"], "one_to_many")
        self.assertEqual(recommendation["evidence"]["observed_overlap"], 2)
        self.assertTrue(recommendation["evidence"]["column_names_normalized_equal"])
        self.assertTrue(recommendation["recommendation_only"])
        self.assertIn("approve exact keys", result["warnings"][0])

    def test_recommendations_do_not_suggest_incompatible_types(self):
        left = source("left", 1, 1, {"id": [1, 2]})
        right = source("right", 2, 2, {"id": ["1", "2"]})

        result = recommend_joins([left, right])

        self.assertEqual(result["recommendations"], [])

    def test_join_execution_rejects_incompatible_key_types(self):
        left = source("left", 1, 1, {"id": [1]})
        right = source("right", 2, 2, {"id": ["1"]})
        with self.assertRaises(MultiDatasetAnalyticsError) as raised:
            execute_join_aggregation(
                [left, right],
                [join("left", "id", "right", "id")],
                [],
                [Metric("rows", "count")],
            )
        self.assertEqual(raised.exception.code, "incompatible_join_types")

    def test_missing_keys_and_duplicate_columns_are_rejected(self):
        left = source("left", 1, 1, {"id": [1]})
        right = source("right", 2, 2, {"other": [1]})
        with self.assertRaises(MultiDatasetAnalyticsError) as missing:
            execute_join_aggregation(
                [left, right],
                [join("left", "not_present", "right", "other")],
                [],
                [Metric("rows", "count")],
            )
        self.assertEqual(missing.exception.code, "missing_join_key")

        duplicate = source(
            "dup",
            3,
            3,
            pa.Table.from_arrays([[1], [2]], names=["id", "id"]),
        )
        with self.assertRaises(MultiDatasetAnalyticsError) as repeated:
            recommend_joins([left, duplicate])
        self.assertEqual(repeated.exception.code, "duplicate_column_names")

    def test_null_keys_do_not_match_and_left_join_reports_unmatched_rows(self):
        left = source("left", 1, 1, {"id": [1, None, 2]})
        right = source("right", 2, 2, {"id": [1, None], "amount": [5, 8]})

        result = execute_join_aggregation(
            [left, right],
            [join("left", "id", "right", "id", kind="left")],
            [],
            [Metric("rows", "count")],
        )

        self.assertEqual(result["joined_row_count"], 3)
        self.assertEqual(result["results"], [{"rows": 3}])
        self.assertEqual(result["join_evidence"][0]["unmatched_left_rows"], 2)
        self.assertEqual(len(result["warnings"]), 2)

    def test_one_to_many_join_preserves_rows_and_aggregates_correctly(self):
        accounts = source(
            "accounts",
            1,
            11,
            {"account_id": [10, 20], "region": ["west", "east"]},
        )
        orders = source(
            "orders",
            2,
            22,
            {"account_id": [10, 10, 20], "amount": [2, 3, 7], "kind": ["a", "a", "b"]},
        )

        result = execute_join_aggregation(
            [accounts, orders],
            [join("accounts", "account_id", "orders", "account_id")],
            [key("accounts", "region")],
            [
                Metric("order_count", "count", key("orders", "amount")),
                Metric("amount_sum", "sum", key("orders", "amount")),
                Metric("amount_avg", "average", key("orders", "amount")),
                Metric("amount_min", "minimum", key("orders", "amount")),
                Metric("amount_max", "maximum", key("orders", "amount")),
                Metric("kind_count", "distinct_count", key("orders", "kind")),
            ],
        )

        self.assertEqual(result["joined_row_count"], 3)
        self.assertEqual(
            result["results"],
            [
                {
                    "accounts.region": "east",
                    "order_count": 1,
                    "amount_sum": 7,
                    "amount_avg": 7,
                    "amount_min": 7,
                    "amount_max": 7,
                    "kind_count": 1,
                },
                {
                    "accounts.region": "west",
                    "order_count": 2,
                    "amount_sum": 5,
                    "amount_avg": 2.5,
                    "amount_min": 2,
                    "amount_max": 3,
                    "kind_count": 1,
                },
            ],
        )
        self.assertEqual(result["input_sources"][0]["source_sha256"], "1".zfill(64))

    def test_many_to_many_requires_approval_and_remains_bounded(self):
        left = source("left", 1, 1, {"id": [1, 1]})
        right = source("right", 2, 2, {"id": [1, 1]})
        with self.assertRaises(MultiDatasetAnalyticsError) as approval:
            execute_join_aggregation(
                [left, right],
                [join("left", "id", "right", "id")],
                [],
                [Metric("rows", "count")],
            )
        self.assertEqual(approval.exception.code, "many_to_many_confirmation_required")

        result = execute_join_aggregation(
            [left, right],
            [join("left", "id", "right", "id", allow=True)],
            [],
            [Metric("rows", "count")],
        )
        self.assertEqual(result["joined_row_count"], 4)
        self.assertTrue(result["join_evidence"][0]["many_to_many"])
        self.assertIn("explicitly approved", result["warnings"][0])

    def test_row_explosion_is_rejected_even_when_many_to_many_is_approved(self):
        left = source("left", 1, 1, {"id": [1] * 400})
        right = source("right", 2, 2, {"id": [1] * 400})

        with self.assertRaises(MultiDatasetAnalyticsError) as raised:
            execute_join_aggregation(
                [left, right],
                [join("left", "id", "right", "id", allow=True)],
                [],
                [Metric("rows", "count")],
            )
        self.assertEqual(raised.exception.code, "row_explosion_limit")

    def test_empty_inputs_and_invalid_metric_types_are_explicit(self):
        left = source("left", 1, 1, {"id": pa.array([], type=pa.int64())})
        right = source(
            "right",
            2,
            2,
            {"id": pa.array([], type=pa.int64()), "label": pa.array([], type=pa.string())},
        )
        empty = execute_join_aggregation(
            [left, right],
            [join("left", "id", "right", "id")],
            [],
            [Metric("rows", "count")],
        )
        self.assertEqual(empty["joined_row_count"], 0)
        self.assertEqual(empty["results"], [{"rows": 0}])

        with self.assertRaises(MultiDatasetAnalyticsError) as invalid:
            execute_join_aggregation(
                [left, right],
                [join("left", "id", "right", "id")],
                [],
                [Metric("total", "sum", key("right", "label"))],
            )
        self.assertEqual(invalid.exception.code, "invalid_metric_type")

    def test_joining_three_sources_is_sequential_and_retains_lineage(self):
        first = source("first", 1, 1, {"id": [1, 2], "kind": ["a", "b"]})
        second = source("second", 2, 2, {"id": [1, 2], "owner_id": [9, 8]})
        third = source("third", 3, 3, {"owner_id": [9, 8], "amount": [3, 7]})
        result = execute_join_aggregation(
            [first, second, third],
            [
                join("first", "id", "second", "id"),
                join("second", "owner_id", "third", "owner_id"),
            ],
            [key("first", "kind")],
            [Metric("total", "sum", key("third", "amount"))],
        )
        self.assertEqual(result["joined_row_count"], 2)
        self.assertEqual(result["group_count"], 2)
        self.assertEqual(len(result["input_sources"]), 3)
        self.assertEqual(
            [row["total"] for row in result["results"]],
            [3, 7],
        )


if __name__ == "__main__":
    unittest.main()
