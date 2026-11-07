from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from training.milestone4.prepare_milestone4 import (
    BENCHMARK_PATH,
    EDA_CORRECTED_RELEASE_DIR,
    ORCHESTRATION_RELEASE,
    _benchmark_cases,
    _build_orchestration_release,
    _duplicate_report,
)
from training.reference_eda.train_lora import preflight
from training.reference_eda.validate_dataset import validate_dataset as validate_eda
from training.validate_sft_dataset import validate_dataset


class Milestone4TrainingReadinessTests(unittest.TestCase):
    def test_orchestration_v2_is_schema_valid_and_draft_only(self):
        records, report = _build_orchestration_release()

        self.assertEqual(report["examples"], 24)
        self.assertEqual(report["split_counts"], {"test": 6, "train": 13, "validation": 5})
        self.assertEqual(report["scenario_groups"], 24)
        self.assertEqual(report["human_approved_training_examples"], 0)
        self.assertFalse(report["training_ready"])
        self.assertEqual(
            sum(record["validation_status"] == "draft" for record in records),
            24,
        )
        self.assertEqual(
            sum(record["source"]["kind"] == "synthetic" for record in records),
            24,
        )

    def test_supplement_has_contract_grounded_examples(self):
        records, _ = _build_orchestration_release()
        supplement_ids = {
            "typed-data-intelligence-handoff",
            "engine-operation-not-replayed-after-resume",
            "stale-schema-evidence-abstention",
            "join-recommendation-is-not-approval",
        }
        selected = [record for record in records if record["id"] in supplement_ids]
        self.assertEqual(len(selected), len(supplement_ids))
        self.assertTrue(all(record["validation_status"] == "draft" for record in selected))
        self.assertTrue(all(record["source"]["kind"] == "synthetic" for record in selected))

    def test_independent_benchmark_covers_all_required_capabilities(self):
        cases = _benchmark_cases()
        self.assertEqual(len(cases), 12)
        self.assertEqual(
            len({case["category"] for case in cases}),
            12,
        )
        self.assertTrue(all(case["split"] == "independent_test" for case in cases))
        self.assertTrue(all(case.get("human_review_required") is not None for case in cases))

    def test_duplicate_report_detects_exact_and_near_duplicate_text(self):
        records = [
            {
                "id": "one",
                "split": "train",
                "messages": [{"role": "user", "content": "Show dataset profile"}],
            },
            {
                "id": "two",
                "split": "test",
                "messages": [{"role": "user", "content": "Show dataset profile"}],
            },
        ]
        report = _duplicate_report(records)
        self.assertEqual(len(report["exact_duplicate_pairs"]), 1)
        self.assertEqual(len(report["cross_split_near_duplicate_pairs"]), 0)

    def test_reference_eda_configs_resolve_to_matching_versioned_release(self):
        release_report = validate_eda(
            EDA_CORRECTED_RELEASE_DIR,
            require_approved=False,
        )
        config_directory = (
            Path(__file__).resolve().parents[1]
            / "training"
            / "reference_eda"
            / "configs"
        )
        for config_path in config_directory.glob("qwen3-*.json"):
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["dataset_version"], "insightos-reference-eda-v3")
            self.assertTrue(config["dataset_directory"].endswith("dataset_v3"))

        report = preflight(
            config_directory / "qwen3-4b.json"
        )

        self.assertEqual(
            report["dataset_directory"].replace("\\", "/").rsplit("/", 1)[-1],
            "dataset_v3",
        )
        self.assertFalse(
            any(
                "Training data path must match" in blocker
                or "Configured dataset version does not match" in blocker
                for blocker in report["blockers"]
            )
        )
        self.assertFalse(report["ready"])
        self.assertEqual(release_report["examples"], 250)
        self.assertTrue(release_report["release_manifest_validated"])

    def test_corrected_eda_release_has_domain_holdout_and_no_cross_split_duplicates(self):
        release_report = validate_eda(
            EDA_CORRECTED_RELEASE_DIR,
            require_approved=False,
        )
        records = [
            json.loads(line)
            for filename in ("train.jsonl", "validation.jsonl", "held_out.jsonl")
            for line in (EDA_CORRECTED_RELEASE_DIR / filename)
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        duplicates = _duplicate_report(records)
        expected_coverage = {
            "train": ["ecommerce", "maintenance", "marketing"],
            "validation": ["fraud"],
            "test": ["healthcare"],
        }

        self.assertEqual(
            release_report["split_counts"],
            {"train": 150, "validation": 50, "test": 50},
        )
        self.assertEqual(
            release_report["scenario_coverage_by_split"],
            expected_coverage,
        )
        self.assertEqual(duplicates["exact_duplicate_pairs"], [])
        self.assertEqual(duplicates["cross_split_near_duplicate_pairs"], [])
        self.assertEqual(release_report["approved_examples"], 0)
        self.assertTrue(release_report["all_examples_remain_drafts"])

        manifest = json.loads(
            (EDA_CORRECTED_RELEASE_DIR / "release_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        for filename, expected_hash in manifest["release_file_sha256"].items():
            contents = (EDA_CORRECTED_RELEASE_DIR / filename).read_bytes()
            self.assertEqual(hashlib.sha256(contents).hexdigest(), expected_hash)
        dataset_report = json.loads(
            (EDA_CORRECTED_RELEASE_DIR / "dataset_report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(dataset_report["training_ready"])

    def test_release_paths_are_versioned_and_additive(self):
        self.assertTrue(ORCHESTRATION_RELEASE.name.endswith("-v2.jsonl"))
        self.assertTrue(BENCHMARK_PATH.name.endswith("-v1.json"))
        report = json.loads(
            (EDA_CORRECTED_RELEASE_DIR / "dataset_report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["dataset_version"], "insightos-reference-eda-v3")
        self.assertTrue(EDA_CORRECTED_RELEASE_DIR.exists())


if __name__ == "__main__":
    unittest.main()
