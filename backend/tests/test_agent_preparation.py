from __future__ import annotations

import threading
import unittest

from app.agent.contracts import ToolResult
from app.agent.preparation import PreparationError, SpecialistPreparationPlanner


class FixtureRegistry:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_quality = False

    def execute(self, name, arguments, *, context=None):
        self.calls.append(name)
        if name == "get_quality_report" and self.fail_quality:
            result = ToolResult(
                status="failed",
                summary="Stored quality data unavailable.",
                error_code="quality_unavailable",
            )
        elif name == "list_files":
            result = ToolResult(
                status="succeeded",
                summary="Found one file.",
                data=[{"id": 7, "filename": "sales.csv", "status": "Ready"}],
            )
        elif name == "get_quality_report":
            result = ToolResult(
                status="succeeded",
                summary="Retrieved stored quality results.",
                data={
                    "report_is_current": True,
                    "provenance": {"operation_id": "quality-1"},
                },
            )
        elif name == "get_inspection_handoff":
            result = ToolResult(
                status="succeeded",
                summary="Retrieved the validated persisted inspection handoff.",
                data={
                    "evidence_references": [
                        {
                            "kind": "quality_report",
                            "provenance_operation_id": "quality-run-1",
                        }
                    ]
                },
            )
        elif name == "list_eda_results":
            result = ToolResult(
                status="succeeded",
                summary="Retrieved stored EDA results.",
                data={"items": [{"id": 91, "status": "Completed"}]},
            )
        else:
            raise AssertionError(f"Unexpected preparation read: {name}")
        return "{}", result, name

    @staticmethod
    def analytics_capabilities():
        return [
            {
                "operation": "run_eda_analysis",
                "execution_kind": "deterministic",
                "supported_outputs": ["eda_result_reference", "provenance"],
            }
        ]


class SpecialistPreparationTests(unittest.TestCase):
    def setUp(self):
        self.planner = SpecialistPreparationPlanner(max_workers=1, max_in_flight=1)
        self.addCleanup(self.planner.shutdown)
        self.registry = FixtureRegistry()

    def test_prepares_existing_evidence_and_typed_context_without_inference(self):
        artifact = self.planner.prepare(
            registry=self.registry,
            workflow_id="a" * 32,
            dataset_id=42,
            file_id=7,
            source_fingerprint="source-v1",
            operation="run_eda_analysis",
            inspection={
                "source_fingerprint": "source-v1",
                "file": {
                    "filename": "sales.csv",
                    "status": "Ready",
                    "tables": [{"table_index": 0, "row_count": 12}],
                },
                "profile": {
                    "version": 1,
                    "tables": [
                        {
                            "row_count": 12,
                            "columns": [{"name": "sales", "physical_types": ["DOUBLE"]}],
                        }
                    ],
                },
            },
            evidence_references=[{"type": "profile", "id": 11}],
            analytics_outputs=[{"tool": "generate_evidence_report", "status": "succeeded"}],
        )

        self.assertEqual(artifact.llm_calls, 0)
        self.assertEqual(artifact.inventory["filename"], "sales.csv")
        self.assertEqual(
            artifact.dataset_inventory.data[0]["filename"],
            "sales.csv",
        )
        self.assertTrue(artifact.quality_results.data["report_is_current"])
        self.assertEqual(artifact.inspection_handoff.status, "available")
        self.assertEqual(artifact.eda_results.data["items"][0]["id"], 91)
        self.assertEqual(artifact.analytics_outputs[0]["tool"], "generate_evidence_report")
        self.assertEqual(artifact.provenance[-1]["operation_id"], "quality-1")
        self.assertEqual(artifact.tool_capabilities[0]["operation"], "run_eda_analysis")
        self.assertEqual(artifact.handoff_context.source_fingerprint, "source-v1")
        self.assertEqual(
            self.registry.calls,
            [
                "list_files",
                "get_inspection_handoff",
                "get_quality_report",
                "list_eda_results",
            ],
        )

    def test_unavailable_persisted_evidence_is_explicit_and_never_fabricated(self):
        self.registry.fail_quality = True
        artifact = self.planner.prepare(
            registry=self.registry,
            workflow_id="b" * 32,
            dataset_id=42,
            file_id=7,
            source_fingerprint="source-v2",
            operation="run_eda_analysis",
            inspection={"file": {}, "profile": {}},
            evidence_references=[],
        )

        self.assertEqual(artifact.quality_results.status, "failed")
        self.assertEqual(artifact.quality_results.data, None)
        self.assertEqual(artifact.quality_results.error_code, "quality_unavailable")
        self.assertEqual(
            artifact.provenance,
            [
                {
                    "kind": "quality_report",
                    "provenance_operation_id": "quality-run-1",
                }
            ],
        )

    def test_async_preparation_is_bounded_and_runs_off_thread(self):
        entered = threading.Event()
        proceed = threading.Event()
        registry = self.registry
        original_execute = registry.execute

        def delayed_execute(name, arguments, *, context=None):
            entered.set()
            proceed.wait(1)
            return original_execute(name, arguments, context=context)

        registry.execute = delayed_execute
        future = self.planner.prepare_async(
            registry=registry,
            workflow_id="c" * 32,
            dataset_id=42,
            file_id=7,
            source_fingerprint="source-v3",
            operation="run_eda_analysis",
            inspection={"file": {}, "profile": {}},
            evidence_references=[],
        )
        self.assertTrue(entered.wait(1))
        with self.assertRaises(PreparationError) as raised:
            self.planner.prepare_async(
                registry=registry,
                workflow_id="d" * 32,
                dataset_id=42,
                file_id=7,
                source_fingerprint="source-v3",
                operation="run_eda_analysis",
                inspection={"file": {}, "profile": {}},
                evidence_references=[],
            )
        self.assertEqual(raised.exception.code, "agent_preparation_busy")
        proceed.set()
        self.assertEqual(future.result(timeout=2).llm_calls, 0)
