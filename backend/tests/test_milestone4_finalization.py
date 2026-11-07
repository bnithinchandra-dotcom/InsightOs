from __future__ import annotations

import ast
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from training.milestone4.approval import (
    canonical_dataset_sha256,
    validate_dataset_attestation,
)
from training.validate_sft_dataset import validate_dataset as validate_sft


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = (
    ROOT
    / "training"
    / "finetuning"
    / "notebooks"
    / "qwen3_8b_orchestration_baseline.ipynb"
)
MILESTONE_ROOT = ROOT / "training" / "milestone4"


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class Milestone4FinalizationTests(unittest.TestCase):
    def _manifest(self, file_hash: str) -> dict:
        version = "insightos-test-v1"
        files = {"dataset.jsonl": file_hash}
        dataset_hash = canonical_dataset_sha256(version, files)
        source = "Owner-reported provenance; no reviewer identity supplied."
        roles = [
            "project_owner",
            "senior_business_analyst",
        ]
        return {
            "manifest_version": "insightos-dataset-validation-v1",
            "datasets": [
                {
                    "dataset_id": "test-dataset",
                    "dataset_version": version,
                    "dataset_sha256": dataset_hash,
                    "file_sha256": files,
                    "human_validation": {
                        "status": "owner_reported_dataset_level",
                        "attestations": [
                            {
                                "dataset_version": version,
                                "dataset_sha256": dataset_hash,
                                "reviewer_role": role,
                                "reviewer_identity": None,
                                "attestation_source": source,
                                "attestation_recorded_on": "2026-11-05",
                                "historical_review_date": None,
                                "review_scope": "Dataset-level test scope.",
                                "approval_state": "owner_reported_human_validated",
                                "per_example_approvals_created": False,
                            }
                            for role in roles
                        ],
                    },
                    "training_authorization": {
                        "authorized": False,
                        "reason": "Training authorization is separate.",
                    },
                }
            ],
        }

    def test_dataset_attestation_is_bound_to_exact_version_and_hash(self):
        digest = hashlib.sha256(b"dataset").hexdigest()
        manifest = self._manifest(digest)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "attestations.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            record = manifest["datasets"][0]
            verified = validate_dataset_attestation(
                path,
                dataset_id="test-dataset",
                dataset_version=record["dataset_version"],
                expected_dataset_sha256=record["dataset_sha256"],
                expected_files=record["file_sha256"],
            )
            self.assertFalse(verified["training_authorized"])
            self.assertEqual(
                verified["reviewer_roles"],
                ["project_owner", "senior_business_analyst"],
            )
            with self.assertRaisesRegex(ValueError, "file hashes"):
                validate_dataset_attestation(
                    path,
                    dataset_id="test-dataset",
                    dataset_version=record["dataset_version"],
                    expected_dataset_sha256=record["dataset_sha256"],
                    expected_files={"dataset.jsonl": "0" * 64},
                )

    def test_dataset_attestation_rejects_duplicate_reviewer_roles(self):
        digest = hashlib.sha256(b"dataset").hexdigest()
        manifest = self._manifest(digest)
        attestations = manifest["datasets"][0]["human_validation"]["attestations"]
        attestations[1]["reviewer_role"] = "project_owner"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "attestations.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            record = manifest["datasets"][0]
            with self.assertRaisesRegex(ValueError, "Reviewer roles"):
                validate_dataset_attestation(
                    path,
                    dataset_id="test-dataset",
                    dataset_version=record["dataset_version"],
                    expected_dataset_sha256=record["dataset_sha256"],
                    expected_files=record["file_sha256"],
                )

    def test_qwen8b_baseline_notebook_is_valid_json_and_python(self):
        notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(notebook["nbformat"], 4)
        self.assertTrue(notebook["cells"])
        code_cells = 0
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            code_cells += 1
            source = "".join(cell["source"])
            source = "\n".join(
                line for line in source.splitlines() if not line.startswith("%")
            )
            ast.parse(source, filename=f"{NOTEBOOK_PATH}:cell-{index}")
        self.assertGreater(code_cells, 0)
        self.assertTrue(
            all(
                not cell.get("outputs") and cell.get("execution_count") is None
                for cell in notebook["cells"]
                if cell["cell_type"] == "code"
            )
        )

    def test_generated_releases_reports_attestations_and_bundle_hashes_match(self):
        eda_report = json.loads(
            (MILESTONE_ROOT / "qwen3_4b_dataset_report_v3.json").read_text(
                encoding="utf-8"
            )
        )
        orchestration_report = json.loads(
            (
                MILESTONE_ROOT
                / "qwen3_8b_orchestration_quality_report_v3.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            eda_report["counts"],
            {
                "examples": 250,
                "train": 150,
                "validation": 50,
                "test": 50,
                "scenario_groups": 50,
                "approved_examples": 0,
                "draft_examples": 250,
            },
        )
        self.assertEqual(
            orchestration_report["counts"]["examples"],
            24,
        )
        self.assertEqual(
            {
                key: orchestration_report["counts"][key]
                for key in ("train", "validation", "test")
            },
            {"train": 13, "validation": 5, "test": 6},
        )
        self.assertEqual(
            orchestration_report["counts"]["approved_training_examples"],
            0,
        )
        self.assertEqual(
            orchestration_report["duplicates"]["scenario_group_leakage"],
            [],
        )
        self.assertEqual(
            eda_report["scenario_coverage_by_split"],
            {
                "train": ["ecommerce", "maintenance", "marketing"],
                "validation": ["fraud"],
                "test": ["healthcare"],
            },
        )
        sft_release = ROOT / "training" / "qwen3-8b" / "releases" / "insightos-agent-sft-v3"
        sft_validation = validate_sft(sft_release / "insightos-agent-sft-v3.jsonl")
        self.assertEqual(sft_validation["examples"], 24)
        self.assertEqual(sft_validation["scenario_groups"], 24)
        self.assertFalse(sft_validation["training_ready"])

        def read_jsonl(path: Path) -> list[dict]:
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        original_by_id = {
            row["id"]: row
            for row in read_jsonl(ROOT / "training" / "qwen3-8b" / "insightos-agent-sft-v1.jsonl")
        }
        final_by_id = {
            row["id"]: row
            for row in read_jsonl(sft_release / "insightos-agent-sft-v3.jsonl")
        }
        self.assertEqual(len(original_by_id), 12)
        self.assertTrue(
            all(final_by_id.get(record_id) == row for record_id, row in original_by_id.items())
        )

        attestations_path = MILESTONE_ROOT / "dataset_validation_attestations_v1.json"
        manifest = json.loads(attestations_path.read_text(encoding="utf-8"))
        for dataset_id, report in (
            ("qwen3-4b-reference-eda", eda_report),
            ("qwen3-8b-orchestration", orchestration_report),
        ):
            record = next(
                item for item in manifest["datasets"] if item["dataset_id"] == dataset_id
            )
            verified = validate_dataset_attestation(
                attestations_path,
                dataset_id=dataset_id,
                dataset_version=report["dataset_version"],
                expected_dataset_sha256=report["dataset_sha256"],
                expected_files=record["file_sha256"],
            )
            self.assertFalse(verified["training_authorized"])
            self.assertEqual(
                record["dataset_sha256"],
                canonical_dataset_sha256(
                    report["dataset_version"],
                    record["file_sha256"],
                ),
            )
            dataset_dir = (
                ROOT / "training" / "reference_eda" / "dataset_v3"
                if dataset_id == "qwen3-4b-reference-eda"
                else ROOT
                / "training"
                / "qwen3-8b"
                / "releases"
                / "insightos-agent-sft-v3"
            )
            for filename, digest in record["file_sha256"].items():
                self.assertEqual(
                    hashlib.sha256((dataset_dir / filename).read_bytes()).hexdigest(),
                    digest,
                )

        bundle_path = MILESTONE_ROOT / "qwen3-8b-baseline-bundle-v1.json"
        benchmark_path = MILESTONE_ROOT / "qwen3-8b-orchestration-benchmark-v2.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        benchmark = bundle["benchmark"]
        self.assertEqual(
            bundle["benchmark_sha256"],
            hashlib.sha256(canonical_json(benchmark)).hexdigest(),
        )
        self.assertEqual(
            bundle["benchmark_file_sha256"],
            hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            bundle["benchmark_dataset_sha256"],
            hashlib.sha256(canonical_json(benchmark["cases"])).hexdigest(),
        )
        self.assertEqual(
            bundle["tool_schemas_sha256"],
            hashlib.sha256(canonical_json(bundle["tool_schemas"])).hexdigest(),
        )
        self.assertEqual(len(benchmark["cases"]), 12)
        self.assertTrue(all(case["tool_execution"] == "never" for case in benchmark["cases"]))


if __name__ == "__main__":
    unittest.main()
