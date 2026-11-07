from __future__ import annotations

import ast
import hashlib
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.tools import TOOL_DEFINITIONS
from app.agent.specialist_agents import SpecialistInputContract
from training.milestone5.import_kaggle_results import validate_archive


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MILESTONE5_ROOT = BACKEND_ROOT / "training" / "milestone5"
NOTEBOOK_PATH = (
    MILESTONE5_ROOT / "notebooks" / "qwen3_8b_baseline_v6.ipynb"
)
CORRECTED_NOTEBOOK_PATH = (
    MILESTONE5_ROOT / "notebooks" / "qwen3_8b_baseline_v7.ipynb"
)
MODEL_ID = "Qwen/Qwen3-8B"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class FakePackageMetadata:
    class PackageNotFoundError(Exception):
        pass

    @staticmethod
    def version(package: str) -> str:
        pins = {
            "transformers": "4.51.3",
            "accelerate": "1.6.0",
            "bitsandbytes": "0.48.2",
            "safetensors": "0.5.3",
            "jsonschema": "4.26.0",
        }
        return pins[package]


class Milestone5BaselinePreparationTests(unittest.TestCase):
    def test_pinned_bundle_matches_model_tools_contracts_and_benchmark(self):
        bundle_bytes = (
            MILESTONE5_ROOT / "qwen3-8b-baseline-bundle-v6.json"
        ).read_bytes()
        bundle = json.loads(bundle_bytes)
        benchmark = bundle["benchmark"]
        actual_tools = sorted(
            [definition.provider_schema() for definition in TOOL_DEFINITIONS],
            key=lambda item: item["function"]["name"],
        )
        self.assertEqual(bundle["bundle_version"], "insightos-qwen3-8b-baseline-bundle-v6")
        self.assertEqual(bundle["model"]["id"], MODEL_ID)
        self.assertEqual(bundle["model"]["revision"], MODEL_REVISION)
        self.assertEqual(bundle["tokenizer"]["id"], MODEL_ID)
        self.assertEqual(bundle["tokenizer"]["revision"], MODEL_REVISION)
        self.assertIsNone(bundle["model"]["adapter"])
        self.assertEqual(bundle["tool_schemas"], actual_tools)
        self.assertEqual(
            bundle["structured_output_contracts"]["specialist_input"],
            SpecialistInputContract.model_json_schema(),
        )
        self.assertEqual(
            bundle["tool_schemas_sha256"],
            hashlib.sha256(canonical_json(actual_tools)).hexdigest(),
        )
        self.assertEqual(
            bundle["benchmark_sha256"],
            hashlib.sha256(canonical_json(benchmark)).hexdigest(),
        )
        self.assertEqual(
            bundle["benchmark_file_sha256"],
            hashlib.sha256(
                (MILESTONE5_ROOT / "qwen3-8b-orchestration-benchmark-v4.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            bundle["structured_output_contracts_sha256"],
            hashlib.sha256(
                canonical_json(bundle["structured_output_contracts"])
            ).hexdigest(),
        )
        self.assertEqual(
            bundle["kaggle_environment"]["packages"]["bitsandbytes"],
            "0.48.2",
        )
        self.assertEqual(
            bundle["kaggle_environment_sha256"],
            hashlib.sha256(
                (MILESTONE5_ROOT / "kaggle-environment-v4.json").read_bytes()
            ).hexdigest(),
        )

    def test_benchmark_v4_preserves_ids_and_has_actual_expected_outcomes(self):
        prior_benchmark = json.loads(
            (MILESTONE5_ROOT / "qwen3-8b-orchestration-benchmark-v3.json").read_text(
                encoding="utf-8"
            )
        )
        original = json.loads(
            (
                BACKEND_ROOT
                / "training"
                / "milestone4"
                / "qwen3-8b-orchestration-benchmark-v2.json"
            ).read_text(encoding="utf-8")
        )
        benchmark = json.loads(
            (MILESTONE5_ROOT / "qwen3-8b-orchestration-benchmark-v4.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(benchmark["cases"], prior_benchmark["cases"])
        self.assertEqual(
            [case["id"] for case in benchmark["cases"]],
            [case["id"] for case in original["cases"]],
        )
        self.assertEqual(len(benchmark["cases"]), 12)
        self.assertEqual(
            len({case["category"] for case in benchmark["cases"]}),
            12,
        )
        self.assertTrue(
            all(
                case.get("expected", {}).get("decision")
                in {"continue", "clarify", "abstain"}
                and case.get("expected", {}).get("safety_checks")
                and case.get("split") == "independent_test"
                and case.get("tool_execution") == "never"
                for case in benchmark["cases"]
            )
        )
        resume_case = next(
            case
            for case in benchmark["cases"]
            if case["id"] == "resume-without-engine-replay"
        )
        self.assertEqual(resume_case["tools"], ["get_eda_result"])
        self.assertEqual(
            resume_case["expected"]["tool_calls_exact"][0]["name"],
            "get_eda_result",
        )
        self.assertIn(
            "run_eda_analysis",
            resume_case["expected"]["forbidden_tool_names"],
        )
        independence = benchmark["independence_audit"]
        self.assertEqual(independence["cross_corpus_exact_duplicate_pairs"], 0)
        self.assertEqual(independence["cross_corpus_near_duplicate_pairs"], 0)
        self.assertEqual(independence["within_benchmark_exact_duplicate_pairs"], 0)
        self.assertEqual(independence["within_benchmark_near_duplicate_pairs"], 0)

    def test_prepared_report_is_pending_without_placeholder_metrics(self):
        report = json.loads(
            (MILESTONE5_ROOT / "qwen3-8b-baseline-report-v5.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["status"], "pending")
        self.assertIsNone(report["metrics"])
        self.assertIsNone(report["execution_artifact"])
        self.assertFalse(report["training_authorized"])
        self.assertEqual(
            report["training_readiness"],
            "NOT READY — BASELINE INCOMPLETE",
        )
        readiness = json.loads(
            (MILESTONE5_ROOT / "training-readiness-assessment-v5.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            readiness["recommendation"],
            "NOT READY — BASELINE INCOMPLETE",
        )
        self.assertFalse(readiness["checks"]["baseline_execution_completed"])
        self.assertFalse(readiness["checks"]["training_authorization"])
        self.assertFalse(readiness["training_authorized"])

    def test_sft_v3_manifest_file_and_canonical_dataset_hashes_match(self):
        release_dir = (
            BACKEND_ROOT
            / "training"
            / "qwen3-8b"
            / "releases"
            / "insightos-agent-sft-v3"
        )
        manifest = json.loads(
            (release_dir / "release_manifest.json").read_text(encoding="utf-8")
        )
        dataset_bytes = (release_dir / manifest["dataset_file"]).read_bytes()
        self.assertEqual(
            hashlib.sha256(dataset_bytes).hexdigest(),
            manifest["dataset_file_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(
                canonical_json(
                    {
                        "dataset_version": manifest["dataset_version"],
                        "files": {
                            manifest["dataset_file"]: manifest["dataset_file_sha256"]
                        },
                    }
                )
            ).hexdigest(),
            manifest["dataset_sha256"],
        )

    def test_notebook_is_self_contained_syntax_valid_and_unexecuted(self):
        notebook_bytes = NOTEBOOK_PATH.read_bytes()
        notebook = json.loads(notebook_bytes)
        self.assertEqual(notebook["nbformat"], 4)
        self.assertGreaterEqual(len(notebook["cells"]), 5)
        code_cells = [
            cell for cell in notebook["cells"] if cell["cell_type"] == "code"
        ]
        self.assertGreaterEqual(len(code_cells), 4)
        for index, cell in enumerate(code_cells):
            source = "".join(cell["source"])
            source = "\n".join(
                line for line in source.splitlines() if not line.startswith("%")
            )
            ast.parse(source, filename=f"{NOTEBOOK_PATH}:cell-{index}")
            self.assertIsNone(cell["execution_count"])
            self.assertEqual(cell["outputs"], [])
        notebook_source = "\n".join(
            "".join(cell["source"])
            for cell in code_cells
        )
        expected_bundle_sha = hashlib.sha256(
            (
                MILESTONE5_ROOT / "qwen3-8b-baseline-bundle-v6.json"
            ).read_bytes()
        ).hexdigest()
        match = re.search(
            r'EXPECTED_BUNDLE_SHA256 = "([0-9a-f]{64})"',
            notebook_source,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), expected_bundle_sha)
        self.assertIn("run_baseline()", notebook_source)
        self.assertIn("_resolve_verified_snapshot", notebook_source)
        self.assertIn("local_files_only=True", notebook_source)
        self.assertIn("validate_final_archive(archive_path)", notebook_source)
        self.assertIn("Qwen/Qwen3-8B", notebook_source)
        self.assertIn("enable_thinking=False", notebook_source)
        self.assertIn("_preflight_runtime()", notebook_source)
        self.assertIn("bitsandbytes", notebook_source)
        self.assertIn("bitsandbytes==0.48.2", notebook_source)
        self.assertNotIn("bitsandbytes==0.45.5", notebook_source)
        self.assertIn("gpu_free_memory_bytes", notebook_source)
        self.assertIn("bitsandbytes_library_path", notebook_source)
        self.assertIn("detected_cuda_binary_version", notebook_source)
        self.assertIn('"sha256": _sha256(archive_path.read_bytes())', notebook_source)
        self.assertIn("expected_decision_accuracy", notebook_source)
        self.assertIn("numeric_grounding_heuristic_rate", notebook_source)
        self.assertNotIn("clarification_decision_accuracy", notebook_source)
        self.assertNotIn("from app.", notebook_source)
        self.assertNotIn("import app.", notebook_source)
        self.assertNotIn("TOOL_DEFINITIONS", notebook_source)
        self.assertIn("archive_validation = validate_final_archive(archive_path)", notebook_source)

    def test_notebook_bundle_preflight_accepts_only_the_pinned_static_bundle(self):
        bundle_path = MILESTONE5_ROOT / "qwen3-8b-baseline-bundle-v6.json"
        bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        runner_source = (
            MILESTONE5_ROOT / "kaggle_baseline_runner.py"
        ).read_text(encoding="utf-8")
        runner_prefix = runner_source.split("# %% [RUN_BASELINE]", maxsplit=1)[0]
        namespace = {}
        exec(compile(runner_prefix, "kaggle_baseline_runner.py", "exec"), namespace)
        namespace["EXPECTED_BUNDLE_SHA256"] = bundle_sha
        bundle, bundle_bytes = namespace["_load_and_validate_bundle"](bundle_path)
        self.assertEqual(hashlib.sha256(bundle_bytes).hexdigest(), bundle_sha)
        self.assertEqual(
            bundle["benchmark"]["benchmark_version"],
            "insightos-qwen3-8b-orchestration-benchmark-v4",
        )
        self.assertEqual(
            bundle["kaggle_environment"]["packages"]["bitsandbytes"], "0.48.2"
        )

    def test_decision_metrics_keep_overall_and_clarification_accuracy_distinct(self):
        runner_source = (
            MILESTONE5_ROOT / "kaggle_baseline_runner.py"
        ).read_text(encoding="utf-8")
        runner_prefix = runner_source.split("# %% [RUN_BASELINE]", maxsplit=1)[0]
        namespace = {}
        exec(compile(runner_prefix, "kaggle_baseline_runner.py", "exec"), namespace)
        case = {
            "id": "clarify-case",
            "category": "clarification",
            "expected": {"decision": "clarify"},
        }
        row = {
            "case_id": "clarify-case",
            "category": "clarification",
            "status": "evaluated",
            "passed": True,
            "checks": {
                "expected_decision_correct": True,
                "clarification_required": True,
                "structured_output_valid": True,
            },
            "applicable_checks": ["expected_decision_correct"],
            "failure_classifications": [],
        }
        metrics = namespace["_aggregate_metrics"]([case], [row])
        self.assertEqual(metrics["expected_decision_accuracy"]["value"], 1.0)
        self.assertEqual(metrics["required_clarification_accuracy"]["value"], 1.0)
        self.assertNotIn("clarification_decision_accuracy", metrics)
        self.assertEqual(
            metrics["numeric_grounding_heuristic_rate"]["status"],
            "unavailable_no_applicable_observations",
        )

    def test_model_response_contract_excludes_runtime_owned_handoff_fields(self):
        namespace = self._runner_namespace()
        original_schema = {
            "type": "object",
            "required": ["specialist_handoff"],
            "properties": {
                "specialist_handoff": {
                    "type": ["object", "null"],
                    "properties": {
                        "task_id": {"type": "string"},
                        "workflow_id": {"type": "string"},
                    },
                }
            },
        }

        model_schema = namespace["_model_facing_response_schema"](original_schema)
        handoff_schema = model_schema["properties"]["specialist_handoff"]["oneOf"][1]

        self.assertEqual(
            handoff_schema["required"],
            ["agent", "objective", "context"],
        )
        self.assertEqual(
            set(handoff_schema["properties"]),
            {"agent", "objective", "context"},
        )
        self.assertFalse(handoff_schema["additionalProperties"])
        self.assertIn("task_id", original_schema["properties"]["specialist_handoff"]["properties"])

    def test_model_prompt_assigns_handoff_ids_to_runtime_and_defaults_to_null(self):
        namespace = self._runner_namespace()
        model_schema = namespace["_model_facing_response_schema"](
            {
                "type": "object",
                "properties": {
                    "specialist_handoff": {"type": ["object", "null"]},
                },
            }
        )
        prompt = namespace["_make_prompt"](
            {"prompt": "List projects.", "tools": []},
            tool_schemas=[],
            response_schema=model_schema,
        )[0]["content"]

        self.assertIn("Set specialist_handoff to null unless", prompt)
        self.assertIn("workflow/task/conversation IDs", prompt)
        self.assertIn("permitted tools", prompt)
        embedded_schema = prompt.split("RESPONSE_SCHEMA=", maxsplit=1)[1].split(
            "\nAVAILABLE_INSIGHTOS_TOOL_SCHEMAS=", maxsplit=1
        )[0]
        self.assertEqual(json.loads(embedded_schema), model_schema)

    def test_score_requires_null_handoff_unless_case_requests_delegation(self):
        namespace = self._runner_namespace()
        from jsonschema import Draft202012Validator

        benchmark_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "intent",
                "plan",
                "tool_calls",
                "specialist_handoff",
                "response",
                "clarification_questions",
                "evidence_references",
                "decision",
            ],
            "properties": {
                "intent": {"type": "string"},
                "plan": {"type": "array", "items": {"type": "string"}},
                "tool_calls": {"type": "array"},
                "specialist_handoff": {"type": ["object", "null"]},
                "response": {"type": "string"},
                "clarification_questions": {"type": "array"},
                "evidence_references": {"type": "array"},
                "decision": {"enum": ["continue", "clarify", "abstain"]},
            },
        }
        response_schema = namespace["_model_facing_response_schema"](benchmark_schema)
        response_validator = Draft202012Validator(response_schema)
        delegation_validator = Draft202012Validator(
            response_schema["properties"]["specialist_handoff"]["oneOf"][1]
        )
        case = {
            "id": "no-delegation",
            "tools": [],
            "expected": {"decision": "continue", "tool_calls_exact": []},
        }
        output = {
            "intent": "List projects",
            "plan": [],
            "tool_calls": [],
            "specialist_handoff": None,
            "response": "I can help with that.",
            "clarification_questions": [],
            "evidence_references": [],
            "decision": "continue",
        }
        score = namespace["_score_case"](
            case,
            output,
            response_validator=response_validator,
            specialist_validator=Draft202012Validator({"type": "object"}),
            delegation_validator=delegation_validator,
            tool_by_name={},
        )

        self.assertTrue(score["passed"])
        self.assertTrue(score["checks"]["expected_tool_sequence_correct"])
        self.assertTrue(score["checks"]["no_unrequested_specialist_handoff"])
        delegated_case = {
            "id": "delegation",
            "tools": [],
            "expected": {
                "decision": "continue",
                "tool_calls_exact": [],
                "specialist_agent": "data_intelligence",
            },
        }
        output["specialist_handoff"] = {
            "agent": "data_intelligence",
            "objective": "Inspect the supplied dataset.",
            "context": {},
        }
        expected_delegation_score = namespace["_score_case"](
            delegated_case,
            output,
            response_validator=response_validator,
            specialist_validator=Draft202012Validator({"type": "object"}),
            delegation_validator=delegation_validator,
            tool_by_name={},
        )
        self.assertTrue(expected_delegation_score["passed"])
        self.assertTrue(
            expected_delegation_score["checks"]["specialist_delegation_contract_valid"]
        )
        self.assertTrue(
            expected_delegation_score["checks"]["specialist_route_correct"]
        )
        output["specialist_handoff"] = {
            "agent": "data_intelligence",
            "objective": "Inspect the supplied dataset.",
            "context": {},
        }
        delegated_score = namespace["_score_case"](
            case,
            output,
            response_validator=response_validator,
            specialist_validator=Draft202012Validator({"type": "object"}),
            delegation_validator=delegation_validator,
            tool_by_name={},
        )
        self.assertFalse(delegated_score["passed"])
        self.assertFalse(
            delegated_score["checks"]["no_unrequested_specialist_handoff"]
        )

    def test_schema_diagnostics_report_all_missing_required_fields(self):
        namespace = self._runner_namespace()
        from jsonschema import Draft202012Validator

        response_schema = {
            "type": "object",
            "required": [
                "decision",
                "evidence_references",
                "specialist_handoff",
            ],
            "properties": {
                "decision": {"type": "string"},
                "evidence_references": {"type": "array"},
                "specialist_handoff": {
                    "oneOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "properties": {"task_id": {"type": "string", "minLength": 4}},
                            "required": ["task_id"],
                        },
                    ]
                },
            },
        }
        output = {
            "decision": "continue",
            "specialist_handoff": {"task_id": "x"},
        }
        score = namespace["_score_case"](
            {"id": "missing-evidence", "tools": [], "expected": {}},
            output,
            response_validator=Draft202012Validator(response_schema),
            specialist_validator=Draft202012Validator({"type": "object"}),
            tool_by_name={},
        )

        self.assertFalse(score["checks"]["structured_output_valid"])
        self.assertIn(
            "evidence_references",
            " ".join(error["message"] for error in score["response_schema_errors"]),
        )
        self.assertIn(
            {
                "instance_path": "/specialist_handoff/task_id",
                "schema_path": "/properties/specialist_handoff/oneOf/1/properties/task_id/minLength",
                "validator": "minLength",
                "message": "'x' is too short",
            },
            score["response_schema_errors"],
        )

    def test_tool_selection_metric_includes_explicitly_empty_expected_sequences(self):
        namespace = self._runner_namespace()
        case = {
            "id": "no-tools",
            "category": "clarification",
            "expected": {"tool_calls_exact": []},
        }
        row = {
            "case_id": "no-tools",
            "status": "evaluated",
            "passed": True,
            "checks": {"expected_tool_sequence_correct": True},
            "applicable_checks": ["expected_tool_sequence_correct"],
            "failure_classifications": [],
        }

        metrics = namespace["_aggregate_metrics"]([case], [row])

        self.assertEqual(metrics["tool_selection_accuracy"]["numerator"], 1)
        self.assertEqual(metrics["tool_selection_accuracy"]["denominator"], 1)

    def test_claim_heuristics_cover_quoted_ids_retrieval_and_hedged_causation(self):
        namespace = self._runner_namespace()
        case = {
            "prompt": 'Use dataset "42" and file "7".',
            "tool_result_fixture": None,
            "fixtures": {},
        }

        _, allowed_ids = namespace["_input_identifiers"](case)

        self.assertEqual(allowed_ids, {"42", "7"})
        self.assertIsNotNone(
            namespace["_EXECUTION_CLAIM_PATTERN"].search(
                "The profile for dataset 42 was retrieved."
            )
        )
        self.assertIsNotNone(
            namespace["_CAUSAL_CLAIM_PATTERN"].search(
                "The campaign may have contributed to the change."
            )
        )

    def test_corrected_notebook_is_versioned_and_keeps_v6_bundle_immutable(self):
        notebook = json.loads(CORRECTED_NOTEBOOK_PATH.read_text(encoding="utf-8"))
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            source = "\n".join(
                line for line in "".join(cell["source"]).splitlines()
                if not line.startswith("%")
            )
            ast.parse(source, filename=f"{CORRECTED_NOTEBOOK_PATH}:cell-{index}")
            self.assertIsNone(cell["execution_count"])
            self.assertEqual(cell["outputs"], [])
        notebook_source = "\n".join(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["cell_type"] == "code"
        )
        self.assertIn("MODEL_RESPONSE_SCHEMA_VERSION", notebook_source)
        self.assertIn("_model_facing_response_schema", notebook_source)
        self.assertIn("insightos-qwen3-8b-evaluator-v2", notebook_source)
        self.assertIn("Corrected Baseline Runner — v7", notebook["cells"][0]["source"][0])
        self.assertIn("qwen3-8b-baseline-bundle-v6.json", notebook_source)
        self.assertIn(
            hashlib.sha256(
                (MILESTONE5_ROOT / "qwen3-8b-baseline-bundle-v6.json").read_bytes()
            ).hexdigest(),
            notebook_source,
        )

    def test_active_run_does_not_resume_a_checkpoint_from_another_evaluator(self):
        namespace = self._runner_namespace()
        with tempfile.TemporaryDirectory() as temporary_directory:
            namespace["OUTPUT_ROOT"] = Path(temporary_directory)
            bundle_hash = "a" * 64
            benchmark_hash = "b" * 64
            first_run_id, _, _ = namespace["_active_run"](
                bundle_hash,
                benchmark_hash,
                "evaluator-v1",
            )
            second_run_id, _, _ = namespace["_active_run"](
                bundle_hash,
                benchmark_hash,
                "evaluator-v2",
            )

        self.assertNotEqual(first_run_id, second_run_id)

    def test_torch_preflight_version_boundary(self):
        runner_source = (
            MILESTONE5_ROOT / "kaggle_baseline_runner.py"
        ).read_text(encoding="utf-8")
        runner_prefix = runner_source.split("# %% [RUN_BASELINE]", maxsplit=1)[0]
        namespace = {}
        exec(compile(runner_prefix, "kaggle_baseline_runner.py", "exec"), namespace)
        supported = namespace["_torch_version_supported"]
        self.assertTrue(supported("2.6.0+cu124"))
        self.assertTrue(supported("2.8.1"))
        self.assertFalse(supported("2.5.1"))
        self.assertFalse(supported("3.0.0"))
        self.assertFalse(supported("unknown"))

    def _runner_namespace(self):
        runner_source = (
            MILESTONE5_ROOT / "kaggle_baseline_runner.py"
        ).read_text(encoding="utf-8")
        runner_prefix = runner_source.split("# %% [RUN_BASELINE]", maxsplit=1)[0]
        namespace = {}
        exec(compile(runner_prefix, "kaggle_baseline_runner.py", "exec"), namespace)
        return namespace

    def _fake_torch(self, *, cuda_version="13.0", available=False, capability=(8, 9)):
        cuda = SimpleNamespace(
            is_available=lambda: available,
            get_device_properties=lambda _device: SimpleNamespace(
                name="Mock NVIDIA GPU",
                total_memory=16_000_000_000,
            ),
            get_device_capability=lambda _device: capability,
            mem_get_info=lambda _device: (8_000_000_000, 16_000_000_000),
            synchronize=lambda: None,
        )
        return SimpleNamespace(
            __version__="2.8.0+cu130",
            version=SimpleNamespace(cuda=cuda_version),
            cuda=cuda,
            float16="float16",
        )

    def _fake_bnb(self, *, loaded_path, compiled_with_cuda):
        library = SimpleNamespace(
            compiled_with_cuda=compiled_with_cuda,
            _lib=SimpleNamespace(_name=loaded_path),
        )
        return SimpleNamespace(
            cextension=SimpleNamespace(lib=library),
            functional=SimpleNamespace(),
        )

    def _fake_transformers(self):
        class BitsAndBytesConfig:
            def __init__(self, **kwargs):
                self.load_in_4bit = kwargs["load_in_4bit"]
                self.bnb_4bit_quant_type = kwargs["bnb_4bit_quant_type"]
                self.bnb_4bit_use_double_quant = kwargs[
                    "bnb_4bit_use_double_quant"
                ]
                self.bnb_4bit_compute_dtype = kwargs[
                    "bnb_4bit_compute_dtype"
                ]

        return SimpleNamespace(BitsAndBytesConfig=BitsAndBytesConfig)

    def _fake_cuda_modules(self, binary_path):
        specs = SimpleNamespace(cuda_version_string="130")
        cextension = SimpleNamespace(
            get_available_cuda_binary_versions=lambda: ["13.0"],
            get_cuda_bnb_library_path=lambda _specs: binary_path,
        )
        cuda_specs = SimpleNamespace(get_cuda_specs=lambda: specs)
        return cextension, cuda_specs

    def _preflight(self, namespace, *, torch, bnb=None, cap_modules=None):
        cextension, cuda_specs = cap_modules or self._fake_cuda_modules(
            Path("C:/mock/libbitsandbytes_cuda130.so")
        )
        return namespace["_preflight_runtime"](
            torch_module=torch,
            bitsandbytes_module=bnb,
            transformers_module=self._fake_transformers(),
            package_metadata=FakePackageMetadata,
            cextension_module=cextension,
            cuda_specs_module=cuda_specs,
        )

    def _snapshot_fixture(self, root: Path, commit: str) -> Path:
        snapshot = root / "models--Qwen--Qwen3-8B" / "snapshots" / commit
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
        (snapshot / "tokenizer_config.json").write_text("{}", encoding="utf-8")
        (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
        (snapshot / "model.safetensors").write_bytes(b"weights")
        return snapshot

    def _resolve_snapshot(self, namespace, snapshot, *, metadata_sha=None):
        class FakeHubApi:
            def model_info(self, repo_id, *, revision):
                self.repo_id = repo_id
                self.revision = revision
                return SimpleNamespace(sha=metadata_sha)

        return namespace["_resolve_verified_snapshot"](
            repo_id=MODEL_ID,
            expected_commit=MODEL_REVISION,
            api=FakeHubApi(),
            snapshot_download_fn=lambda repo_id, *, revision: snapshot,
        )

    def test_snapshot_verification_uses_hub_sha_and_exact_snapshot_commit(self):
        namespace = self._runner_namespace()
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = Path(temporary_directory)
            snapshot = self._snapshot_fixture(cache, MODEL_REVISION)
            refs = cache / "models--Qwen--Qwen3-8B" / "refs"
            refs.mkdir()
            (refs / "main").write_text("stale-branch-commit", encoding="utf-8")
            verified = self._resolve_snapshot(
                namespace,
                snapshot,
                metadata_sha=MODEL_REVISION,
            )
        self.assertEqual(verified["actual_commit"], MODEL_REVISION)
        self.assertEqual(
            verified["diagnostics"]["hub_metadata_commit"], MODEL_REVISION
        )
        self.assertFalse(verified["cache_refs_used"])

    def test_snapshot_verification_rejects_mismatched_and_missing_metadata(self):
        namespace = self._runner_namespace()
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = self._snapshot_fixture(
                Path(temporary_directory), MODEL_REVISION
            )
            for actual_sha in ("different-commit", None):
                with self.subTest(actual_sha=actual_sha):
                    with self.assertRaises(
                        namespace["SnapshotRevisionVerificationError"]
                    ) as failure:
                        self._resolve_snapshot(
                            namespace,
                            snapshot,
                            metadata_sha=actual_sha,
                        )
                    self.assertEqual(
                        failure.exception.diagnostics["expected_commit"],
                        MODEL_REVISION,
                    )
                    self.assertEqual(
                        failure.exception.diagnostics["actual_commit"],
                        MODEL_REVISION,
                    )
                    self.assertEqual(
                        failure.exception.diagnostics["hub_metadata_commit"],
                        actual_sha,
                    )
                    self.assertEqual(
                        failure.exception.diagnostics["snapshot_directory"],
                        str(snapshot),
                    )

    def test_snapshot_verification_rejects_stale_cache_commit(self):
        namespace = self._runner_namespace()
        stale_commit = "0" * 40
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = self._snapshot_fixture(Path(temporary_directory), stale_commit)
            with self.assertRaises(
                namespace["SnapshotRevisionVerificationError"]
            ) as failure:
                self._resolve_snapshot(
                    namespace,
                    snapshot,
                    metadata_sha=MODEL_REVISION,
                )
        self.assertEqual(failure.exception.diagnostics["actual_commit"], stale_commit)
        self.assertEqual(
            failure.exception.diagnostics["expected_commit"], MODEL_REVISION
        )

    def test_snapshot_verification_resolves_symlinked_cache_paths(self):
        namespace = self._runner_namespace()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            actual_snapshot = self._snapshot_fixture(root / "real-cache", MODEL_REVISION)
            alias_snapshot = (
                root
                / "cache-alias"
                / "models--Qwen--Qwen3-8B"
                / "snapshots"
                / MODEL_REVISION
            )
            original_resolve = Path.resolve

            def resolve_with_cache_symlink(path, *, strict=False):
                if path == alias_snapshot:
                    return actual_snapshot
                return original_resolve(path, strict=strict)

            with patch.object(Path, "resolve", resolve_with_cache_symlink):
                verified = self._resolve_snapshot(
                    namespace,
                    alias_snapshot,
                    metadata_sha=MODEL_REVISION,
                )
            self.assertNotEqual(
                verified["snapshot_directory"],
                verified["resolved_snapshot_directory"],
            )

    def test_snapshot_verification_rejects_incomplete_weight_shards(self):
        namespace = self._runner_namespace()
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = self._snapshot_fixture(
                Path(temporary_directory), MODEL_REVISION
            )
            (snapshot / "model.safetensors").unlink()
            (snapshot / "model.safetensors.index.json").write_text(
                json.dumps({"weight_map": {"weight": "shard-00001.safetensors"}}),
                encoding="utf-8",
            )
            with self.assertRaises(
                namespace["SnapshotRevisionVerificationError"]
            ) as failure:
                self._resolve_snapshot(
                    namespace,
                    snapshot,
                    metadata_sha=MODEL_REVISION,
                )
        self.assertIn("weight shards are missing", failure.exception.diagnostics["verification_error"]["message"])

    def test_snapshot_verification_rejects_broken_symlink(self):
        namespace = self._runner_namespace()
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = self._snapshot_fixture(
                Path(temporary_directory), MODEL_REVISION
            )
            dangling_link = snapshot / "dangling-tokenizer-link"
            dangling_link.touch()
            original_is_symlink = Path.is_symlink
            original_resolve = Path.resolve

            def is_symlink(path):
                return path == dangling_link or original_is_symlink(path)

            def resolve(path, *, strict=False):
                if path == dangling_link:
                    raise FileNotFoundError("broken snapshot symlink")
                return original_resolve(path, strict=strict)

            with patch.object(Path, "is_symlink", is_symlink):
                with patch.object(Path, "resolve", resolve):
                    with self.assertRaises(
                        namespace["SnapshotRevisionVerificationError"]
                    ) as failure:
                        self._resolve_snapshot(
                            namespace,
                            snapshot,
                            metadata_sha=MODEL_REVISION,
                        )
        self.assertEqual(
            failure.exception.diagnostics["verification_error"]["type"],
            "FileNotFoundError",
        )

    def test_preflight_distinguishes_cpu_only_pytorch_from_missing_gpu(self):
        namespace = self._runner_namespace()
        no_cuda_torch = self._fake_torch(cuda_version=None)
        with self.assertRaises(namespace["RuntimePreflightError"]) as no_cuda:
            self._preflight(namespace, torch=no_cuda_torch)
        self.assertEqual(
            no_cuda.exception.diagnostics["failure_category"],
            "no_cuda_enabled_pytorch",
        )

        no_gpu_torch = self._fake_torch(available=False)
        with self.assertRaises(namespace["RuntimePreflightError"]) as no_gpu:
            self._preflight(namespace, torch=no_gpu_torch)
        self.assertEqual(
            no_gpu.exception.diagnostics["failure_category"],
            "no_nvidia_gpu",
        )

    def test_preflight_distinguishes_missing_cuda_binary_and_cpu_fallback(self):
        namespace = self._runner_namespace()
        cpu_library = self._fake_bnb(
            loaded_path="libbitsandbytes_cpu.so",
            compiled_with_cuda=False,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_path = Path(temporary_directory) / "libbitsandbytes_cuda130.so"
            modules = self._fake_cuda_modules(missing_path)
            with self.assertRaises(namespace["RuntimePreflightError"]) as missing:
                self._preflight(
                    namespace,
                    torch=self._fake_torch(available=True),
                    bnb=cpu_library,
                    cap_modules=modules,
                )
            diagnostics = missing.exception.diagnostics
            self.assertEqual(diagnostics["failure_category"], "missing_cuda_binary")
            for key in (
                "python_version",
                "pytorch_version",
                "torch_cuda_version",
                "cuda_available",
                "gpu_name",
                "gpu_compute_capability",
                "gpu_total_memory_bytes",
                "gpu_free_memory_bytes",
                "bitsandbytes_version",
                "bitsandbytes_library_path",
                "detected_cuda_binary_version",
            ):
                self.assertIn(key, diagnostics)

            missing_path.touch()
            with self.assertRaises(namespace["RuntimePreflightError"]) as cpu:
                self._preflight(
                    namespace,
                    torch=self._fake_torch(available=True),
                    bnb=cpu_library,
                    cap_modules=modules,
                )
            self.assertEqual(
                cpu.exception.diagnostics["failure_category"],
                "bitsandbytes_cpu_only_fallback",
            )

    def test_preflight_distinguishes_unsupported_architecture_and_nf4_failure(self):
        namespace = self._runner_namespace()
        with self.assertRaises(namespace["RuntimePreflightError"]) as architecture:
            self._preflight(
                namespace,
                torch=self._fake_torch(available=True, capability=(5, 2)),
            )
        self.assertEqual(
            architecture.exception.diagnostics["failure_category"],
            "incompatible_gpu_architecture",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            binary_path = Path(temporary_directory) / "libbitsandbytes_cuda130.so"
            binary_path.touch()
            bnb = self._fake_bnb(
                loaded_path=str(binary_path),
                compiled_with_cuda=True,
            )
            bnb.functional.quantize_4bit = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("no kernel image is available for execution")
            )
            torch = self._fake_torch(available=True)
            torch.randn = lambda *_args, **_kwargs: object()
            with self.assertRaises(namespace["RuntimePreflightError"]) as nf4:
                self._preflight(
                    namespace,
                    torch=torch,
                    bnb=bnb,
                    cap_modules=self._fake_cuda_modules(binary_path),
                )
        self.assertEqual(
            nf4.exception.diagnostics["failure_category"],
            "incompatible_gpu_architecture",
        )

    def test_importer_does_not_accept_a_missing_kaggle_archive(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            incoming = Path(temporary_directory) / "incoming"
            incoming.mkdir()
            name = (
                "insightos-qwen3-8b-baseline-v1-"
                "20261105T120000Z-0123abcd-export-001.zip"
            )
            with self.assertRaises(FileNotFoundError):
                validate_archive(
                    incoming / name,
                    incoming_dir=incoming,
                    validated_dir=Path(temporary_directory) / "validated",
                )

    def test_importer_rejects_incomplete_diagnostic_export(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            incoming = Path(temporary_directory) / "incoming"
            incoming.mkdir()
            archive_path = incoming / (
                "insightos-qwen3-8b-baseline-v1-"
                "20261105T120000Z-0123abcd-export-001.zip"
            )
            manifest = {
                "manifest_version": "insightos-qwen3-8b-baseline-execution-v1",
                "run_id": "20261105T120000Z-0123abcd",
                "execution_status": "incomplete",
                "baseline_status": "pending",
                "generated_case_ids": [],
                "expected_case_ids": [f"case-{index}" for index in range(12)],
            }
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "execution_manifest.json",
                    json.dumps(manifest),
                )
            with self.assertRaisesRegex(ValueError, "execution is incomplete"):
                validate_archive(
                    archive_path,
                    incoming_dir=incoming,
                    validated_dir=Path(temporary_directory) / "validated",
                )

    def test_kaggle_handoff_paths_exist_and_no_inference_outputs_exist(self):
        self.assertTrue(
            (MILESTONE5_ROOT / "kaggle_results" / "incoming").is_dir()
        )
        self.assertTrue((MILESTONE5_ROOT / "README.md").is_file())
        self.assertTrue((MILESTONE5_ROOT / "kaggle-environment-v4.json").is_file())
        self.assertFalse(
            any(
                (MILESTONE5_ROOT / "kaggle_results" / "incoming").glob("*.zip")
            )
        )
        self.assertFalse(
            any(
                (MILESTONE5_ROOT / "kaggle_results" / "validated").glob("*")
            )
            if (MILESTONE5_ROOT / "kaggle_results" / "validated").exists()
            else False
        )


if __name__ == "__main__":
    unittest.main()
