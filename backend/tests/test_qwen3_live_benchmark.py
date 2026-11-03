import json
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app.agent.contracts import ModelResponse, ProviderMessage
from app.agent.model_router import RoutedCompletion
from scripts.benchmarks import qwen3_live_benchmark
from scripts.benchmarks.qwen3_live_benchmark import (
    FIXTURE_PATH,
    MODEL_TARGETS,
    DEFAULT_MODEL_TARGET,
    _apply_quality_scores,
    _load_model_resident,
    _mark_truncated_result,
    _memory_pressure_is_severe,
    _new_case_result,
    _number_tokens,
    _only_model_is_loaded,
    _required_free_gb,
    _role_recommendations,
    _route_completion,
    _run_handoff,
    _workloads_for_model,
    _validate_workload,
)


class Qwen3LiveBenchmarkTests(unittest.TestCase):
    def test_numeric_claims_are_normalized(self):
        self.assertEqual(
            _number_tokens({"text": "$12,000 and 25%"}),
            {"12000", "25"},
        )

    def test_model_memory_estimate_is_advisory_for_load_planning(self):
        model = {"size_bytes": 5 * 1024**3}
        self.assertEqual(_required_free_gb(model, already_loaded=False), 7.0)
        self.assertEqual(_required_free_gb(model, already_loaded=True), 0.5)

    def test_commit_and_pagefile_pressure_are_treated_as_severe(self):
        self.assertTrue(
            _memory_pressure_is_severe(
                {"free_gb": 2.0, "memory_load_percent": 93, "committed_gb": 35.0, "commit_limit_gb": 36.0}
            )
        )
        self.assertTrue(
            _memory_pressure_is_severe(
                {"free_gb": 2.0, "memory_load_percent": 93, "pagefile_usage_percent": 96.0}
            )
        )
        self.assertFalse(
            _memory_pressure_is_severe(
                {"free_gb": 2.0, "memory_load_percent": 93, "committed_gb": 12.0, "commit_limit_gb": 36.0}
            )
        )

    def test_telemetry_check_records_three_snapshots(self):
        snapshot = {
            "physical_memory": {"total_kb": 16384, "available_kb": 4096},
            "committed_memory": {"committed_bytes": 1, "commit_limit_bytes": 2},
            "pagefile": {"current_usage_mb": 100, "allocated_mb": 200},
            "availability": {"windows_metrics": True},
        }
        with TemporaryDirectory() as tmpdir:
            with patch.object(qwen3_live_benchmark, "RESULTS_ROOT", Path(tmpdir)):
                with patch.object(
                    qwen3_live_benchmark,
                    "_windows_telemetry_snapshot",
                    side_effect=[snapshot, snapshot, snapshot],
                ):
                    output_dir = qwen3_live_benchmark._run_telemetry_check()
                    report = json.loads((output_dir / "telemetry_diagnostic.json").read_text(encoding="utf-8"))
                    self.assertEqual(len(report["snapshots"]), 3)
                    self.assertIn("validations", report)

    def test_benchmark_selects_exactly_one_model_by_default(self):
        self.assertEqual(DEFAULT_MODEL_TARGET, "qwen3:8b")
        self.assertEqual(
            MODEL_TARGETS,
            ("qwen3:8b", "qwen3:4b-instruct", "qwen3:4b", "qwen3:0.6b"),
        )

    def test_model_residency_check_rejects_multiple_models(self):
        self.assertTrue(_only_model_is_loaded("qwen3:8b", [{"name": "qwen3:8b"}]))
        self.assertFalse(
            _only_model_is_loaded(
                "qwen3:8b",
                [{"name": "qwen3:8b"}, {"name": "qwen3:0.6b"}],
            )
        )

    def test_baseline_and_optimized_prompts_are_comparable_on_one_model(self):
        router = Mock()
        router.complete.side_effect = lambda role, request: RoutedCompletion(
            role=role,
            model=request.model,
            response=ModelResponse(
                message=ProviderMessage(role="assistant", content='{"ok":true}'),
                finish_reason="stop",
            ),
        )
        options = {
            "model": "qwen3:8b",
            "role": "analytics_modelling",
            "category": "routing",
            "prompt": "Return a JSON object.",
        }
        _route_completion(router, **options, prompt_variant="baseline")
        _route_completion(router, **options, prompt_variant="optimized")
        baseline_request = router.complete.call_args_list[0].args[1]
        optimized_request = router.complete.call_args_list[1].args[1]
        self.assertEqual(baseline_request.model, optimized_request.model)
        self.assertEqual(baseline_request.max_output_tokens, optimized_request.max_output_tokens)
        self.assertFalse(baseline_request.reasoning_enabled)
        self.assertFalse(optimized_request.reasoning_enabled)
        self.assertNotEqual(
            baseline_request.messages[0].content,
            optimized_request.messages[0].content,
        )
        self.assertIn(
            "Approve only the exact requested",
            optimized_request.messages[0].content,
        )
        self.assertIn(
            "Senior analyst",
            optimized_request.messages[0].content,
        )

    def test_mocked_specialist_workflow_validates_under_both_prompt_variants(self):
        evidence = [{"type": "benchmark_fixture", "id": "synthetic-inspection-01"}]
        arguments = {
            "dataset_id": 501,
            "file_id": 601,
            "rules": "default",
        }
        responses = {
            "data_intelligence": {
                "contract_version": 1,
                "status": "completed",
                "summary": "Inspected the verified synthetic fields.",
                "observed_fields": ["month", "revenue", "expenses"],
                "recommended_operation": "run_quality_analysis",
                "evidence_references": evidence,
            },
            "analytics_modelling": {
                "contract_version": 1,
                "status": "approved",
                "tool_name": "run_quality_analysis",
                "arguments": arguments,
                "summary": "Approved the requested deterministic quality operation.",
                "evidence_references": evidence,
            },
            "insights_visualization": {
                "contract_version": 1,
                "status": "completed",
                "summary": "The synthetic engine result is presented with its limitation.",
                "evidence_references": evidence,
                "visualization_specification": None,
            },
        }
        fixture = next(
            item
            for item in json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["workloads"]
            if item["id"] == "specialist_handoff"
        )
        prompt_contents = {}

        class ScriptedRouter:
            def __init__(self):
                self.requests = []

            def model_for(self, role):
                return "qwen3:8b"

            def complete(self, role, request):
                self.requests.append(request)
                response = ModelResponse(
                    message=ProviderMessage(
                        role="assistant",
                        content=json.dumps(responses[role]),
                    ),
                    finish_reason="stop",
                )
                return RoutedCompletion(
                    role=role,
                    model="qwen3:8b",
                    response=response,
                )

        for variant in ("baseline", "optimized"):
            router = ScriptedRouter()
            workflow, handoff = _run_handoff(
                router,
                workflow_id=uuid.uuid4().hex,
                prompt_variant=variant,
            )
            result = _validate_workload(
                fixture,
                json.dumps(workflow),
                handoff_valid=handoff["valid"],
            )
            self.assertTrue(handoff["valid"])
            self.assertFalse(handoff["engine_executed"])
            self.assertTrue(result["json_valid"])
            self.assertTrue(result["schema_valid"])
            self.assertTrue(result["hallucination_control"])
            prompt_contents[variant] = router.requests[0].messages[0].content

        self.assertNotEqual(prompt_contents["baseline"], prompt_contents["optimized"])

    def test_workloads_are_filtered_by_registered_role_eligibility(self):
        fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        eligible, skipped = _workloads_for_model(
            fixtures["workloads"],
            "qwen3:0.6b",
        )
        self.assertEqual(
            {workload["id"] for workload in eligible},
            {"business_summary", "structured_presentation"},
        )
        self.assertNotIn("specialist_handoff", {item["id"] for item in eligible})
        self.assertIn("specialist_handoff", {item["id"] for item in skipped})

    def test_memory_abort_uses_severe_pressure_not_model_estimate(self):
        self.assertFalse(
            _memory_pressure_is_severe(
                {"free_gb": 1.2, "memory_load_percent": 92}
            )
        )
        self.assertTrue(
            _memory_pressure_is_severe(
                {"free_gb": 0.4, "memory_load_percent": 96}
            )
        )
        self.assertTrue(
            _memory_pressure_is_severe(
                {"free_gb": 2.0, "memory_load_percent": 98}
            )
        )

    @patch(
        "scripts.benchmarks.qwen3_live_benchmark._local_json",
        return_value={"load_duration": 1_250_000_000, "total_duration": 1_300_000_000},
    )
    def test_resident_load_requests_indefinite_keep_alive(self, local_json):
        result = _load_model_resident("qwen3:8b")
        self.assertTrue(result["loaded"])
        self.assertEqual(result["load_duration_ms"], 1250)
        local_json.assert_called_once_with(
            "/api/generate",
            {
                "model": "qwen3:8b",
                "prompt": "",
                "stream": False,
                "keep_alive": -1,
            },
            timeout_seconds=300,
        )

    def test_route_answer_key_rejects_wrong_category(self):
        fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        workload = next(
            item for item in fixtures["workloads"] if item["id"] == "task_classification"
        )
        routes = [dict(item) for item in workload["expected_routes"]]
        routes[0]["task_category"] = "analytical"
        result = _validate_workload(
            workload,
            json.dumps({"routes": routes}),
        )
        self.assertTrue(result["schema_valid"])
        self.assertFalse(result["factual_accuracy"])

    def test_summary_rejects_unsupported_numeric_claim(self):
        workload = {
            "id": "business_summary",
            "required_numbers": [10000],
            "allowed_numbers": [10000],
            "required_terms": ["march"],
        }
        result = _validate_workload(
            workload,
            '{"executive_summary":"March was $10,000 and rose 40%.",'
            '"trends":["March"],"exceptions":["March"],"limitations":"No cause"}',
        )
        self.assertFalse(result["hallucination_control"])
        self.assertIn("unsupported_numeric_claim", result["errors"])

    def test_presentation_rejects_unexpected_schema_field(self):
        workload = {
            "id": "structured_presentation",
            "required_numbers": [100],
            "allowed_numbers": [100],
            "required_terms": ["revenue"],
        }
        result = _validate_workload(
            workload,
            '{"title":"Revenue","summary":"100","highlights":[],"warnings":[],"extra":true}',
        )
        self.assertFalse(result["schema_valid"])

    def test_unrun_role_recommendations_are_insufficient_evidence(self):
        recommendations = _role_recommendations([])
        self.assertTrue(recommendations)
        self.assertTrue(
            all(item["classification"] == "Insufficient evidence" for item in recommendations)
        )

    def test_consistency_requires_repeated_identical_outputs(self):
        results = [
            {
                "model": "qwen3:4b",
                "workload": "task_classification",
                "status": "completed",
                "response": {"routes": []},
                "validation": {"factual_accuracy": True},
                "measurements": {"request_latency_ms": 100, "system_memory_minimum_during_gb": 3},
                "inference_settings": {"timeout_seconds": 20},
                "error": None,
            },
            {
                "model": "qwen3:4b",
                "workload": "task_classification",
                "status": "completed",
                "response": {"routes": []},
                "validation": {"factual_accuracy": True},
                "measurements": {"request_latency_ms": 110, "system_memory_minimum_during_gb": 3},
                "inference_settings": {"timeout_seconds": 20},
                "error": None,
            },
        ]
        _apply_quality_scores(results)
        self.assertTrue(results[0]["validation"]["consistency"])
        self.assertEqual(results[0]["quality_scores"]["latency"], 1.0)
        self.assertEqual(results[0]["quality_scores"]["resource_efficiency"], 1.0)

    def test_truncated_output_cannot_be_scored_as_success(self):
        result = {
            "status": "completed",
            "validation": {
                "schema_valid": True,
                "instruction_following": True,
                "completeness": True,
                "errors": [],
            },
        }
        _mark_truncated_result(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "response_truncated")
        self.assertFalse(result["validation"]["schema_valid"])

    def test_pre_inference_memory_abort_keeps_workload_scores_unrun(self):
        result = _new_case_result(
            "qwen3:8b",
            {"id": "business_summary"},
            1,
            "aborted_memory_pressure",
            "Severe system memory pressure during model loading.",
        )
        _apply_quality_scores([result])
        self.assertIsNone(result["quality_scores"]["resource_efficiency"])
        self.assertIsNone(result["quality_scores"]["latency"])


if __name__ == "__main__":
    unittest.main()
