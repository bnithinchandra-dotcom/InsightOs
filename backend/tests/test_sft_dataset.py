import json
import tempfile
import unittest
from pathlib import Path

from training.validate_sft_dataset import (
    DATASET_PATH,
    DatasetValidationError,
    validate_dataset,
)


class SftDatasetValidationTests(unittest.TestCase):
    def _write_examples(self, rows: list[dict]) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "dataset.jsonl"
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        return path

    def _rows(self) -> list[dict]:
        return [
            json.loads(line)
            for line in DATASET_PATH.read_text(encoding="utf-8").splitlines()
        ]

    def test_versioned_dataset_has_valid_splits_and_no_group_leakage(self):
        summary = validate_dataset()
        self.assertEqual(summary["dataset_version"], "insightos-agent-sft-v1")
        self.assertEqual(
            summary["split_counts"],
            {"test": 2, "train": 8, "validation": 2},
        )
        self.assertEqual(summary["scenario_groups"], 12)
        self.assertEqual(summary["human_approved_training_examples"], 0)
        self.assertFalse(summary["training_ready"])

    def test_string_identifier_is_rejected_for_strict_tool_arguments(self):
        rows = self._rows()
        call = rows[0]["messages"][2]["tool_calls"][0]
        call["function"]["arguments"]["dataset_id"] = "42"

        with self.assertRaises(DatasetValidationError) as raised:
            validate_dataset(self._write_examples(rows))

        self.assertTrue(
            any("invalid arguments for list_files" in error for error in raised.exception.errors)
        )

    def test_related_scenario_group_cannot_cross_splits(self):
        rows = self._rows()
        rows[8]["scenario_group"] = rows[0]["scenario_group"]

        with self.assertRaises(DatasetValidationError) as raised:
            validate_dataset(self._write_examples(rows))

        self.assertTrue(
            any("leaks across" in error for error in raised.exception.errors)
        )

    def test_training_requires_human_approval(self):
        with self.assertRaises(DatasetValidationError) as raised:
            validate_dataset(require_approved=True)

        self.assertEqual(
            sum("must be human-approved" in error for error in raised.exception.errors),
            8,
        )


if __name__ == "__main__":
    unittest.main()
