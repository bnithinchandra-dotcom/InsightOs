import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.agent.config import AgentSettings
from app.agent.contracts import (
    ModelResponse,
    ProviderMessage,
    ToolCall,
    ToolResult,
    UsageMetadata,
)
from app.agent.runtime import execute_agent_request
from app.agent.tools import ToolRegistry
from app.main import MonthlyForecastRequest


def _settings():
    return AgentSettings(
        provider="ollama",
        base_url="http://127.0.0.1:11434",
        model="qwen3:8b",
        api_key=None,
        timeout_seconds=30,
        max_output_tokens=512,
        max_model_turns=3,
        max_tool_calls=4,
        max_history_messages=10,
    )


class EngineRequestProvider:
    def __init__(self, tool_name, arguments, *, fail_role=None):
        self.tool_name = tool_name
        self.arguments = arguments
        self.fail_role = fail_role
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if request.tools:
            return ModelResponse(
                message=ProviderMessage(
                    role="assistant",
                    content="",
                    tool_calls=(
                    ToolCall(
                        id="engine-call-1",
                        name=self.tool_name,
                        arguments=self.arguments,
                    ),
                ),
                ),
                finish_reason="tool_calls",
                usage=UsageMetadata(input_tokens=100, output_tokens=10),
            )
        prompt = json.loads(request.messages[-1].content)
        system = request.messages[0].content
        if self.fail_role and self.fail_role in system:
            return ModelResponse(
                message=ProviderMessage(role="assistant", content="not-json"),
                finish_reason="stop",
            )
        context = prompt["context"]
        if "Agent 1, Data Intelligence" in system:
            inspection = context["inspection"]
            profile = inspection.get("profile") or {}
            fields = [
                column["name"]
                for table in profile.get("tables", [])
                for column in table.get("columns", [])
                if isinstance(column, dict) and isinstance(column.get("name"), str)
            ]
            response = {
                "contract_version": 1,
                "status": "completed",
                "summary": "The selected file has a stored numeric sales field.",
                "observed_fields": fields,
                "recommended_operation": context["operation"],
                "evidence_references": context["evidence_references"],
            }
        elif "Agent 2, Analytics and Modelling" in system:
            response = {
                "contract_version": 1,
                "status": "approved",
                "tool_name": context["requested_tool"],
                "arguments": context["requested_arguments"],
                "summary": "The requested deterministic engine is appropriate.",
                "evidence_references": context["data_intelligence"]["evidence_references"],
            }
        elif "Agent 3, Insights and Visualization" in system:
            response = {
                "contract_version": 1,
                "status": "completed",
                "summary": "The requested analysis is complete; see the validated engine evidence.",
                "evidence_references": context["allowed_evidence_references"],
                "visualization_specification": None,
            }
        else:
            self.fail("Unexpected provider request in specialist workflow.")
        return ModelResponse(
            message=ProviderMessage(
                role="assistant",
                content=json.dumps(response),
            ),
            finish_reason="stop",
            usage=UsageMetadata(input_tokens=100, output_tokens=10),
        )


class DatasetEngineToolTests(unittest.TestCase):
    def setUp(self):
        self.main = SimpleNamespace(
            get_dataset_file=Mock(
                return_value={
                    "id": 7,
                    "dataset_id": 42,
                    "status": "Ready",
                    "normalization_status": "Ready",
                    "filename": "sales.csv",
                    "checksum": "source-checksum",
                    "normalization_result": {
                        "tables": [
                            {
                                "table_index": 0,
                                "table_name": "sales",
                                "row_count": 12,
                                "column_count": 1,
                                "schema": [{"name": "sales", "type": "DOUBLE"}],
                            }
                        ]
                    },
                    "profile_result": {
                        "tables": [
                            {
                                "row_count": 12,
                                "column_count": 1,
                                "columns": [
                                    {
                                        "name": "sales",
                                        "missing_value_count": 0,
                                        "physical_types": ["DOUBLE"],
                                    }
                                ],
                            }
                        ]
                    },
                }
            ),
            normalize_dataset_file=Mock(
                return_value={
                    "dataset_id": 42,
                    "file_id": 7,
                    "status": "Ready",
                    "normalization_result": {
                        "tables": [{"row_count": 12, "column_count": 2}]
                    },
                }
            ),
            analyze_dataset_file_quality=Mock(
                return_value={
                    "dataset_id": 42,
                    "file_id": 7,
                    "status": "Completed",
                    "quality_report": {"findings": [{"title": "Missing values"}]},
                }
            ),
            analyze_dataset_file_eda=Mock(
                return_value={
                    "id": 91,
                    "dataset_id": 42,
                    "file_id": 7,
                    "status": "Completed",
                    "result": {
                        "analysis_type": "univariate",
                        "status": "completed",
                        "source": {"dataset_id": 42, "file_id": 7},
                        "rows_analyzed": 12,
                        "analyzed_columns": ["sales"],
                        "columns": [
                            {
                                "name": "sales",
                                "statistics": {"mean": 15.5},
                            }
                        ],
                    },
                    "provenance": {"source_sha256": "abc"},
                }
            ),
        )
        self.main_patch = patch("app.agent.tools._main", return_value=self.main)
        self.main_patch.start()
        self.addCleanup(self.main_patch.stop)

    def test_engine_tools_are_offered_only_for_explicit_selected_dataset_intent(self):
        registry = ToolRegistry()
        default_tools = {
            item["function"]["name"] for item in registry.provider_schemas()
        }
        self.assertNotIn("run_eda_analysis", default_tools)
        self.assertNotIn("normalize_file", default_tools)
        self.assertNotIn("run_quality_analysis", default_tools)

        eda_tools = {
            item["function"]["name"]
            for item in registry.provider_schemas(
                context={
                    "dataset_id": 42,
                    "user_message": "Run EDA on sales in the selected dataset.",
                }
            )
        }
        self.assertIn("run_eda_analysis", eda_tools)
        self.assertNotIn("normalize_file", eda_tools)

        normalization_tools = {
            item["function"]["name"]
            for item in registry.provider_schemas(
                context={
                    "dataset_id": 42,
                    "user_message": "Run normalization on the selected file.",
                }
            )
        }
        self.assertIn("normalize_file", normalization_tools)

        mention_only_tools = {
            item["function"]["name"]
            for item in registry.provider_schemas(
                context={
                    "dataset_id": 42,
                    "user_message": "The previous normalization was slow.",
                }
            )
        }
        self.assertNotIn("normalize_file", mention_only_tools)

        missing_context_tools = {
            item["function"]["name"]
            for item in registry.provider_schemas(
                context={"user_message": "Run EDA on sales."}
            )
        }
        self.assertNotIn("run_eda_analysis", missing_context_tools)

    def test_monthly_forecast_is_registered_only_for_explicit_selected_dataset_requests(self):
        registry = ToolRegistry()
        forecast_tools = {
            item["function"]["name"]
            for item in registry.provider_schemas(
                context={
                    "dataset_id": 42,
                    "user_message": "Generate a monthly forecast of net_sales.",
                }
            )
        }
        self.assertIn("run_monthly_forecast", forecast_tools)
        self.assertNotIn(
            "run_monthly_forecast",
            {
                item["function"]["name"]
                for item in registry.provider_schemas(
                    context={
                        "dataset_id": 42,
                        "user_message": "Explain what a forecast is.",
                    }
                )
            },
        )
        self.assertNotIn(
            "run_monthly_forecast",
            {
                item["function"]["name"]
                for item in registry.provider_schemas(
                    context={"user_message": "Generate a monthly forecast."}
                )
            },
        )

    def test_monthly_forecast_runs_through_specialists_and_returns_verified_values(self):
        self.main.MonthlyForecastRequest = MonthlyForecastRequest
        self.main.create_monthly_forecast = Mock(
            return_value={
                "workflow_id": "a" * 32,
                "task_id": "b" * 32,
                "status": "completed",
            }
        )
        result = {
            "dataset_id": 42,
            "file_id": 7,
            "table_index": 0,
            "date_column": "order_date",
            "target_column": "net_sales",
            "aggregation": "sum",
            "horizon": 1,
            "source_fingerprint": "c" * 64,
            "normalized_artifact_sha256": "d" * 64,
            "historical": [{"period": "2022-12", "observed": 140.0}],
            "forecast": [{"period": "2023-01", "predicted": 150.25, "kind": "forecast"}],
            "evaluation": {
                "candidate": {"mae": 4.0, "rmse": 5.0, "wape": 2.5},
                "baseline": {"mae": 8.0, "rmse": 9.0, "wape": 5.0},
                "baseline_name": "last_observation",
                "beats_baseline": True,
            },
            "chart": {
                "title": "net_sales monthly forecast",
                "chart_type": "line",
                "x_field": "order_date",
                "y_field": "net_sales",
                "data": {"labels": ["2022-12", "2023-01"], "series": []},
            },
            "warnings": [],
            "limitations": [],
            "references": [
                {
                    "type": "normalized_source",
                    "dataset_id": 42,
                    "file_id": 7,
                    "source_fingerprint": "c" * 64,
                }
            ],
        }
        self.main.get_monthly_forecast = Mock(
            return_value={
                "workflow_id": "a" * 32,
                "status": "completed",
                "created_at": "2026-10-28T00:00:00",
                "completed_at": "2026-10-28T00:00:01",
                "task": {
                    "id": "b" * 32,
                    "status": "completed",
                    "result": result,
                },
            }
        )
        provider = EngineRequestProvider(
            "run_monthly_forecast",
            {
                "dataset_id": 42,
                "file_id": 7,
                "table_index": 0,
                "date_column": "order_date",
                "target_column": "net_sales",
                "aggregation": "sum",
                "horizon": 1,
            },
        )

        outcome = execute_agent_request(
            user_message="Generate a monthly forecast of net_sales.",
            history=[],
            context={"dataset_id": 42, "conversation_id": 301},
            settings=_settings(),
            provider=provider,
            registry=ToolRegistry(),
        )

        self.assertEqual(outcome.status, "completed")
        self.assertIn("2023-01: 150.25", outcome.assistant_text)
        self.assertIn("MAE 4.00 vs selected baseline 8.00", outcome.assistant_text)
        self.main.create_monthly_forecast.assert_called_once()
        self.main.get_monthly_forecast.assert_called_once_with("a" * 32)
        forecast_result = next(
            item
            for item in outcome.structured_results
            if item["tool"] == "run_monthly_forecast"
        )
        self.assertEqual(forecast_result["status"], "succeeded")
        self.assertEqual(forecast_result["data"]["workflow_id"], "a" * 32)
        self.assertEqual(forecast_result["data"]["forecast"][0]["predicted"], 150.25)

    def test_agent_two_runs_real_eda_endpoint_with_validated_scoped_arguments(self):
        provider = EngineRequestProvider(
            "run_eda_analysis",
            {
                "dataset_id": 42,
                "file_id": 7,
                "analysis_type": "univariate",
                "table_index": 0,
                "selected_columns": ["sales"],
            },
        )
        outcome = execute_agent_request(
            user_message="Run EDA on the sales column.",
            history=[],
            context={"dataset_id": 42, "conversation_id": 301},
            settings=_settings(),
            provider=provider,
            registry=ToolRegistry(),
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.activities[0].tool_name, "run_eda_analysis")
        self.assertEqual(outcome.activities[0].status, "succeeded")
        self.assertIn("analyzed 12 row(s)", outcome.assistant_text)
        self.assertIn("sales", outcome.assistant_text)
        self.main.analyze_dataset_file_eda.assert_called_once_with(
            42,
            7,
            request={
                "schema_version": 1,
                "analysis_type": "univariate",
                "table_index": 0,
                "selected_columns": ["sales"],
            },
        )
        result = next(
            item
            for item in outcome.structured_results
            if item["tool"] == "run_eda_analysis"
        )
        self.assertEqual(result["data"]["id"], 91)
        self.assertEqual(
            result["data"]["agent1_inspection"]["file"]["filename"],
            "sales.csv",
        )
        self.assertEqual(
            result["data"]["agent1_inspection"]["profile"]["tables"][0]["row_count"],
            12,
        )
        self.assertEqual(
            result["data"]["agent1_inspection"]["source_fingerprint"],
            "source-checksum",
        )
        self.assertEqual(
            result["data"]["provenance"]["source_sha256"],
            "abc",
        )
        self.assertEqual(self.main.get_dataset_file.call_count, 2)
        trace = next(
            item
            for item in outcome.structured_results
            if item["tool"] == "orchestration_trace"
        )
        self.assertEqual(trace["data"]["outcome"], "completed")
        self.assertEqual(trace["data"]["file_id"], 7)
        self.assertEqual(trace["data"]["conversation_id"], 301)
        self.assertEqual(
            trace["data"]["source_identity"]["source_fingerprint"],
            "source-checksum",
        )
        self.assertEqual(len(trace["data"]["execution_plan"]), 5)
        self.assertEqual(
            trace["data"]["execution_plan"][1]["source_fingerprint"],
            "source-checksum",
        )
        self.assertEqual(
            trace["data"]["execution_plan"][1]["depends_on"],
            [f"{trace['data']['task_id']}:plan"],
        )
        self.assertEqual(
            trace["data"]["execution_plan"][2]["input_references"][0]["type"],
            "dataset_file_inspection",
        )
        self.assertEqual(
            trace["data"]["execution_plan"][3]["depends_on"],
            [f"{trace['data']['task_id']}:engine"],
        )
        self.assertEqual(
            result["workflow_id"],
            trace["data"]["task_id"],
        )
        self.assertEqual(
            [stage["role"] for stage in trace["data"]["stages"]],
            [
                "planner_coordinator",
                "data_intelligence",
                "analytics_modelling",
                "verification",
                "insights_visualization",
            ],
        )
        self.assertEqual(len(provider.requests), 4)
        analytics_context = json.loads(
            provider.requests[2].messages[-1].content
        )["context"]
        presentation_context = json.loads(
            provider.requests[3].messages[-1].content
        )["context"]
        self.assertEqual(
            analytics_context["data_intelligence"]["observed_fields"],
            ["sales"],
        )
        self.assertEqual(
            presentation_context["engine_result"]["data"]["id"],
            91,
        )

        self.assertEqual(
            presentation_context["data_intelligence"]["observed_fields"],
            ["sales"],
        )
        self.assertEqual(
            [
                item["tool"]
                for item in outcome.structured_results
                if item["tool"].startswith("agent")
            ],
            [
                "agent1_data_intelligence",
                "agent2_analytics_decision",
                "agent3_insights_visualization",
            ],
        )
        self.assertEqual(
            outcome.structured_results[-1]["data"]["model"],
            "qwen3:8b",
        )
        self.assertEqual(
            trace["data"]["models"],
            {
                "orchestrator": "qwen3:8b",
                "data_intelligence": "qwen3:8b",
                "analytics_modelling": "qwen3:8b",
                "insights_visualization": "qwen3:8b",
            },
        )
        verification_references = trace["data"]["stages"][3]["evidence_references"]
        self.assertIn(
            "execution_placement",
            [reference["type"] for reference in verification_references],
        )
        self.assertIn("sales.csv", trace["data"]["stages"][1]["summary"])
        self.assertIn("1 normalized table(s)", trace["data"]["stages"][1]["summary"])

        wrong_source = self.main.analyze_dataset_file_eda.return_value.copy()
        wrong_source["dataset_id"] = 42
        wrong_source["result"] = {
            **wrong_source["result"],
            "source": {"dataset_id": 42, "file_id": 8},
        }
        self.main.analyze_dataset_file_eda.return_value = wrong_source
        mismatched_file = execute_agent_request(
            user_message="Run EDA on the sales column.",
            history=[],
            context={"dataset_id": 42},
            settings=_settings(),
            provider=EngineRequestProvider(
                "run_eda_analysis",
                {
                    "dataset_id": 42,
                    "file_id": 7,
                    "analysis_type": "univariate",
                    "table_index": 0,
                    "selected_columns": ["sales"],
                },
            ),
            registry=ToolRegistry(),
        )
        self.assertEqual(mismatched_file.error_code, "engine_result_scope_mismatch")

    def test_specialist_stages_are_scheduled_before_execution_with_validated_handoffs(self):
        provider = EngineRequestProvider(
            "run_eda_analysis",
            {
                "dataset_id": 42,
                "file_id": 7,
                "analysis_type": "univariate",
                "table_index": 0,
                "selected_columns": ["sales"],
            },
        )
        scheduled = []
        completed_keys = set()

        def scheduler(name, arguments, execute, **kwargs):
            task_key = f"task-{len(scheduled) + 1}"
            dependencies = kwargs.get("depends_on") or []
            self.assertTrue(set(dependencies).issubset(completed_keys))
            provider_calls_before_execute = len(provider.requests)
            scheduled.append((name, arguments, dependencies, provider_calls_before_execute))
            raw, result, label = execute()
            self.assertEqual(result.status, "succeeded", result.summary)
            if name.startswith("specialist_"):
                handoff = result.data["handoff"]
                self.assertTrue(handoff["verified"])
                self.assertEqual(handoff["source_fingerprint"], "source-checksum")
                self.assertEqual(handoff["dataset_id"], 42)
                self.assertEqual(handoff["file_id"], 7)
                self.assertEqual(handoff["parameters"]["file_id"], 7)
                self.assertEqual(
                    handoff["artifact_references"],
                    handoff["output"]["evidence_references"],
                )
                if handoff["stage"] == "data_intelligence":
                    self.assertIn(
                        "sales",
                        [
                            column["name"]
                            for table in handoff["verified_schema"]["tables"]
                            for column in table["columns"]
                        ],
                    )
                if handoff["stage"] == "insights_visualization":
                    self.assertEqual(
                        handoff["actual_operations"][0]["operation"],
                        "run_eda_analysis",
                    )
                    self.assertEqual(
                        len(
                            handoff["actual_operations"][0][
                                "execution_fingerprint"
                            ]
                        ),
                        64,
                    )
            completed_keys.add(task_key)
            return raw, result, label, task_key

        outcome = execute_agent_request(
            user_message="Run EDA on the sales column.",
            history=[],
            context={"dataset_id": 42, "conversation_id": 301},
            settings=_settings(),
            provider=provider,
            registry=ToolRegistry(),
            workflow_scheduler=scheduler,
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(
            [item[0] for item in scheduled],
            [
                "specialist_data_intelligence",
                "specialist_analytics_modelling",
                "run_eda_analysis",
                "specialist_insights_visualization",
            ],
        )
        self.assertEqual(scheduled[0][2], [])
        self.assertEqual(scheduled[1][2], ["task-1"])
        self.assertEqual(scheduled[2][2], ["task-2"])
        self.assertEqual(scheduled[3][2], ["task-1", "task-2", "task-3"])
        self.assertEqual([item[3] for item in scheduled[:2]], [1, 2])

    def test_malformed_specialist_handoff_blocks_all_dependent_execution(self):
        provider = EngineRequestProvider(
            "run_eda_analysis",
            {
                "dataset_id": 42,
                "file_id": 7,
                "analysis_type": "univariate",
                "table_index": 0,
                "selected_columns": ["sales"],
            },
        )
        scheduled = []

        def scheduler(name, _arguments, execute, **kwargs):
            scheduled.append((name, kwargs.get("depends_on") or []))
            raw, result, label = execute()
            if name == "specialist_data_intelligence":
                result = ToolResult(
                    status="succeeded",
                    summary=result.summary,
                    data={
                        **result.data,
                        "handoff": {
                            **result.data["handoff"],
                            "dataset_id": 999,
                        },
                    },
                    references=result.references,
                )
            return raw, result, label, f"task-{len(scheduled)}"

        outcome = execute_agent_request(
            user_message="Run EDA on the sales column.",
            history=[],
            context={"dataset_id": 42, "conversation_id": 301},
            settings=_settings(),
            provider=provider,
            registry=ToolRegistry(),
            workflow_scheduler=scheduler,
        )

        self.assertEqual([item[0] for item in scheduled], ["specialist_data_intelligence"])
        self.assertEqual(outcome.error_code, "engine_execution_incomplete")
        self.assertFalse(self.main.analyze_dataset_file_eda.called)
        failed = next(
            item
            for item in outcome.structured_results
            if item["tool"] == "agent1_data_intelligence"
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "specialist_handoff_invalid")

    def test_engine_result_with_mismatched_source_identity_is_not_presented(self):
        engine_data = self.main.analyze_dataset_file_eda.return_value.copy()
        engine_data["dataset_id"] = 99
        self.main.analyze_dataset_file_eda.return_value = engine_data
        outcome = execute_agent_request(
            user_message="Run EDA on the sales column.",
            history=[],
            context={"dataset_id": 42},
            settings=_settings(),
            provider=EngineRequestProvider(
                "run_eda_analysis",
                {
                    "dataset_id": 42,
                    "file_id": 7,
                    "analysis_type": "univariate",
                    "table_index": 0,
                    "selected_columns": ["sales"],
                },
            ),
            registry=ToolRegistry(),
        )

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "engine_result_scope_mismatch")
        rejected = next(
            item
            for item in outcome.structured_results
            if item["tool"] == "run_eda_analysis"
        )
        self.assertEqual(rejected["status"], "failed")
        self.assertIsNone(rejected["data"])
        self.assertEqual(
            rejected["error_code"],
            "engine_result_scope_mismatch",
        )
        self.assertEqual(outcome.activities[0].status, "failed")
        self.assertIn("could not be verified", outcome.assistant_text)
        self.assertNotIn("analyzed 12 row(s)", outcome.assistant_text)
        trace = next(
            item
            for item in outcome.structured_results
            if item["tool"] == "orchestration_trace"
        )
        self.assertEqual(trace["data"]["outcome"], "failed")
        self.assertEqual(trace["data"]["stages"][3]["status"], "blocked")

    def test_agent_failures_block_dependents_and_preserve_engine_results(self):
        for failed_role in (
            "Agent 1, Data Intelligence",
            "Agent 2, Analytics and Modelling",
            "Agent 3, Insights and Visualization",
        ):
            with self.subTest(role=failed_role):
                self.main.analyze_dataset_file_eda.reset_mock()
                outcome = execute_agent_request(
                    user_message="Run EDA on the sales column.",
                    history=[],
                    context={"dataset_id": 42},
                    settings=_settings(),
                    provider=EngineRequestProvider(
                        "run_eda_analysis",
                        {
                            "dataset_id": 42,
                            "file_id": 7,
                            "analysis_type": "univariate",
                            "table_index": 0,
                            "selected_columns": ["sales"],
                        },
                        fail_role=failed_role,
                    ),
                    registry=ToolRegistry(),
                )

                if failed_role == "Agent 1, Data Intelligence":
                    self.assertEqual(outcome.status, "failed")
                    self.main.analyze_dataset_file_eda.assert_not_called()
                elif failed_role == "Agent 2, Analytics and Modelling":
                    self.assertEqual(outcome.status, "failed")
                    self.main.analyze_dataset_file_eda.assert_not_called()
                else:
                    self.assertEqual(outcome.status, "partial")
                    self.main.analyze_dataset_file_eda.assert_called_once()
                    self.assertTrue(
                        any(
                            item["tool"] == "run_eda_analysis"
                            and item["status"] == "succeeded"
                            for item in outcome.structured_results
                        )
                    )
                    self.assertTrue(
                        any(
                            item["tool"] == "agent3_insights_visualization"
                            and item["status"] == "failed"
                            for item in outcome.structured_results
                        )
                    )
                    trace = next(
                        item
                        for item in outcome.structured_results
                        if item["tool"] == "orchestration_trace"
                    )
                    self.assertEqual(trace["data"]["outcome"], "partial")
                    self.assertEqual(
                        trace["data"]["stages"][-1]["status"],
                        "failed",
                    )

    def test_normalization_tool_runs_only_after_explicit_user_request(self):
        provider = EngineRequestProvider(
            "normalize_file",
            {"dataset_id": 42, "file_id": 7},
        )
        outcome = execute_agent_request(
            user_message="Normalize the selected file.",
            history=[],
            context={"dataset_id": 42},
            settings=_settings(),
            provider=provider,
            registry=ToolRegistry(),
        )

        self.assertEqual(outcome.status, "completed")
        self.assertIn("Normalization completed", outcome.assistant_text)
        self.main.normalize_dataset_file.assert_called_once_with(42, 7)

    def test_quality_analysis_uses_existing_engine_and_reports_failure(self):
        provider = EngineRequestProvider(
            "run_quality_analysis",
            {"dataset_id": 42, "file_id": 7},
        )
        outcome = execute_agent_request(
            user_message="Run quality analysis on the selected file.",
            history=[],
            context={"dataset_id": 42},
            settings=_settings(),
            provider=provider,
            registry=ToolRegistry(),
        )

        self.assertEqual(outcome.status, "completed")
        self.assertIn("1 finding(s)", outcome.assistant_text)
        self.main.analyze_dataset_file_quality.assert_called_once_with(42, 7)

        partial_data = self.main.analyze_dataset_file_quality.return_value.copy()
        partial_data["status"] = "PartiallyCompleted"
        self.main.analyze_dataset_file_quality.return_value = partial_data
        partial_outcome = execute_agent_request(
            user_message="Run quality analysis on the selected file.",
            history=[],
            context={"dataset_id": 42},
            settings=_settings(),
            provider=EngineRequestProvider(
                "run_quality_analysis",
                {"dataset_id": 42, "file_id": 7},
            ),
            registry=ToolRegistry(),
        )
        self.assertEqual(partial_outcome.status, "partial")
        partial_trace = next(
            item
            for item in partial_outcome.structured_results
            if item["tool"] == "orchestration_trace"
        )
        self.assertEqual(partial_trace["data"]["outcome"], "partial")

        self.main.analyze_dataset_file_quality.side_effect = HTTPException(
            status_code=409,
            detail={
                "code": "normalization_required",
                "message": "Quality analysis requires ready normalized output.",
            },
        )
        failed_outcome = execute_agent_request(
            user_message="Run quality analysis on the selected file.",
            history=[],
            context={"dataset_id": 42},
            settings=_settings(),
            provider=EngineRequestProvider(
                "run_quality_analysis",
                {"dataset_id": 42, "file_id": 7},
            ),
            registry=ToolRegistry(),
        )
        self.assertEqual(failed_outcome.status, "failed")
        engine_failure = next(
            item
            for item in failed_outcome.structured_results
            if item["tool"] == "run_quality_analysis"
        )
        self.assertEqual(engine_failure["error_code"], "normalization_required")
        self.assertEqual(engine_failure["data"]["agent1_inspection"]["file_id"], 7)
        self.assertIn("No successful result is available", failed_outcome.assistant_text)

    def test_tool_rejects_unselected_or_cross_dataset_engine_requests(self):
        registry = ToolRegistry()
        _, missing_context, _ = registry.execute(
            "run_eda_analysis",
            {
                "dataset_id": 42,
                "file_id": 7,
                "analysis_type": "univariate",
                "selected_columns": ["sales"],
            },
            context={"user_message": "Run EDA on sales."},
        )
        self.assertEqual(missing_context.error_code, "engine_execution_not_authorized")
        self.main.analyze_dataset_file_eda.assert_not_called()

        _, mismatched, _ = registry.execute(
            "run_eda_analysis",
            {
                "dataset_id": 99,
                "file_id": 7,
                "analysis_type": "univariate",
                "selected_columns": ["sales"],
            },
            context={
                "dataset_id": 42,
                "user_message": "Run EDA on sales.",
            },
        )
        self.assertEqual(mismatched.error_code, "dataset_scope_mismatch")
        self.main.analyze_dataset_file_eda.assert_not_called()

        self.main.get_dataset_file.side_effect = HTTPException(
            status_code=404,
            detail="Dataset file not found.",
        )
        _, unauthorized, _ = registry.execute(
            "run_eda_analysis",
            {
                "dataset_id": 42,
                "file_id": 7,
                "analysis_type": "univariate",
                "selected_columns": ["sales"],
            },
            context={
                "dataset_id": 42,
                "user_message": "Run EDA on sales.",
            },
        )
        self.assertEqual(unauthorized.error_code, "http_404")
        self.main.analyze_dataset_file_eda.assert_not_called()


if __name__ == "__main__":
    unittest.main()