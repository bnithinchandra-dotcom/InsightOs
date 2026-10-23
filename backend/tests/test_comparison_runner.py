from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from training.evaluation.comparison_runner import (
    _case_failure_types,
    _configuration_descriptor,
    _ollama_run,
    _summary_row,
    _token_limit_profile,
    main,
)
from training.evaluation.hf_runner import load_suite


class ComparisonRunnerTests(unittest.TestCase):
    def test_summary_uses_initial_outputs_for_leak_truncation_and_latency(self):
        configuration = {
            "model": {"name": "qwen3:4b"},
            "generation_settings": {"temperature": 0.0},
        }
        result = {
            "id": "repeat_exact_token",
            "pass_at_1": {"passed": 1, "total": 2, "rate": 0.5},
            "pass_after_retry": {"passed": 2, "total": 2, "rate": 1.0},
            "fallback_count": 1,
            "requested_trial_count": 3,
            "completed_trial_count": 2,
            "incomplete_trial_count": 1,
            "trials": [
                {
                    "attempts": [
                        {
                            "attempt": 1,
                            "generation": {
                                "reasoning_leak": True,
                                "raw_leak": True,
                                "final_leak": True,
                                "termination_reason": "max_new_tokens",
                                "duration_ms": 200.0,
                            },
                        }
                    ],
                    "final_score": {"failure_category": "repeat_token_mismatch"},
                },
                {
                    "attempts": [
                        {
                            "attempt": 1,
                            "generation": {
                                "reasoning_leak": False,
                                "raw_leak": False,
                                "final_leak": False,
                                "termination_reason": "eos",
                                "duration_ms": 100.0,
                            },
                        }
                    ],
                    "final_score": {"failure_category": None},
                },
                {
                    "completed": False,
                    "attempts": [
                        {
                            "attempt": 1,
                            "generation": {
                                "raw_leak": True,
                                "final_leak": True,
                                "termination_reason": "max_new_tokens",
                                "duration_ms": 900.0,
                            },
                        }
                    ],
                    "final_score": None,
                },
            ],
        }
        row = _summary_row(configuration, result)
        self.assertEqual(row["pass_at_1"], 0.5)
        self.assertEqual(row["pass_after_retry"], 1.0)
        self.assertEqual(row["reasoning_leak_rate"], 0.5)
        self.assertEqual(row["raw_leak_rate"], 0.5)
        self.assertEqual(row["final_leak_rate"], 0.5)
        self.assertEqual(row["truncation_rate"], 0.5)
        self.assertEqual(row["median_latency_ms"], 150.0)
        self.assertEqual(row["fallback_count"], 1)
        self.assertEqual(row["model"], "qwen3:4b")
        self.assertEqual(row["temperature"], 0.0)
        self.assertEqual(row["requested_trial_count"], 3)
        self.assertEqual(row["completed_trial_count"], 2)

    def test_model_profiles_capture_thinking_and_instruct_token_limits(self):
        profile, limits = _token_limit_profile("qwen3:4b-instruct")
        self.assertEqual(profile, "instruct")
        self.assertEqual(limits["instruction_exactly_two_sentences"], 256)
        profile, limits = _token_limit_profile("qwen3:4b")
        self.assertEqual(profile, "thinking_capable")
        self.assertEqual(limits["repeat_exact_token"], 1536)
        self.assertEqual(limits["json_profit_margin"], 1536)
        self.assertEqual(limits["analysis_plan_profit_margin"], 2048)

    def test_unrun_configuration_descriptors_preserve_generation_settings(self):
        descriptor = _configuration_descriptor(
            "qwen3:4b-instruct",
            0.0,
            3,
            {"qwen3:4b-instruct": {"digest": "digest-fixture"}},
        )
        self.assertEqual(descriptor["model"]["tag"], "qwen3:4b-instruct")
        self.assertEqual(descriptor["model"]["digest"], "digest-fixture")
        self.assertEqual(descriptor["temperature"], 0.0)
        settings = descriptor["generation_settings"]
        self.assertFalse(settings["think"])
        self.assertTrue(settings["stream"])
        self.assertEqual(settings["timeout_seconds"], 300)
        self.assertEqual(settings["trials_per_case"], 3)
        self.assertEqual(settings["token_limit_profile"], "instruct")

    def test_failure_categories_distinguish_format_formula_truncation_and_content(self):
        result = {
            "trials": [
                {"final_score": {"failure_category": "json_schema_mismatch"}},
                {"final_score": {"failure_category": "incorrect_profit_margin_formula"}},
                {"final_score": {"failure_category": "output_truncated"}},
                {"final_score": {"failure_category": "abstention_check_failed"}},
            ]
        }
        self.assertEqual(
            _case_failure_types(result),
            ["content", "format/schema", "formula", "truncation"],
        )

    def test_ollama_comparison_stops_at_transport_error_and_reports_partial_counts(self):
        suite_version, cases = load_suite()
        calls = []

        class SyntheticTransportError(RuntimeError):
            category = "connection_error"

        def generate(_case, *, feedback=None):
            calls.append(feedback)
            raise SyntheticTransportError("Synthetic connection refusal.")

        with patch(
            "training.evaluation.ollama_runner._generator",
            return_value=generate,
        ):
            report = _ollama_run(
                "qwen3:4b",
                0.3,
                "http://localhost:11434",
                cases[:2],
                suite_version,
                trials_per_case=4,
                model_metadata={"digest": "fixture-digest"},
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(report["model"]["name"], "qwen3:4b")
        self.assertEqual(report["model"]["tag"], "qwen3:4b")
        self.assertEqual(report["model"]["digest"], "fixture-digest")
        self.assertEqual(report["generation_settings"]["temperature"], 0.3)
        self.assertFalse(report["generation_settings"]["think"])
        self.assertTrue(report["generation_settings"]["stream"])
        self.assertEqual(report["generation_settings"]["timeout_seconds"], 300)
        self.assertEqual(
            report["generation_settings"]["max_output_tokens_by_case"][
                "analysis_plan_profit_margin"
            ],
            2048,
        )
        self.assertEqual(len(report["cases"]), 2)
        interrupted = report["cases"][0]
        self.assertEqual(interrupted["status"], "incomplete")
        self.assertEqual(interrupted["evaluation_status"], "error")
        self.assertEqual(interrupted["requested_trial_count"], 4)
        self.assertEqual(interrupted["completed_trial_count"], 0)
        self.assertEqual(interrupted["pass_at_1"]["total"], 0)
        self.assertIsNone(interrupted["pass_at_1"]["rate"])
        self.assertEqual(interrupted["fallback_count"], 0)
        self.assertEqual(
            report["trial_counts"],
            {
                "requested": 8,
                "completed": 0,
                "incomplete": 8,
                "requested_per_case": 4,
            },
        )
        self.assertEqual(interrupted["error"]["category"], "connection_error")
        self.assertEqual(interrupted["interruption"]["category"], "connection_error")
        self.assertEqual(interrupted["interruption"]["trial_index"], 1)
        self.assertEqual(interrupted["error"]["type"], "SyntheticTransportError")
        self.assertLessEqual(len(interrupted["error"]["message"]), 200)
        self.assertEqual(report["cases"][1]["status"], "not_started")
        self.assertEqual(report["cases"][1]["completed_trials"], 0)
        row = _summary_row(report, interrupted)
        self.assertIsNone(row["pass_at_1"])
        self.assertIsNone(row["median_latency_ms"])
        self.assertEqual(row["requested_trial_count"], 4)
        self.assertEqual(row["completed_trial_count"], 0)

    def test_comparison_exit_code_distinguishes_abort_from_test_failure(self):
        for report_data, expected_code in (
            ({"status": "stopped_after_generation_error", "table": []}, 2),
            (
                {
                    "status": "complete",
                    "table": [{"evaluation_status": "failed"}],
                },
                1,
            ),
        ):
            with self.subTest(expected_code=expected_code), tempfile.TemporaryDirectory() as directory:
                report_path = Path(directory) / "summary.json"
                report_path.write_text(json.dumps(report_data), encoding="utf-8")
                output = StringIO()
                with patch(
                    "training.evaluation.comparison_runner.run_comparison",
                    return_value=report_path,
                ), redirect_stdout(output):
                    code = main(["--temperatures", "0"])
                self.assertEqual(code, expected_code)


if __name__ == "__main__":
    unittest.main()
