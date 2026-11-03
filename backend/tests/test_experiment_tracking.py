import unittest
import tempfile
from pathlib import Path

from app.experiment_tracking import (
    assess_baseline_promotion,
    create_experiment_record,
    persist_experiment_record,
)


class ExperimentTrackingTests(unittest.TestCase):
    def test_record_captures_reproducibility_metadata_without_observation_values(self):
        record = create_experiment_record(
            kind="supervised",
            model={"candidate": "ridge", "version": "1"},
            dataset={
                "dataset_id": 12,
                "fingerprint": "a" * 64,
                "feature_schema": [{"name": "revenue", "type": "double"}],
            },
            split={"type": "random_holdout", "test_sample_count": 20},
            seed=42,
            configuration={"alpha": 1.0},
            metrics={"mae": 2.5},
        )

        self.assertEqual(len(record["experiment_id"]), 36)
        self.assertEqual(len(record["input_fingerprint"]), 64)
        self.assertEqual(record["dataset"]["fingerprint"], "a" * 64)
        self.assertEqual(record["random_seed"], 42)
        self.assertEqual(record["promotion"]["automatic_promotion"], False)
        self.assertNotIn("rows", record)
        self.assertNotIn("observations", record["dataset"])

    def test_promotion_policy_only_recommends_human_review_after_baseline_win(self):
        recommendation = assess_baseline_promotion(
            candidate_metrics={"mae": 4.0},
            baseline_metrics={"mae": 5.0},
            metric="mae",
            direction="minimize",
            minimum_sample_count=10,
            sample_count=20,
        )

        self.assertEqual(
            recommendation["status"],
            "eligible_for_human_review",
        )
        self.assertFalse(recommendation["automatic_promotion"])

    def test_promotion_policy_blocks_small_samples_and_missing_metrics(self):
        small_sample = assess_baseline_promotion(
            candidate_metrics={"accuracy": 0.9},
            baseline_metrics={"accuracy": 0.8},
            metric="accuracy",
            direction="maximize",
            minimum_sample_count=10,
            sample_count=9,
        )
        missing_metric = assess_baseline_promotion(
            candidate_metrics={},
            baseline_metrics={"accuracy": 0.8},
            metric="accuracy",
            direction="maximize",
            minimum_sample_count=10,
            sample_count=20,
        )

        self.assertEqual(small_sample["status"], "not_recommended")
        self.assertEqual(missing_metric["status"], "not_recommended")
        self.assertFalse(small_sample["automatic_promotion"])

    def test_persistence_is_atomic_idempotent_and_rejects_id_collisions(self):
        record = create_experiment_record(
            kind="llm",
            model={"provider": "fixture", "model": "fixture-model"},
            dataset=None,
            split={"type": "fixture"},
            seed=None,
            configuration={"prompt_version": "v1"},
            metrics={"pass_count": 1},
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            path = persist_experiment_record(record, directory)
            same_path = persist_experiment_record(record, directory)

            self.assertEqual(path, same_path)
            self.assertEqual(path.parent, directory)
            self.assertEqual(path.read_text(encoding="utf-8")[-1], "\n")
            self.assertEqual(
                [item.name for item in directory.iterdir()],
                [path.name],
            )
            conflicting_record = {**record, "metrics": {"pass_count": 0}}
            with self.assertRaisesRegex(ValueError, "already exists"):
                persist_experiment_record(conflicting_record, directory)


if __name__ == "__main__":
    unittest.main()
