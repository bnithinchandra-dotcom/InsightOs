import unittest
from copy import deepcopy

from app.agent.config import AgentSettings
from app.agent.contracts import ToolResult
from app.agent.runtime import execute_agent_request
from app.agent.tools import ToolRegistry
from tests.test_agent_inspection_handoff import inspection_inputs, schema_record
from app.agent.inspection_handoff import build_inspection_handoff
from app.provenance import canonical_sha256


class FakeRegistry(ToolRegistry):
    def __init__(self, handoff, files=None, fail_first=None):
        super().__init__(definitions=())
        self.handoff = handoff
        self.files = files or [
            {
                "id": handoff["file_id"],
                "dataset_id": handoff["dataset_id"],
                "filename": "accounts.csv",
                "status": "Ready",
            }
        ]
        self.fail_first = fail_first
        self.calls = []

    def provider_schemas(self):
        return (
            {"function": {"name": "list_files"}},
            {"function": {"name": "get_inspection_handoff"}},
        )

    def execute(self, name, raw_arguments, *, context=None):
        self.calls.append((name, raw_arguments, context))
        if self.fail_first == name and sum(call[0] == name for call in self.calls) == 1:
            result = ToolResult(
                status="failed",
                summary="Temporary read failure.",
                error_code="tool_execution_failed",
            )
        elif name == "list_files":
            result = ToolResult(
                status="succeeded",
                summary="Found dataset files.",
                data=self.files,
            )
        elif name == "get_inspection_handoff":
            result = ToolResult(
                status="succeeded",
                summary="Retrieved inspection handoff.",
                data=self.handoff,
            )
        else:
            raise AssertionError(f"Unexpected tool: {name}")
        return "{}", result, name


class UnexpectedProvider:
    def complete(self, _request):
        raise AssertionError("A deterministic inspection must not call the model.")


def build_handoff(dataset_id=7):
    file_record, profile, quality, operations = inspection_inputs()
    handoff = build_inspection_handoff(
        file_record=file_record,
        profile_response=profile,
        quality_response=quality,
        schema_analysis=schema_record(),
        preprocessing_operations=operations,
    )
    if dataset_id != handoff["dataset_id"]:
        def remap_dataset_ids(value):
            if isinstance(value, dict):
                return {
                    key: (
                        dataset_id
                        if key == "dataset_id" and item == handoff["dataset_id"]
                        else remap_dataset_ids(item)
                    )
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [remap_dataset_ids(item) for item in value]
            return value

        handoff = remap_dataset_ids(handoff)
        handoff["handoff_id"] = canonical_sha256(
            {key: value for key, value in handoff.items() if key != "handoff_id"}
        )
    return handoff


def no_model_settings():
    return AgentSettings(
        provider="none",
        base_url=None,
        model=None,
        api_key=None,
        timeout_seconds=1,
        max_output_tokens=64,
        max_model_turns=2,
        max_tool_calls=2,
        max_history_messages=2,
    )


class OrchestratorTests(unittest.TestCase):
    def test_inspection_runs_typed_bounded_handoffs_and_validates_claims_without_model(self):
        registry = FakeRegistry(build_handoff())
        outcome = execute_agent_request(
            user_message="How many rows and columns are in this dataset?",
            history=[],
            context={"dataset_id": 7},
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )

        self.assertEqual(outcome.status, "completed")
        self.assertIn("has 3 rows", outcome.assistant_text)
        self.assertIn("has 2 columns", outcome.assistant_text)
        self.assertEqual(
            [call[0] for call in registry.calls],
            ["list_files", "get_inspection_handoff"],
        )
        trace = next(
            result["data"]
            for result in outcome.structured_results
            if result["tool"] == "orchestration_trace"
        )
        self.assertEqual(trace["outcome"], "completed")
        self.assertEqual(trace["max_steps"], 6)
        self.assertEqual(
            [stage["role"] for stage in trace["stages"] if stage["role"] != "data_intelligence"],
            [
                "planner_coordinator",
                "analytics_modelling",
                "verification",
                "insights_visualization",
            ],
        )
        self.assertTrue(all(stage["contract_version"] == 1 for stage in trace["stages"]))
        self.assertEqual(len(trace["task_id"]), 32)

    def test_missing_dataset_context_is_blocked_before_tool_execution(self):
        registry = FakeRegistry(build_handoff())
        outcome = execute_agent_request(
            user_message="How many rows are in this dataset?",
            history=[],
            context=None,
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.error_code, "dataset_context_required")
        self.assertIn("Select a dataset", outcome.assistant_text)
        self.assertEqual(registry.calls, [])

    def test_multiple_ready_files_block_ambiguous_inspection(self):
        registry = FakeRegistry(
            build_handoff(),
            files=[
                {"id": 11, "status": "Ready"},
                {"id": 12, "status": "Ready"},
            ],
        )
        outcome = execute_agent_request(
            user_message="How many rows are in this dataset?",
            history=[],
            context={"dataset_id": 7},
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.error_code, "file_selection_required")
        self.assertEqual([call[0] for call in registry.calls], ["list_files"])

    def test_read_only_tool_retries_once_then_completes(self):
        registry = FakeRegistry(build_handoff(), fail_first="list_files")
        outcome = execute_agent_request(
            user_message="How many rows are in this dataset?",
            history=[],
            context={"dataset_id": 7},
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(
            [call[0] for call in registry.calls],
            ["list_files", "list_files", "get_inspection_handoff"],
        )
        trace = next(
            result["data"]
            for result in outcome.structured_results
            if result["tool"] == "orchestration_trace"
        )
        self.assertEqual(trace["stages"][1]["retry_count"], 1)

    def test_unregistered_analytics_returns_explicit_unsupported_status(self):
        registry = FakeRegistry(build_handoff())
        outcome = execute_agent_request(
            user_message="What is the total sales amount?",
            history=[],
            context={"dataset_id": 7},
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "unsupported")
        self.assertEqual(outcome.error_code, "unsupported_capability")
        self.assertIn("cannot compute totals", outcome.assistant_text)
        self.assertEqual(registry.calls, [])

    def test_unknown_column_is_reported_from_the_inspected_schema(self):
        registry = FakeRegistry(build_handoff())
        outcome = execute_agent_request(
            user_message="How many missing values are in the made_up_field column?",
            history=[],
            context={"dataset_id": 7},
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "unsupported")
        self.assertEqual(outcome.error_code, "unknown_column")
        self.assertIn("made_up_field", outcome.assistant_text)
        self.assertIn("account_id, balance", outcome.assistant_text)

    def test_specific_column_request_only_reports_that_column(self):
        registry = FakeRegistry(build_handoff())
        outcome = execute_agent_request(
            user_message="How many missing values are in the balance column?",
            history=[],
            context={"dataset_id": 7},
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertIn("Column balance", outcome.assistant_text)
        self.assertIn("has 1 missing values", outcome.assistant_text)
        self.assertNotIn("account_id", outcome.assistant_text)

    def test_duplicate_count_is_reported_but_semantic_invalid_values_are_unsupported(self):
        registry = FakeRegistry(build_handoff())
        outcome = execute_agent_request(
            user_message="Which columns contain duplicate or invalid values?",
            history=[],
            context={"dataset_id": 7},
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "partial")
        self.assertIn("has 1 exact duplicate rows", outcome.assistant_text)
        self.assertIn("invalid-value detection is not supported", outcome.assistant_text)

    def test_tampered_handoff_cannot_produce_a_successful_claim(self):
        handoff = deepcopy(build_handoff())
        handoff["tables"][0]["row_count"] = 999
        registry = FakeRegistry(handoff)
        outcome = execute_agent_request(
            user_message="How many rows are in this dataset?",
            history=[],
            context={"dataset_id": 7},
            settings=no_model_settings(),
            provider=UnexpectedProvider(),
            registry=registry,
        )
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.error_code, "inspection_handoff_identity_invalid")
        self.assertNotIn("999", outcome.assistant_text)


if __name__ == "__main__":
    unittest.main()
