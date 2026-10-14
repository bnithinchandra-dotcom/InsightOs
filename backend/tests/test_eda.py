import hashlib
import json
import math
import tempfile
import unittest
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from app import eda
from app.eda import (
    EDAConfiguration,
    EDAError,
    analyze_normalized_file,
    eda_configuration_from_dict,
)
from app.normalization import normalize_file
from app.parsing import parse_dataset_file
from app.quality_analysis import (
    QUALITY_ANALYSIS_CONFIGURATION,
    build_contract_report,
    quality_analysis_key,
)
from app.quality_rules import MISSING_VALUE_CANDIDATE_RULE, RuleExecutionResult, RuleFinding


class FakeObjectStorage:
    def __init__(self):
        self.objects = {}
        self.buckets = set()
        self.metadata = {}

    def bucket_exists(self, bucket):
        return bucket in self.buckets

    def make_bucket(self, bucket):
        self.buckets.add(bucket)

    def stat_object(self, bucket, key):
        payload = self.objects.get((bucket, key), b"")
        return SimpleNamespace(size=len(payload))

    def put_object(self, bucket, key, stream, size, content_type=None, metadata=None):
        payload = stream.read()
        if len(payload) != size or content_type != "application/vnd.apache.parquet":
            raise ValueError("Unexpected object metadata.")
        self.objects[(bucket, key)] = payload
        self.metadata[(bucket, key)] = metadata or {}

    def get_object(self, bucket, key):
        return BytesIO(self.objects[(bucket, key)])


class EDAContractTests(unittest.TestCase):
    def test_configuration_defaults_and_serialization_are_deterministic(self):
        first = eda_configuration_from_dict({"selected_columns": ["metric"]})
        second = eda_configuration_from_dict({"selected_columns": ["metric"]})
        self.assertEqual(first, second)
        self.assertEqual(first.histogram_bins, 10)
        self.assertEqual(first.percentiles, (25.0, 50.0, 75.0))
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(
            json.dumps(first.as_dict(), sort_keys=True),
            json.dumps(second.as_dict(), sort_keys=True),
        )

    def test_configuration_rejects_unsupported_and_malformed_values(self):
        cases = (
            ({"schema_version": 2, "selected_columns": ["x"]}, "version"),
            ({"analysis_type": "correlation", "selected_columns": ["x"]}, "univariate"),
            ({"selected_columns": ["x", "x"]}, "unique"),
            ({"selected_columns": [""]}, "column names"),
            ({"selected_columns": ["x"], "histogram_bins": 101}, "Histogram bins"),
            ({"selected_columns": ["x"], "percentiles": [50, 25]}, "Percentiles"),
            ({"selected_columns": ["x"], "max_categories": 101}, "max_categories"),
            ({"selected_columns": ["x"], "max_result_bytes": 2_000_000}, "max_result_bytes"),
            ({"selected_columns": ["x"], "sampling": "random"}, "unsupported fields"),
            (
                {
                    "analysis_type": "bivariate",
                    "selected_columns": ["x", "y"],
                    "column_pairs": [1],
                },
                "two non-empty",
            ),
        )
        for configuration, message in cases:
            with self.subTest(configuration=configuration):
                with self.assertRaisesRegex(EDAError, message):
                    eda_configuration_from_dict(configuration)

    def test_configuration_bounds_columns_and_percentiles(self):
        with self.assertRaisesRegex(EDAError, "1 to"):
            EDAConfiguration()
        with self.assertRaisesRegex(EDAError, "1 to"):
            EDAConfiguration(
                selected_columns=tuple(f"column_{i}" for i in range(eda.MAX_EDA_COLUMNS + 1))
            )
        with self.assertRaisesRegex(EDAError, "Percentiles"):
            EDAConfiguration(selected_columns=("x",), percentiles=(float("nan"),))
        with self.assertRaisesRegex(EDAError, "analysis type"):
            EDAConfiguration(analysis_type=[], selected_columns=("x",))


class EDAServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.storage = FakeObjectStorage()
        self.dataset_id = 7
        self.file_id = 11

    def make_source(self, table):
        source_path = Path(self.temp_dir.name) / "source.parquet"
        pq.write_table(table, source_path)
        source_bytes = source_path.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        with source_path.open("rb") as source:
            parsed = parse_dataset_file(
                "source.parquet",
                source,
                len(source_bytes),
            )
        parsed_result = parsed.as_dict()
        normalization_result = normalize_file(
            source_path,
            "source.parquet",
            parsed_result,
            self.dataset_id,
            self.file_id,
            source_sha256,
            self.storage,
            "processed",
        )
        return {
            "parsed_result": parsed_result,
            "profile_result": parsed.profile_result,
            "normalization_result": normalization_result,
            "source_size_bytes": len(source_bytes),
            "source_sha256": source_sha256,
        }

    def run_eda(self, table, columns, **kwargs):
        source = self.make_source(table)
        configuration = EDAConfiguration(selected_columns=tuple(columns), **kwargs)
        result = analyze_normalized_file(
            storage=self.storage,
            processed_bucket="processed",
            dataset_id=self.dataset_id,
            file_id=self.file_id,
            file_status="Ready",
            normalization_status="Ready",
            source_filename="source.parquet",
            source_size_bytes=source["source_size_bytes"],
            source_sha256=source["source_sha256"],
            parsed_result=source["parsed_result"],
            normalization_result=source["normalization_result"],
            configuration=configuration,
            profile_result=source["profile_result"],
        )
        return result, source

    def test_bivariate_numeric_numeric_summary_has_expected_statistics(self):
        table = pa.table({"x": [1.0, 2.0, 3.0, 4.0], "y": [2.0, 4.0, 6.0, 8.0]})
        result, _ = self.run_eda(table, ["x", "y"], analysis_type="bivariate")
        self.assertEqual(result.analysis_type, "bivariate")
        self.assertEqual(result.provenance["status"], "completed")
        self.assertEqual(
            result.provenance["verification"]["status"],
            "verified",
        )
        self.assertEqual(result.provenance["operation_type"], "eda:bivariate")
        self.assertEqual(result.status, "completed")
        pair = result.pair_results[0]
        self.assertEqual(pair.pair_type, "numeric_numeric")
        self.assertEqual(pair.valid_observations, 4)
        self.assertEqual(pair.statistics["pearson_correlation"], 1.0)
        self.assertEqual(pair.statistics["spearman_rank_correlation"], 1.0)
        self.assertAlmostEqual(pair.statistics["covariance"], 10.0 / 3.0)

    def test_numeric_relationships_with_extreme_finite_values_are_json_safe(self):
        result, _ = self.run_eda(
            pa.table(
                {
                    "x": [-1e308, 1e308],
                    "y": [-1e308, 1e308],
                }
            ),
            ["x", "y"],
            analysis_type="bivariate",
        )
        statistics = result.pair_results[0].statistics
        self.assertEqual(statistics["pearson_correlation"], 1.0)
        self.assertEqual(statistics["spearman_rank_correlation"], 1.0)
        self.assertIsNone(statistics["covariance"])
        self.assertTrue(any("covariance exceeds" in item for item in statistics["warnings"]))
        json.dumps(result.as_dict(), allow_nan=False)
        grouped, _ = self.run_eda(
            pa.table(
                {
                    "value": [-1e308, 1e308],
                    "category": ["same", "same"],
                }
            ),
            ["value", "category"],
            analysis_type="bivariate",
        )
        group = grouped.pair_results[0].statistics["group_statistics"][0]
        self.assertEqual((group["mean"], group["median"]), (0.0, 0.0))
        self.assertIsNone(group["sample_variance_ddof_1"])
        self.assertTrue(
            any(
                "sample variance exceeds binary64" in warning
                for warning in grouped.pair_results[0].warnings
            )
        )

    def test_multivariate_correlation_matrix_is_symmetric_and_bounded(self):
        table = pa.table({"x": [1.0, 2.0, 3.0, 4.0], "y": [1.0, 2.0, 3.0, 4.0], "z": [4.0, 3.0, 2.0, 1.0]})
        result, _ = self.run_eda(table, ["x", "y", "z"], analysis_type="multivariate")
        self.assertEqual(result.analysis_type, "multivariate")
        self.assertEqual(result.status, "completed")
        matrix = result.multivariate_summary["correlation_matrix"]
        self.assertEqual(matrix["x"]["x"], 1.0)
        self.assertEqual(matrix["y"]["y"], 1.0)
        self.assertEqual(matrix["z"]["z"], 1.0)
        self.assertEqual(matrix["x"]["y"], 1.0)
        self.assertEqual(matrix["y"]["x"], 1.0)
        self.assertEqual(matrix["x"]["z"], -1.0)
        self.assertEqual(matrix["z"]["x"], -1.0)
        summary = result.multivariate_summary
        self.assertEqual(summary["eligible_features"], 3)
        self.assertEqual(summary["selected_features"], ["x", "y", "z"])
        self.assertEqual(summary["feature_count"], 3)
        self.assertFalse(summary["features_truncated"])

    def test_multivariate_diagonal_is_undefined_when_self_correlation_is_undefined(self):
        table = pa.table(
            {
                "varying": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "constant": [7.0, 7.0, 7.0, 7.0, 7.0, 7.0],
                "all_null": pa.array([None] * 6, type=pa.float64()),
                "mixed": [2.0, None, 6.0, float("nan"), 10.0, float("inf")],
            }
        )
        result, _ = self.run_eda(
            table,
            ["varying", "constant", "all_null", "mixed"],
            analysis_type="multivariate",
        )
        summary = result.multivariate_summary
        matrix = summary["correlation_matrix"]
        counts = summary["pairwise_valid_counts"]

        self.assertEqual(matrix["varying"]["varying"], 1.0)
        self.assertIsNone(matrix["constant"]["constant"])
        self.assertIsNone(matrix["all_null"]["all_null"])
        self.assertEqual(matrix["mixed"]["mixed"], 1.0)
        self.assertEqual(counts["varying"]["varying"], 6)
        self.assertEqual(counts["constant"]["constant"], 6)
        self.assertEqual(counts["all_null"]["all_null"], 0)
        self.assertEqual(counts["mixed"]["mixed"], 3)
        self.assertEqual(matrix["varying"]["mixed"], 1.0)
        self.assertEqual(matrix["mixed"]["varying"], 1.0)
        self.assertEqual(counts["varying"]["mixed"], 3)
        self.assertEqual(counts["mixed"]["varying"], 3)
        serialized = json.dumps(result.as_dict(), allow_nan=False, sort_keys=True)
        self.assertIsInstance(json.loads(serialized), dict)

    def test_numeric_categorical_group_statistics_exclude_missing_and_are_ordered(self):
        table = pa.table(
            {
                "value": [1.0, 3.0, 10.0, 14.0, None, 5.0, float("inf"), 8.0],
                "group": ["b", "b", "a", "a", "a", None, "b", "c"],
            }
        )
        result, _ = self.run_eda(
            table,
            ["value", "group"],
            analysis_type="bivariate",
        )

        pair = result.pair_results[0]
        self.assertEqual(pair.pair_type, "numeric_categorical")
        self.assertEqual(pair.valid_observations, 5)
        groups = pair.statistics["group_statistics"]
        self.assertEqual(
            [group["category"] for group in groups],
            [["str", "a"], ["str", "b"], ["str", "c"]],
        )
        self.assertEqual(
            [
                (group["count"], group["mean"], group["median"])
                for group in groups
            ],
            [(2, 12.0, 12.0), (2, 2.0, 2.0), (1, 8.0, 8.0)],
        )

    def test_numeric_categorical_explicit_pair_supports_both_column_orders(self):
        table = pa.table(
            {
                "group": ["b", "a", "b", "a", "c"],
                "value": [1.0, 10.0, 3.0, 14.0, 8.0],
            }
        )
        forward, _ = self.run_eda(
            table,
            ["value", "group"],
            analysis_type="bivariate",
            column_pairs=(("value", "group"),),
        )
        reverse, _ = self.run_eda(
            table,
            ["value", "group"],
            analysis_type="bivariate",
            column_pairs=(("group", "value"),),
        )

        forward_pair = forward.pair_results[0]
        reverse_pair = reverse.pair_results[0]
        self.assertEqual(
            (forward_pair.left_column, forward_pair.right_column),
            ("value", "group"),
        )
        self.assertEqual(
            (reverse_pair.left_column, reverse_pair.right_column),
            ("group", "value"),
        )
        self.assertEqual(forward_pair.pair_type, "numeric_categorical")
        self.assertEqual(reverse_pair.pair_type, "numeric_categorical")
        self.assertEqual(
            forward_pair.statistics["group_statistics"],
            reverse_pair.statistics["group_statistics"],
        )
        self.assertEqual(
            [
                (group["category"], group["count"], group["mean"], group["median"])
                for group in reverse_pair.statistics["group_statistics"]
            ],
            [
                (["str", "a"], 2, 12.0, 12.0),
                (["str", "b"], 2, 2.0, 2.0),
                (["str", "c"], 1, 8.0, 8.0),
            ],
        )

    def test_numeric_categorical_empty_and_singleton_groups(self):
        singleton_result, _ = self.run_eda(
            pa.table({"value": [4.0], "group": ["only"]}),
            ["value", "group"],
            analysis_type="bivariate",
        )
        singleton = singleton_result.pair_results[0]
        self.assertEqual(singleton.valid_observations, 1)
        self.assertEqual(singleton.statistics["group_statistics"][0]["count"], 1)
        self.assertEqual(singleton.statistics["group_statistics"][0]["sample_variance_ddof_1"], 0.0)
        self.assertEqual(singleton.statistics["group_statistics"][0]["sample_standard_deviation_ddof_1"], 0.0)

        empty_result, _ = self.run_eda(
            pa.table(
                {
                    "value": pa.array([None, float("nan")], type=pa.float64()),
                    "group": ["a", "b"],
                }
            ),
            ["value", "group"],
            analysis_type="bivariate",
        )
        empty = empty_result.pair_results[0]
        self.assertEqual(empty.valid_observations, 0)
        self.assertEqual(empty.statistics["group_statistics"], [])

    def test_numeric_categorical_category_limit_is_deterministic(self):
        result, _ = self.run_eda(
            pa.table(
                {
                    "value": [1.0, 2.0, 3.0],
                    "group": ["c", "a", "b"],
                }
            ),
            ["value", "group"],
            analysis_type="bivariate",
            max_categories=2,
        )
        stats = result.pair_results[0].statistics
        self.assertEqual(
            [group["category"] for group in stats["group_statistics"]],
            [["str", "a"], ["str", "b"]],
        )
        self.assertTrue(stats["warnings"])

    def test_categorical_contingency_totals_and_cramers_v(self):
        # For [[10, 20], [20, 10]], chi-square is 20/3, n is 60,
        # and Cramer's V is sqrt((20/3) / 60) = 1/3.
        left = ["A"] * 30 + ["B"] * 30
        right = ["X"] * 10 + ["Y"] * 20 + ["X"] * 20 + ["Y"] * 10
        result, _ = self.run_eda(
            pa.table({"left": left, "right": right}),
            ["left", "right"],
            analysis_type="bivariate",
        )

        stats = result.pair_results[0].statistics
        self.assertEqual(stats["total_observations"], 60)
        self.assertEqual(
            [row["counts"] for row in stats["contingency_table"]],
            [
                {'["str","X"]': 10, '["str","Y"]': 20},
                {'["str","X"]': 20, '["str","Y"]': 10},
            ],
        )
        self.assertEqual(
            stats["row_totals"],
            {'["str","A"]': 30, '["str","B"]': 30},
        )
        self.assertEqual(
            stats["column_totals"],
            {'["str","X"]': 30, '["str","Y"]': 30},
        )
        self.assertAlmostEqual(stats["cramers_v"], 1.0 / 3.0)

        perfect, _ = self.run_eda(
            pa.table(
                {
                    "left": ["A"] * 10 + ["B"] * 10,
                    "right": ["X"] * 10 + ["Y"] * 10,
                }
            ),
            ["left", "right"],
            analysis_type="bivariate",
        )
        self.assertAlmostEqual(
            perfect.pair_results[0].statistics["cramers_v"],
            1.0,
        )

    def test_categorical_contingency_missing_and_degenerate_inputs(self):
        result, _ = self.run_eda(
            pa.table(
                {
                    "left": ["A", "A", "B", None, "B"],
                    "right": ["X", None, "Y", "X", "Y"],
                }
            ),
            ["left", "right"],
            analysis_type="bivariate",
        )
        stats = result.pair_results[0].statistics
        self.assertEqual(stats["total_observations"], 3)
        self.assertEqual(sum(stats["row_totals"].values()), 3)
        self.assertEqual(sum(stats["column_totals"].values()), 3)

        degenerate, _ = self.run_eda(
            pa.table({"left": ["A", "A"], "right": ["X", "Y"]}),
            ["left", "right"],
            analysis_type="bivariate",
        )
        self.assertIsNone(degenerate.pair_results[0].statistics["cramers_v"])

        all_missing, _ = self.run_eda(
            pa.table(
                {
                    "left": pa.array([None, None], type=pa.string()),
                    "right": pa.array([None, None], type=pa.string()),
                }
            ),
            ["left", "right"],
            analysis_type="bivariate",
        )
        empty_stats = all_missing.pair_results[0].statistics
        self.assertEqual(empty_stats["contingency_table"], [])
        self.assertEqual(empty_stats["total_observations"], 0)
        self.assertIsNone(empty_stats["cramers_v"])

    def test_categorical_contingency_category_limit_bounds_table_and_totals(self):
        result, _ = self.run_eda(
            pa.table(
                {
                    "left": ["a", "a", "b", "b", "c", "c"],
                    "right": ["x", "y", "x", "z", "y", "z"],
                }
            ),
            ["left", "right"],
            analysis_type="bivariate",
            max_categories=2,
        )
        stats = result.pair_results[0].statistics
        self.assertLessEqual(len(stats["contingency_table"]), 2)
        self.assertLessEqual(len(stats["column_totals"]), 2)
        self.assertEqual(
            stats["total_observations"],
            sum(stats["row_totals"].values()),
        )
        self.assertEqual(
            stats["total_observations"],
            sum(stats["column_totals"].values()),
        )
        self.assertTrue(stats["warnings"])

    def test_contingency_truncation_removes_zero_margin_dimensions(self):
        result, _ = self.run_eda(
            pa.table(
                {
                    "left": ["A", "B", "C"],
                    "right": ["X", "Z", "Y"],
                }
            ),
            ["left", "right"],
            analysis_type="bivariate",
            max_categories=2,
        )
        stats = result.pair_results[0].statistics
        self.assertEqual(
            [row["left_category"] for row in stats["contingency_table"]],
            [["str", "A"]],
        )
        self.assertEqual(
            list(stats["contingency_table"][0]["counts"]),
            ['["str","X"]'],
        )
        self.assertEqual(stats["total_observations"], 1)
        self.assertEqual(stats["excluded_observations"], 2)
        self.assertEqual(stats["row_totals"], {'["str","A"]': 1})
        self.assertEqual(stats["column_totals"], {'["str","X"]': 1})
        self.assertIsNone(stats["cramers_v"])

    def test_long_bivariate_category_labels_are_bounded_and_collision_safe(self):
        common_prefix = "p" * (eda._MAX_DISPLAY_VALUE_LENGTH + 20)
        labels = [common_prefix + "first", common_prefix + "second"]
        grouped, _ = self.run_eda(
            pa.table({"value": [1.0, 2.0], "category": labels}),
            ["value", "category"],
            analysis_type="bivariate",
        )
        group_labels = [
            item["category"][1]
            for item in grouped.pair_results[0].statistics["group_statistics"]
        ]
        self.assertEqual(len(group_labels), 2)
        self.assertEqual(len(set(group_labels)), 2)
        self.assertTrue(all(len(label) <= eda._MAX_DISPLAY_VALUE_LENGTH for label in group_labels))
        self.assertTrue(
            all(
                any(
                    label.endswith(hashlib.sha256(value.encode("utf-8")).hexdigest())
                    for value in labels
                )
                for label in group_labels
            )
        )

        contingency, _ = self.run_eda(
            pa.table({"left": labels, "right": ["x", "y"]}),
            ["left", "right"],
            analysis_type="bivariate",
        )
        row_labels = [
            row["left_category"][1]
            for row in contingency.pair_results[0].statistics["contingency_table"]
        ]
        self.assertEqual(len(set(row_labels)), 2)
        self.assertTrue(all(len(label) <= eda._MAX_DISPLAY_VALUE_LENGTH for label in row_labels))

    def test_pair_configuration_and_execution_limits(self):
        with self.assertRaisesRegex(EDAError, "max_pairs"):
            EDAConfiguration(
                analysis_type="bivariate",
                selected_columns=("a", "b"),
                max_pairs=0,
            )
        with self.assertRaisesRegex(EDAError, "unique"):
            EDAConfiguration(
                analysis_type="bivariate",
                selected_columns=("a", "b"),
                column_pairs=(("a", "b"), ("a", "b")),
            )
        with self.assertRaisesRegex(EDAError, "two non-empty"):
            eda_configuration_from_dict(
                {
                    "analysis_type": "bivariate",
                    "selected_columns": ["a", "b"],
                    "column_pairs": [["a"]],
                }
            )
        with self.assertRaisesRegex(EDAError, "at most"):
            EDAConfiguration(
                analysis_type="bivariate",
                selected_columns=("a", "b", "c"),
                column_pairs=(("a", "b"), ("b", "c")),
                max_pairs=1,
            )
        many_pairs = tuple(
            (f"left_{index}", f"right_{index}")
            for index in range(17)
        )
        with self.assertRaisesRegex(EDAError, "at most 32 distinct columns"):
            EDAConfiguration(
                analysis_type="bivariate",
                selected_columns=("selected_left", "selected_right"),
                column_pairs=many_pairs,
            )

        table = pa.table(
            {
                "a": [1.0, 2.0, 3.0],
                "b": [2.0, 3.0, 4.0],
                "c": [3.0, 4.0, 5.0],
            }
        )
        auto_result, _ = self.run_eda(
            table,
            ["a", "b", "c"],
            analysis_type="bivariate",
            max_pairs=2,
        )
        self.assertEqual(len(auto_result.pair_results), 2)
        self.assertEqual(auto_result.multivariate_summary["eligible_pairs"], 3)
        self.assertEqual(auto_result.multivariate_summary["analyzed_pairs"], 2)
        self.assertTrue(auto_result.multivariate_summary["pairs_truncated"])
        self.assertEqual(len(auto_result.multivariate_summary["warnings"]), 1)

        observed_pair_scan = []
        original_validate_scan_size = eda._validate_scan_size

        def record_pair_scan(parquet_file, selected_columns):
            observed_pair_scan.extend(selected_columns)
            return original_validate_scan_size(parquet_file, selected_columns)

        with patch.object(eda, "_validate_scan_size", side_effect=record_pair_scan):
            explicit_result, _ = self.run_eda(
                table,
                ["a", "b", "c"],
                analysis_type="bivariate",
                column_pairs=(("a", "c"),),
                max_pairs=1,
            )
        self.assertEqual(
            [(pair.left_column, pair.right_column) for pair in explicit_result.pair_results],
            [("a", "c")],
        )
        self.assertEqual(explicit_result.multivariate_summary["pair_count"], 1)
        self.assertEqual(explicit_result.multivariate_summary["eligible_pairs"], 1)
        self.assertEqual(explicit_result.multivariate_summary["analyzed_pairs"], 1)
        self.assertFalse(explicit_result.multivariate_summary["pairs_truncated"])
        self.assertEqual(observed_pair_scan, ["a", "c"])

        with self.assertRaisesRegex(EDAError, "not present in the table"):
            self.run_eda(
                table,
                ["a", "b"],
                analysis_type="bivariate",
                column_pairs=(("a", "unknown"),),
            )

    def test_multivariate_max_features_bounds_matrix_and_selected_features(self):
        table = pa.table(
            {
                "a": [1.0, 2.0],
                "b": [2.0, 3.0],
                "c": [3.0, 4.0],
                "label": ["x", "y"],
            }
        )
        observed_scan_columns = []
        original_validate_scan_size = eda._validate_scan_size

        def record_scan_columns(parquet_file, selected_columns):
            observed_scan_columns.extend(selected_columns)
            return original_validate_scan_size(parquet_file, selected_columns)

        with patch.object(eda, "_validate_scan_size", side_effect=record_scan_columns):
            result, _ = self.run_eda(
                table,
                ["a", "b", "c", "label"],
                analysis_type="multivariate",
                max_features=2,
            )
        summary = result.multivariate_summary
        self.assertEqual(summary["selected_features"], ["a", "b"])
        self.assertEqual(summary["feature_count"], 2)
        self.assertEqual(summary["eligible_features"], 3)
        self.assertTrue(summary["features_truncated"])
        self.assertEqual(len(summary["warnings"]), 1)
        self.assertEqual(set(summary["correlation_matrix"]), {"a", "b"})
        self.assertEqual(set(summary["correlation_matrix"]["a"]), {"a", "b"})
        self.assertEqual(set(summary["pairwise_valid_counts"]), {"a", "b"})
        self.assertEqual(observed_scan_columns, ["a", "b"])
        with self.assertRaisesRegex(EDAError, "max_features"):
            EDAConfiguration(
                analysis_type="multivariate",
                selected_columns=("a", "b"),
                max_features=0,
            )

    def test_multivariate_insufficient_feature_summary_reports_numeric_selection(self):
        result, _ = self.run_eda(
            pa.table({"metric": [1.0, 2.0], "label": ["a", "b"]}),
            ["metric", "label"],
            analysis_type="multivariate",
        )
        summary = result.multivariate_summary
        self.assertEqual(result.status, "partially_completed")
        self.assertEqual(summary["eligible_features"], 1)
        self.assertEqual(summary["selected_features"], ["metric"])
        self.assertEqual(summary["numeric_features"], ["metric"])
        self.assertEqual(summary["feature_count"], 1)
        self.assertFalse(summary["features_truncated"])

    def test_dictionary_encoded_string_expansion_is_bounded_before_materialization(self):
        values = pa.DictionaryArray.from_arrays(
            pa.array([0] * 2048, type=pa.int16()),
            pa.array(["z" * 100_000]),
        )
        source = self.make_source(
            pa.table(
                {
                    "category": values,
                    "other": ["group"] * len(values),
                }
            )
        )
        original_iter_batches = pq.ParquetFile.iter_batches
        batch_types = []

        def record_dictionary_batches(parquet_file, *args, **kwargs):
            for batch in original_iter_batches(parquet_file, *args, **kwargs):
                batch_types.append(batch.column(0).type)
                yield batch

        with (
            patch.object(eda, "MAX_EDA_DECODED_VALUE_BYTES", 1024),
            patch.object(pq.ParquetFile, "iter_batches", record_dictionary_batches),
            patch.object(
                eda,
                "_arrow_to_pylist",
                side_effect=AssertionError("oversized Arrow values were materialized"),
            ) as materialize,
        ):
            with self.assertRaisesRegex(EDAError, "estimated Python bytes"):
                analyze_normalized_file(
                    storage=self.storage,
                    processed_bucket="processed",
                    dataset_id=self.dataset_id,
                    file_id=self.file_id,
                    file_status="Ready",
                    normalization_status="Ready",
                    source_filename="source.parquet",
                    source_size_bytes=source["source_size_bytes"],
                    source_sha256=source["source_sha256"],
                    parsed_result=source["parsed_result"],
                    normalization_result=source["normalization_result"],
                    configuration=EDAConfiguration(
                        analysis_type="bivariate",
                        selected_columns=("category", "other"),
                    ),
                )
        self.assertTrue(batch_types)
        self.assertTrue(all(pa.types.is_dictionary(data_type) for data_type in batch_types))
        materialize.assert_not_called()

    def test_univariate_dictionary_string_expansion_is_bounded_before_materialization(self):
        values = pa.DictionaryArray.from_arrays(
            pa.array([0] * 16, type=pa.int16()),
            pa.array(["z" * 100_000]),
        )
        source = self.make_source(pa.table({"category": values}))
        original_iter_batches = pq.ParquetFile.iter_batches
        batch_types = []

        def record_dictionary_batches(parquet_file, *args, **kwargs):
            for batch in original_iter_batches(parquet_file, *args, **kwargs):
                batch_types.append(batch.column(0).type)
                yield batch

        with (
            patch.object(eda, "MAX_EDA_DECODED_VALUE_BYTES", 1024),
            patch.object(pq.ParquetFile, "iter_batches", record_dictionary_batches),
            patch.object(
                eda,
                "_arrow_to_pylist",
                side_effect=AssertionError("oversized Arrow values were materialized"),
            ) as materialize,
        ):
            with self.assertRaisesRegex(EDAError, "estimated Python bytes"):
                analyze_normalized_file(
                    storage=self.storage,
                    processed_bucket="processed",
                    dataset_id=self.dataset_id,
                    file_id=self.file_id,
                    file_status="Ready",
                    normalization_status="Ready",
                    source_filename="source.parquet",
                    source_size_bytes=source["source_size_bytes"],
                    source_sha256=source["source_sha256"],
                    parsed_result=source["parsed_result"],
                    normalization_result=source["normalization_result"],
                    configuration=EDAConfiguration(selected_columns=("category",)),
                )
        self.assertTrue(batch_types)
        self.assertTrue(all(pa.types.is_dictionary(data_type) for data_type in batch_types))
        materialize.assert_not_called()

    def test_univariate_decoded_value_budget_accumulates_across_batches(self):
        source = self.make_source(pa.table({"category": ["x"] * 4}))
        original_materialize = eda._arrow_to_pylist
        with (
            patch.object(eda, "MAX_EDA_DECODED_VALUE_BYTES", 300),
            patch.object(eda, "EDA_BATCH_SIZE", 2),
            patch.object(
                eda,
                "_arrow_to_pylist",
                wraps=original_materialize,
            ) as materialize,
        ):
            with self.assertRaisesRegex(EDAError, "estimated Python bytes"):
                analyze_normalized_file(
                    storage=self.storage,
                    processed_bucket="processed",
                    dataset_id=self.dataset_id,
                    file_id=self.file_id,
                    file_status="Ready",
                    normalization_status="Ready",
                    source_filename="source.parquet",
                    source_size_bytes=source["source_size_bytes"],
                    source_sha256=source["source_sha256"],
                    parsed_result=source["parsed_result"],
                    normalization_result=source["normalization_result"],
                    configuration=EDAConfiguration(selected_columns=("category",)),
                )
        self.assertEqual(materialize.call_count, 1)

    def test_relationship_scan_and_result_limits_are_enforced(self):
        table = pa.table(
            {
                "left": ["a" * 1024, "b" * 1024],
                "right": ["x", "y"],
            }
        )
        with patch.object(eda, "MAX_EDA_SCAN_BYTES", 1):
            with self.assertRaisesRegex(EDAError, "uncompressed scan-size limit"):
                self.run_eda(
                    table,
                    ["left", "right"],
                    analysis_type="bivariate",
                )
        result_table = pa.table(
            {
                "left": [f"category-{index:03d}-{'x' * 40}" for index in range(30)],
                "right": [f"group-{index:03d}-{'y' * 40}" for index in range(30)],
            }
        )
        with self.assertRaisesRegex(EDAError, "serialized size limit"):
            self.run_eda(
                result_table,
                ["left", "right"],
                analysis_type="bivariate",
                max_categories=30,
                max_result_bytes=1024,
            )

    def test_relationship_batches_stop_on_budget_without_materializing_full_table(self):
        row_count = eda.EDA_BATCH_SIZE * 3
        source = self.make_source(
            pa.table(
                {
                    "value": [float(index) for index in range(row_count)],
                    "label": ["group"] * row_count,
                }
            )
        )
        observed_batches = []
        original_iter_batches = pq.ParquetFile.iter_batches

        def record_batches(parquet_file, *args, **kwargs):
            for batch in original_iter_batches(parquet_file, *args, **kwargs):
                observed_batches.append(batch.num_rows)
                yield batch

        with (
            patch.object(eda, "MAX_EDA_NUMERIC_VALUES", 2),
            patch.object(pq.ParquetFile, "iter_batches", record_batches),
            patch.object(eda.pq, "read_table", side_effect=AssertionError("whole-table read")),
        ):
            with self.assertRaisesRegex(EDAError, "limited to 2 finite values"):
                analyze_normalized_file(
                    storage=self.storage,
                    processed_bucket="processed",
                    dataset_id=self.dataset_id,
                    file_id=self.file_id,
                    file_status="Ready",
                    normalization_status="Ready",
                    source_filename="source.parquet",
                    source_size_bytes=source["source_size_bytes"],
                    source_sha256=source["source_sha256"],
                    parsed_result=source["parsed_result"],
                    normalization_result=source["normalization_result"],
                    configuration=EDAConfiguration(
                        analysis_type="bivariate",
                        selected_columns=("value", "label"),
                    ),
                )
        self.assertEqual(observed_batches, [eda.EDA_BATCH_SIZE])

    def test_relationship_category_and_cell_limits_apply_during_streaming(self):
        table = pa.table(
            {
                "left": [f"left-{index}" for index in range(8)],
                "right": [f"right-{index}" for index in range(8)],
            }
        )
        with patch.object(eda, "MAX_EDA_TRACKED_CATEGORIES", 3):
            with self.assertRaisesRegex(EDAError, "3 distinct category entries"):
                self.run_eda(table, ["left", "right"], analysis_type="bivariate")
        with patch.object(eda, "MAX_EDA_CONTINGENCY_CELLS", 2):
            with self.assertRaisesRegex(EDAError, "non-empty contingency cells"):
                self.run_eda(table, ["left", "right"], analysis_type="bivariate")
        numeric_category_table = pa.table(
            {"value": [1.0, 2.0], "group": ["x", "x"]}
        )
        with patch.object(eda, "MAX_EDA_GROUPED_VALUES", 1):
            with self.assertRaisesRegex(EDAError, "grouped values per request"):
                self.run_eda(
                    numeric_category_table,
                    ["value", "group"],
                    analysis_type="bivariate",
                )

    def test_relationship_pairwise_work_budget_is_enforced(self):
        table = pa.table(
            {"left": ["a"] * 8, "right": ["b"] * 8}
        )
        with patch.object(eda, "MAX_EDA_PAIRWISE_VALUES", 3):
            with self.assertRaisesRegex(EDAError, "pairwise values per request"):
                self.run_eda(table, ["left", "right"], analysis_type="bivariate")

    def test_failure_result_uses_analysis_specific_algorithm_version(self):
        source = self.make_source(pa.table({"x": [1.0], "y": [2.0]}))
        with patch.object(self.storage, "get_object", side_effect=OSError("storage unavailable")):
            result = analyze_normalized_file(
                storage=self.storage,
                processed_bucket="processed",
                dataset_id=self.dataset_id,
                file_id=self.file_id,
                file_status="Ready",
                normalization_status="Ready",
                source_filename="source.parquet",
                source_size_bytes=source["source_size_bytes"],
                source_sha256=source["source_sha256"],
                parsed_result=source["parsed_result"],
                normalization_result=source["normalization_result"],
                configuration=EDAConfiguration(
                    analysis_type="bivariate",
                    selected_columns=("x", "y"),
                ),
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.algorithm_version, "bivariate-1")
        self.assertEqual(result.provenance["status"], "failed")
        self.assertEqual(
            result.provenance["verification"]["status"],
            "unverifiable",
        )
        self.assertEqual(
            result.provenance["inputs"][0]["sha256"],
            source["source_sha256"],
        )
        self.assertEqual(
            result.provenance["inputs"][0]["size_bytes"],
            source["source_size_bytes"],
        )
        self.assertEqual(
            result.multivariate_summary,
            {
                "pair_count": 1,
                "analyzed_pairs": 0,
                "eligible_pairs": 1,
                "pairs_truncated": False,
                "pair_selection_mode": "all_selected_columns",
                "warnings": [],
            },
        )

    def test_failed_multivariate_result_marks_selection_metadata_unavailable(self):
        source = self.make_source(
            pa.table({"x": [1.0], "y": [2.0], "label": ["a"]})
        )
        with patch.object(self.storage, "get_object", side_effect=OSError("storage unavailable")):
            result = analyze_normalized_file(
                storage=self.storage,
                processed_bucket="processed",
                dataset_id=self.dataset_id,
                file_id=self.file_id,
                file_status="Ready",
                normalization_status="Ready",
                source_filename="source.parquet",
                source_size_bytes=source["source_size_bytes"],
                source_sha256=source["source_sha256"],
                parsed_result=source["parsed_result"],
                normalization_result=source["normalization_result"],
                configuration=EDAConfiguration(
                    analysis_type="multivariate",
                    selected_columns=("x", "y", "label"),
                    max_features=1,
                ),
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.multivariate_summary,
            {
                "selected_features": None,
                "numeric_features": None,
                "feature_count": None,
                "eligible_features": None,
                "features_truncated": None,
                "warnings": [],
            },
        )

    def test_relationship_numeric_category_and_row_budgets_are_enforced(self):
        numeric_table = pa.table({"left": [1.0, 2.0], "right": [2.0, 3.0]})
        with patch.object(eda, "MAX_EDA_NUMERIC_VALUES", 1):
            with self.assertRaisesRegex(EDAError, "limited to 1 finite values"):
                self.run_eda(
                    numeric_table,
                    ["left", "right"],
                    analysis_type="bivariate",
                )

        category_table = pa.table(
            {"left": ["a", "b", "c"], "right": ["x", "y", "z"]}
        )
        with patch.object(eda, "MAX_EDA_TRACKED_CATEGORIES", 2):
            with self.assertRaisesRegex(EDAError, "2 distinct category entries"):
                self.run_eda(
                    category_table,
                    ["left", "right"],
                    analysis_type="bivariate",
                )

        with patch.object(eda, "MAX_EDA_ROWS", 1):
            with self.assertRaisesRegex(EDAError, "limited to 1 rows"):
                self.run_eda(
                    numeric_table,
                    ["left", "right"],
                    analysis_type="bivariate",
                )

    def test_relationship_failure_cleans_up_downloaded_artifact(self):
        source = self.make_source(
            pa.table({"left": ["a" * 1024], "right": ["x"]})
        )
        arguments = {
            "storage": self.storage,
            "processed_bucket": "processed",
            "dataset_id": self.dataset_id,
            "file_id": self.file_id,
            "file_status": "Ready",
            "normalization_status": "Ready",
            "source_filename": "source.parquet",
            "source_size_bytes": source["source_size_bytes"],
            "source_sha256": source["source_sha256"],
            "parsed_result": source["parsed_result"],
            "normalization_result": source["normalization_result"],
            "configuration": EDAConfiguration(
                analysis_type="bivariate",
                selected_columns=("left", "right"),
            ),
        }
        original = eda.tempfile.NamedTemporaryFile
        paths = []

        def track_temporary_file(*args, **kwargs):
            handle = original(*args, **kwargs)
            paths.append(Path(handle.name))
            return handle

        with (
            patch.object(
                eda.tempfile,
                "NamedTemporaryFile",
                side_effect=track_temporary_file,
            ),
            patch.object(eda, "MAX_EDA_SCAN_BYTES", 1),
            self.assertRaisesRegex(EDAError, "uncompressed scan-size limit"),
        ):
            analyze_normalized_file(**arguments)
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())

    def test_numeric_statistics_histogram_percentiles_and_box_plot(self):
        table = pa.table({"metric": [1.0, 2.0, 3.0, 4.0]})
        result, _ = self.run_eda(
            table,
            ["metric"],
            histogram_bins=2,
        )

        column = result.columns[0]
        stats = column.statistics
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.exact)
        self.assertEqual(result.rows_analyzed, 4)
        self.assertEqual(stats["count"], 4)
        self.assertEqual(stats["mean"], 2.5)
        self.assertEqual(stats["median"], 2.5)
        self.assertEqual(stats["minimum"], 1.0)
        self.assertEqual(stats["maximum"], 4.0)
        self.assertEqual(stats["range"], 3.0)
        self.assertAlmostEqual(stats["sample_variance_ddof_1"], 5 / 3)
        self.assertAlmostEqual(stats["sample_standard_deviation_ddof_1"], math.sqrt(5 / 3))
        self.assertEqual(stats["percentiles_linear"]["25.0"], 1.75)
        self.assertEqual(stats["percentiles_linear"]["75.0"], 3.25)
        self.assertEqual(stats["histogram"]["edges"], [1.0, 2.5, 4.0])
        self.assertEqual(stats["histogram"]["counts"], [2, 2])
        self.assertEqual(stats["box_plot"]["lower_whisker"], 1.0)
        self.assertEqual(stats["box_plot"]["upper_whisker"], 4.0)

    def test_numeric_null_nan_and_infinity_are_separated(self):
        table = pa.table({"metric": pa.array([1.0, None, float("nan"), float("inf"), 3.0])})
        result, _ = self.run_eda(table, ["metric"])
        column = result.columns[0]
        self.assertEqual(column.non_null_count, 4)
        self.assertEqual(column.null_count, 1)
        self.assertEqual(column.statistics["finite_count"], 2)
        self.assertEqual(column.statistics["non_finite_count"], 2)
        self.assertEqual(column.statistics["mean"], 2.0)
        self.assertEqual(sum(column.statistics["histogram"]["counts"]), 2)

    def test_numeric_range_beyond_binary64_is_reported_without_false_failure(self):
        result, _ = self.run_eda(
            pa.table({"metric": [-1e308, 1e308]}),
            ["metric"],
        )
        column = result.columns[0]
        self.assertEqual(result.status, "completed")
        self.assertEqual(column.statistics["mean"], 0.0)
        self.assertEqual(column.statistics["median"], 0.0)
        self.assertIsNone(column.statistics["range"])
        self.assertIsNone(column.statistics["sample_variance_ddof_1"])
        self.assertIsNone(column.statistics["histogram"])
        self.assertTrue(any("variance exceeds binary64" in warning for warning in column.warnings))

    def test_sample_variance_avoids_intermediate_square_overflow(self):
        result, _ = self.run_eda(
            pa.table({"metric": [0.0, 0.0, 0.0, 2e154]}),
            ["metric"],
        )
        statistics = result.columns[0].statistics
        self.assertAlmostEqual(statistics["sample_variance_ddof_1"] / 1e308, 1.0)
        self.assertAlmostEqual(
            statistics["sample_standard_deviation_ddof_1"] / 1e154,
            1.0,
        )

    def test_empty_all_null_constant_and_single_numeric_inputs(self):
        cases = (
            (pa.array([], type=pa.float64()), 0, None, None),
            (pa.array([None, None], type=pa.float64()), 0, None, None),
            (pa.array([4.0, 4.0, 4.0]), 3, 4.0, 0.0),
            (pa.array([9.0]), 1, 9.0, None),
        )
        for values, expected_count, expected_mean, expected_variance in cases:
            with self.subTest(values=values.to_pylist()):
                result, _ = self.run_eda(pa.table({"metric": values}), ["metric"])
                stats = result.columns[0].statistics
                self.assertEqual(stats["count"], expected_count)
                self.assertEqual(stats["mean"], expected_mean)
                self.assertEqual(stats["sample_variance_ddof_1"], expected_variance)

    def test_categorical_frequencies_are_deterministic_and_bounded(self):
        result, _ = self.run_eda(
            pa.table({"label": ["b", "a", "a", "b", "c"]}),
            ["label"],
            max_categories=2,
        )
        stats = result.columns[0].statistics
        self.assertEqual(
            stats["frequencies"],
            [
                {"value": "a", "count": 2, "percentage_of_non_null": 40.0},
                {"value": "b", "count": 2, "percentage_of_non_null": 40.0},
            ],
        )
        self.assertEqual(stats["distinct_value_count"], 3)
        self.assertTrue(stats["frequencies_truncated"])
        self.assertEqual(stats["empty_string_count"], 0)

    def test_long_categorical_values_are_not_retained_unbounded(self):
        result, _ = self.run_eda(
            pa.table({"label": ["x" * (eda._MAX_DISPLAY_VALUE_LENGTH + 1)]}),
            ["label"],
        )
        stats = result.columns[0].statistics
        self.assertIsNone(stats["distinct_value_count"])
        self.assertTrue(stats["category_tracking_truncated"])
        self.assertEqual(stats["frequencies"], [])

    def test_boolean_and_empty_string_counts(self):
        result, _ = self.run_eda(
            pa.table(
                {
                    "flag": pa.array([True, False, True, None], type=pa.bool_()),
                    "label": ["", "x", "", None],
                }
            ),
            ["flag", "label"],
        )
        flag, label = result.columns
        self.assertEqual(flag.statistics["distinct_value_count"], 2)
        self.assertEqual(flag.statistics["frequencies"][0]["value"], True)
        self.assertEqual(label.statistics["empty_string_count"], 2)
        self.assertEqual(label.null_count, 1)

    def test_date_and_timestamp_ranges_use_actual_arrow_types(self):
        table = pa.table(
            {
                "day": pa.array([date(2024, 1, 1), date(2024, 1, 3)], type=pa.date32()),
                "instant": pa.array(
                    [
                        datetime(2024, 1, 1, tzinfo=timezone.utc),
                        datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
                    ],
                    type=pa.timestamp("us", tz="UTC"),
                ),
            }
        )
        result, _ = self.run_eda(table, ["day", "instant"])
        day, instant = result.columns
        self.assertEqual(day.statistics["coverage_days"], 2)
        self.assertIsNone(day.statistics["coverage_seconds"])
        self.assertEqual(instant.statistics["coverage_seconds"], 7200.0)

    def test_unsupported_arrow_type_is_explicitly_skipped(self):
        result, _ = self.run_eda(
            pa.table({"nested": pa.array([[1, 2], [3]], type=pa.list_(pa.int64()))}),
            ["nested"],
        )
        self.assertEqual(result.status, "partially_completed")
        self.assertEqual(result.columns[0].status, "unsupported")
        self.assertEqual(result.analyzed_columns, ())

    def test_artifact_checksum_mismatch_is_rejected(self):
        source = self.make_source(pa.table({"metric": [1.0, 2.0]}))
        table_metadata = source["normalization_result"]["tables"][0]
        object_key = table_metadata["object_key"]
        payload = bytearray(self.storage.objects[("processed", object_key)])
        payload[-1] ^= 1
        self.storage.objects[("processed", object_key)] = bytes(payload)
        with self.assertRaisesRegex(EDAError, "checksum verification"):
            analyze_normalized_file(
                storage=self.storage,
                processed_bucket="processed",
                dataset_id=self.dataset_id,
                file_id=self.file_id,
                file_status="Ready",
                normalization_status="Ready",
                source_filename="source.parquet",
                source_size_bytes=source["source_size_bytes"],
                source_sha256=source["source_sha256"],
                parsed_result=source["parsed_result"],
                normalization_result=source["normalization_result"],
                configuration=EDAConfiguration(selected_columns=("metric",)),
            )

    def test_normalization_state_and_missing_artifact_fail_safely(self):
        source = self.make_source(pa.table({"metric": [1.0]}))
        arguments = {
            "storage": self.storage,
            "processed_bucket": "processed",
            "dataset_id": self.dataset_id,
            "file_id": self.file_id,
            "file_status": "Ready",
            "normalization_status": "Ready",
            "source_filename": "source.parquet",
            "source_size_bytes": source["source_size_bytes"],
            "source_sha256": source["source_sha256"],
            "parsed_result": source["parsed_result"],
            "normalization_result": source["normalization_result"],
            "configuration": EDAConfiguration(selected_columns=("metric",)),
        }
        with self.assertRaisesRegex(EDAError, "ready normalization"):
            analyze_normalized_file(**{**arguments, "normalization_status": "Failed"})
        object_key = source["normalization_result"]["tables"][0]["object_key"]
        del self.storage.objects[("processed", object_key)]
        failure = analyze_normalized_file(**arguments)
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failure.failure.code, "artifact_analysis_failed")

    def test_unknown_column_is_rejected(self):
        source = self.make_source(pa.table({"metric": [1.0]}))
        with self.assertRaisesRegex(EDAError, "not in the normalized schema"):
            analyze_normalized_file(
                storage=self.storage,
                processed_bucket="processed",
                dataset_id=self.dataset_id,
                file_id=self.file_id,
                file_status="Ready",
                normalization_status="Ready",
                source_filename="source.parquet",
                source_size_bytes=source["source_size_bytes"],
                source_sha256=source["source_sha256"],
                parsed_result=source["parsed_result"],
                normalization_result=source["normalization_result"],
                configuration=EDAConfiguration(selected_columns=("absent",)),
            )

    def test_profile_is_reused_only_when_counts_and_schema_match(self):
        table = pa.table({"metric": [1.0, None, 3.0]})
        source = self.make_source(table)
        configuration = EDAConfiguration(selected_columns=("metric",))
        arguments = {
            "storage": self.storage,
            "processed_bucket": "processed",
            "dataset_id": self.dataset_id,
            "file_id": self.file_id,
            "file_status": "Ready",
            "normalization_status": "Ready",
            "source_filename": "source.parquet",
            "source_size_bytes": source["source_size_bytes"],
            "source_sha256": source["source_sha256"],
            "parsed_result": source["parsed_result"],
            "normalization_result": source["normalization_result"],
            "configuration": configuration,
        }
        valid = analyze_normalized_file(
            **arguments,
            profile_result=source["profile_result"],
        )
        self.assertNotIn("Stored profile", " ".join(valid.warnings))

        stale_profile = json.loads(json.dumps(source["profile_result"]))
        stale_profile["tables"][0]["columns"][0]["missing_value_count"] = 0
        stale = analyze_normalized_file(**arguments, profile_result=stale_profile)
        self.assertTrue(any("Stored profile was incompatible" in warning for warning in stale.warnings))
        self.assertEqual(stale.columns[0].null_count, 1)

    def test_only_current_quality_findings_are_returned_as_bounded_references(self):
        source = self.make_source(pa.table({"metric": [1.0, None, 3.0]}))
        normalization = source["normalization_result"]
        analysis_key, configuration_sha256 = quality_analysis_key(
            source["source_sha256"],
            normalization,
            QUALITY_ANALYSIS_CONFIGURATION,
        )
        scope = {
            "table_index": 0,
            "table_name": normalization["tables"][0]["table_name"],
            "column_position": 0,
            "column_name": "metric",
        }
        finding = RuleFinding(
            classification="suspected_issue",
            title="Missing values",
            description="The selected column contains missing values.",
            scope=scope,
            affected_count=1,
            denominator_basis="rows",
            denominator_count=3,
            denominator_reason=None,
            evidence={},
            confidence=0.9,
            confidence_basis="Synthetic test evidence.",
            recommended_investigation="Review source completeness.",
        )
        rule = MISSING_VALUE_CANDIDATE_RULE
        execution = RuleExecutionResult(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            status="Completed",
            severity=rule.severity,
            findings=(finding,),
            affected_count=1,
            denominator_count=3,
            rows_considered=3,
            budget_limit=10,
            budget_used=1,
            reason=None,
        )
        started = datetime.now(timezone.utc)
        report = build_contract_report(
            source["source_sha256"],
            normalization,
            analysis_key,
            configuration_sha256,
            started,
            started,
            [execution],
        )
        arguments = {
            "storage": self.storage,
            "processed_bucket": "processed",
            "dataset_id": self.dataset_id,
            "file_id": self.file_id,
            "file_status": "Ready",
            "normalization_status": "Ready",
            "source_filename": "source.parquet",
            "source_size_bytes": source["source_size_bytes"],
            "source_sha256": source["source_sha256"],
            "parsed_result": source["parsed_result"],
            "normalization_result": normalization,
            "configuration": EDAConfiguration(selected_columns=("metric",)),
        }
        current = analyze_normalized_file(**arguments, quality_report=report)
        self.assertEqual(
            current.columns[0].related_quality_findings[0]["finding_id"],
            report["findings"][0]["finding_id"],
        )
        stale_report = json.loads(json.dumps(report))
        stale_report["analysis"]["source_sha256"] = "0" * 64
        stale = analyze_normalized_file(**arguments, quality_report=stale_report)
        self.assertEqual(stale.columns[0].related_quality_findings, ())
        self.assertTrue(any("quality findings were stale" in warning for warning in stale.warnings))

    def test_quality_findings_from_other_tables_are_not_attached(self):
        findings = [
            {
                "finding_id": "selected-table-finding",
                "rule_id": "missing-value-candidate",
                "classification": "suspected_issue",
                "severity": "medium",
                "scope": {"table_index": 0, "column_name": "metric"},
            },
            {
                "finding_id": "other-table-finding",
                "rule_id": "missing-value-candidate",
                "classification": "suspected_issue",
                "severity": "medium",
                "scope": {"table_index": 1, "column_name": "metric"},
            },
        ]
        with patch.object(
            eda,
            "_verified_quality_findings",
            return_value=(findings, []),
        ):
            result, _ = self.run_eda(pa.table({"metric": [1.0]}), ["metric"])

        references = result.columns[0].related_quality_findings
        self.assertEqual(
            [reference["finding_id"] for reference in references],
            ["selected-table-finding"],
        )

    def test_analysis_is_repeatable_and_does_not_mutate_artifact(self):
        table = pa.table({"metric": [2.0, 5.0, 8.0]})
        source = self.make_source(table)
        config = EDAConfiguration(selected_columns=("metric",))
        arguments = {
            "storage": self.storage,
            "processed_bucket": "processed",
            "dataset_id": self.dataset_id,
            "file_id": self.file_id,
            "file_status": "Ready",
            "normalization_status": "Ready",
            "source_filename": "source.parquet",
            "source_size_bytes": source["source_size_bytes"],
            "source_sha256": source["source_sha256"],
            "parsed_result": source["parsed_result"],
            "normalization_result": source["normalization_result"],
            "configuration": config,
            "profile_result": source["profile_result"],
        }
        object_key = source["normalization_result"]["tables"][0]["object_key"]
        original_bytes = self.storage.objects[("processed", object_key)]
        first = analyze_normalized_file(**arguments).as_dict()
        second = analyze_normalized_file(**arguments).as_dict()
        first.pop("executed_at")
        second.pop("executed_at")
        first["provenance"].pop("completed_at")
        second["provenance"].pop("completed_at")
        self.assertEqual(first, second)
        self.assertEqual(
            self.storage.objects[("processed", object_key)],
            original_bytes,
        )

    def test_numeric_and_result_limits_are_enforced(self):
        source = self.make_source(pa.table({"metric": [1.0, 2.0]}))
        with patch.object(eda, "MAX_EDA_NUMERIC_VALUES", 1):
            with self.assertRaisesRegex(EDAError, "limited to 1 finite values"):
                analyze_normalized_file(
                    storage=self.storage,
                    processed_bucket="processed",
                    dataset_id=self.dataset_id,
                    file_id=self.file_id,
                    file_status="Ready",
                    normalization_status="Ready",
                    source_filename="source.parquet",
                    source_size_bytes=source["source_size_bytes"],
                    source_sha256=source["source_sha256"],
                    parsed_result=source["parsed_result"],
                    normalization_result=source["normalization_result"],
                    configuration=EDAConfiguration(selected_columns=("metric",)),
                )
        with self.assertRaisesRegex(EDAError, "serialized size limit"):
            analyze_normalized_file(
                storage=self.storage,
                processed_bucket="processed",
                dataset_id=self.dataset_id,
                file_id=self.file_id,
                file_status="Ready",
                normalization_status="Ready",
                source_filename="source.parquet",
                source_size_bytes=source["source_size_bytes"],
                source_sha256=source["source_sha256"],
                parsed_result=source["parsed_result"],
                normalization_result=source["normalization_result"],
                configuration=EDAConfiguration(
                    selected_columns=("metric",),
                    max_result_bytes=1024,
                ),
            )

    def test_uncompressed_selected_column_scan_limit_is_enforced(self):
        source = self.make_source(pa.table({"label": ["x" * 1024]}))
        with patch.object(eda, "MAX_EDA_SCAN_BYTES", 1):
            with self.assertRaisesRegex(EDAError, "uncompressed scan-size limit"):
                analyze_normalized_file(
                    storage=self.storage,
                    processed_bucket="processed",
                    dataset_id=self.dataset_id,
                    file_id=self.file_id,
                    file_status="Ready",
                    normalization_status="Ready",
                    source_filename="source.parquet",
                    source_size_bytes=source["source_size_bytes"],
                    source_sha256=source["source_sha256"],
                    parsed_result=source["parsed_result"],
                    normalization_result=source["normalization_result"],
                    configuration=EDAConfiguration(selected_columns=("label",)),
                )

    def test_invalid_storage_key_or_bucket_fails_before_storage_access(self):
        source = self.make_source(pa.table({"metric": [1.0]}))
        normalization = json.loads(json.dumps(source["normalization_result"]))
        normalization["tables"][0]["object_key"] = "../outside.parquet"
        with self.assertRaises(EDAError):
            analyze_normalized_file(
                storage=self.storage,
                processed_bucket="processed",
                dataset_id=self.dataset_id,
                file_id=self.file_id,
                file_status="Ready",
                normalization_status="Ready",
                source_filename="source.parquet",
                source_size_bytes=source["source_size_bytes"],
                source_sha256=source["source_sha256"],
                parsed_result=source["parsed_result"],
                normalization_result=normalization,
                configuration=EDAConfiguration(selected_columns=("metric",)),
            )


if __name__ == "__main__":
    unittest.main()
