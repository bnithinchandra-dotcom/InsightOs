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
