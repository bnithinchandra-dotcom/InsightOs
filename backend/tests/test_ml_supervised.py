import json
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pyarrow as pa

from app.ml_supervised import (
    MAX_SUPERVISED_ROWS,
    SupervisedEvaluationError,
    evaluate_supervised,
    validate_supervised_request,
)


class SupervisedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.schema = [
            {"name": "target", "type": "double"},
            {"name": "signal", "type": "double"},
            {"name": "category", "type": "string"},
        ]
        self.profile = [
            {
                "name": "target",
                "missing_value_count": 0,
                "distinct_value_count": 100,
                "distinct_count_exact": True,
            },
            {
                "name": "signal",
                "missing_value_count": 0,
                "distinct_value_count": 100,
                "distinct_count_exact": True,
            },
            {
                "name": "category",
                "missing_value_count": 0,
                "distinct_value_count": 3,
                "distinct_count_exact": True,
            },
        ]

    def test_request_requires_explicit_target_and_valid_unique_columns(self):
        with self.assertRaisesRegex(SupervisedEvaluationError, "target column cannot"):
            validate_supervised_request(
                task_type="regression",
                target_column="target",
                feature_columns=["target"],
                schema=self.schema,
                row_count=100,
            )
        with self.assertRaisesRegex(SupervisedEvaluationError, "unique"):
            validate_supervised_request(
                task_type="regression",
                target_column="target",
                feature_columns=["signal", "signal"],
                schema=self.schema,
                row_count=100,
            )
        with self.assertRaisesRegex(SupervisedEvaluationError, "not in"):
            validate_supervised_request(
                task_type="regression",
                target_column="missing",
                feature_columns=["signal"],
                schema=self.schema,
                row_count=100,
            )

    def test_identifier_feature_requires_explicit_confirmation(self):
        schema = self.schema + [{"name": "record_id", "type": "int64"}]
        profile = self.profile + [{
            "name": "record_id",
            "distinct_value_count": 100,
            "distinct_count_exact": True,
        }]
        with self.assertRaisesRegex(SupervisedEvaluationError, "identifier-like"):
            validate_supervised_request(
                task_type="regression",
                target_column="target",
                feature_columns=["record_id"],
                schema=schema,
                row_count=100,
                profile_columns=profile,
            )
        validated = validate_supervised_request(
            task_type="regression",
            target_column="target",
            feature_columns=["record_id"],
            schema=schema,
            row_count=100,
            profile_columns=profile,
            confirmed_suspicious_features=["record_id"],
        )
        self.assertEqual(validated["feature_kinds"]["record_id"], "numeric")

    def test_regression_evaluates_regularized_and_tree_candidates_against_baseline(self):
        values = np.arange(120, dtype=np.float64)
        table = pa.table({
            "target": values * 2.0 + np.sin(values / 3),
            "signal": values,
            "category": ["a", "b", "c"] * 40,
        })
        validated = validate_supervised_request(
            task_type="regression",
            target_column="target",
            feature_columns=["signal", "category"],
            schema=self.schema,
            row_count=120,
            profile_columns=self.profile,
        )
        result = evaluate_supervised(
            table=table,
            task_type="regression",
            target_column="target",
            feature_columns=["signal", "category"],
            dataset_id=1,
            file_id=2,
            table_index=0,
            source_fingerprint="a" * 64,
            artifact_sha256="b" * 64,
            feature_kinds=validated["feature_kinds"],
            confirmed_suspicious_features=["signal"],
        )
        metrics = result["evaluation"]["test_result"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["evaluation"]["split_strategy"]["type"], "random_holdout")
        self.assertGreater(metrics["test_sample_count"] if "test_sample_count" in metrics else result["evaluation"]["test_sample_count"], 0)
        self.assertTrue(np.isfinite(metrics["model"]["mae"]))
        self.assertTrue(np.isfinite(metrics["baseline"]["rmse"]))
        self.assertEqual(set(result["candidate_models"]), {"ridge", "random_forest"})
        self.assertEqual(len(result["result_sha256"]), 64)
        json.dumps(result, allow_nan=False)

    def test_classification_stratifies_and_reports_bounded_confusion_matrix(self):
        rows = 120
        labels = ["no", "yes"] * (rows // 2)
        table = pa.table({
            "target": labels,
            "signal": [0.0 if label == "no" else 1.0 for label in labels],
            "category": [None if index % 11 == 0 else f"c{index % 4}" for index in range(rows)],
        })
        schema = [
            {"name": "target", "type": "string"},
            {"name": "signal", "type": "double"},
            {"name": "category", "type": "string"},
        ]
        validated = validate_supervised_request(
            task_type="classification",
            target_column="target",
            feature_columns=["signal", "category"],
            schema=schema,
            row_count=rows,
        )
        result = evaluate_supervised(
            table=table,
            task_type="classification",
            target_column="target",
            feature_columns=["signal", "category"],
            dataset_id=1,
            file_id=2,
            table_index=0,
            source_fingerprint="a" * 64,
            artifact_sha256="b" * 64,
            feature_kinds=validated["feature_kinds"],
        )
        report = result["evaluation"]["test_result"]
        self.assertEqual(result["evaluation"]["split_strategy"]["type"], "stratified_random_holdout")
        self.assertEqual(len(report["model"]["confusion_matrix"]), 2)
        self.assertIsNotNone(report["model"]["roc_auc"])
        self.assertGreaterEqual(report["model"]["balanced_accuracy"], 0)

    def test_chronological_split_keeps_later_periods_for_testing(self):
        size = 100
        schema = [
            {"name": "target", "type": "double"},
            {"name": "signal", "type": "double"},
            {"name": "when", "type": "timestamp[us, tz=UTC]"},
        ]
        times = [
            datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=min(index, 70))
            for index in range(size)
        ]
        table = pa.table({
            "target": [float(index * 2) for index in range(size)],
            "signal": [float(index) for index in range(size)],
            "when": times,
        })
        validated = validate_supervised_request(
            task_type="regression",
            target_column="target",
            feature_columns=["signal"],
            schema=schema,
            row_count=size,
            time_column="when",
        )
        result = evaluate_supervised(
            table=table,
            task_type="regression",
            target_column="target",
            feature_columns=["signal"],
            dataset_id=1,
            file_id=2,
            table_index=0,
            source_fingerprint="a" * 64,
            artifact_sha256="b" * 64,
            feature_kinds=validated["feature_kinds"],
            time_column="when",
            confirmed_suspicious_features=["signal"],
        )
        split = result["evaluation"]["split_strategy"]
        self.assertEqual(split["type"], "chronological_holdout")
        self.assertGreater(split["test_start"], split["training_end"])
        self.assertEqual(result["evaluation"]["test_sample_count"], 30)

    def test_group_split_has_no_shared_groups(self):
        group_names = [f"g{index // 5}" for index in range(100)]
        table = pa.table({
            "target": [float(index) for index in range(100)],
            "signal": [float(index % 17) for index in range(100)],
            "group": group_names,
        })
        schema = [
            {"name": "target", "type": "double"},
            {"name": "signal", "type": "double"},
            {"name": "group", "type": "string"},
        ]
        validated = validate_supervised_request(
            task_type="regression",
            target_column="target",
            feature_columns=["signal"],
            schema=schema,
            row_count=100,
            group_column="group",
        )
        result = evaluate_supervised(
            table=table,
            task_type="regression",
            target_column="target",
            feature_columns=["signal"],
            dataset_id=1,
            file_id=2,
            table_index=0,
            source_fingerprint="a" * 64,
            artifact_sha256="b" * 64,
            feature_kinds=validated["feature_kinds"],
            group_column="group",
        )
        self.assertEqual(result["evaluation"]["split_strategy"]["type"], "group_holdout")

    def test_group_split_rejects_missing_group_values(self):
        table = pa.table({
            "target": [float(index) for index in range(100)],
            "signal": [float(index % 17) for index in range(100)],
            "group": [None if index == 0 else f"g{index // 5}" for index in range(100)],
        })
        with self.assertRaisesRegex(SupervisedEvaluationError, "requires a group"):
            evaluate_supervised(
                table=table,
                task_type="regression",
                target_column="target",
                feature_columns=["signal"],
                dataset_id=1,
                file_id=2,
                table_index=0,
                source_fingerprint="a" * 64,
                artifact_sha256="b" * 64,
                feature_kinds={"signal": "numeric"},
                group_column="group",
            )

    def test_target_copy_and_insufficient_classes_are_rejected(self):
        values = [float(index) for index in range(100)]
        table = pa.table({"target": values, "copy": values})
        with self.assertRaisesRegex(SupervisedEvaluationError, "duplicates"):
            evaluate_supervised(
                table=table,
                task_type="regression",
                target_column="target",
                feature_columns=["copy"],
                dataset_id=1,
                file_id=2,
                table_index=0,
                source_fingerprint="a" * 64,
                artifact_sha256="b" * 64,
                feature_kinds={"copy": "numeric"},
            )
        with self.assertRaisesRegex(SupervisedEvaluationError, "four rows"):
            evaluate_supervised(
                table=pa.table({
                    "target": ["a"] * 97 + ["b"] * 3,
                    "signal": list(range(100)),
                }),
                task_type="classification",
                target_column="target",
                feature_columns=["signal"],
                dataset_id=1,
                file_id=2,
                table_index=0,
                source_fingerprint="a" * 64,
                artifact_sha256="b" * 64,
                feature_kinds={"signal": "numeric"},
            )

    def test_row_limit_is_explicit(self):
        self.assertGreater(MAX_SUPERVISED_ROWS, 0)
        with self.assertRaisesRegex(SupervisedEvaluationError, "30 to"):
            validate_supervised_request(
                task_type="regression",
                target_column="target",
                feature_columns=["signal"],
                schema=self.schema,
                row_count=10,
            )


if __name__ == "__main__":
    unittest.main()
