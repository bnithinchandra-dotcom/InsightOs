import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.agent.config import AgentSettings
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderMessage,
    ToolResult,
)
from app.agent.providers import OllamaProvider, OpenAICompatibleProvider
from app.agent.runtime import execute_agent_request
from app.agent.runtime import (
    _runtime_state_sets,
    _slim_contract_prompt,
    _trusted_runtime_state,
)
from app.agent.slim_orchestrator import (
    FEW_SHOT_BUNDLE_SHA256,
    GOLD_RESPONSES_SHA256,
    SLIM_ORCHESTRATOR_INSTRUCTIONS,
    SlimOrchestratorTurn,
    classify_evidence_provenance,
    load_few_shot_examples,
    response_json_schema,
    validate_runtime_turn,
)
from app.agent.tools import StrictArguments, ToolDefinition, ToolRegistry


class SequenceProvider:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ModelResponse(
            ProviderMessage(role="assistant", content=next(self.outputs)),
            finish_reason="stop",
        )


class FakeHttpResponse:
    status_code = 200
    is_success = True

    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield self.body


class FakeHttpClient:
    def __init__(self, response, **_kwargs):
        self.response = response
        self.request = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url, json=None, headers=None):
        self.request = (method, url, json, headers)
        return self.response


def _settings(provider="ollama") -> AgentSettings:
    return AgentSettings(
        provider=provider,
        base_url="http://provider.invalid",
        model="qwen3:8b",
        api_key="fixture-key" if provider != "ollama" else None,
        timeout_seconds=1,
        max_output_tokens=256,
        max_model_turns=3,
        max_tool_calls=2,
        max_history_messages=4,
    )


class EmptyArguments(StrictArguments):
    pass


class SlimOrchestratorTests(unittest.TestCase):
    @staticmethod
    def _turn(**overrides):
        response = {
            "decision": "answer",
            "intent": "Respond to the user.",
            "plan": [],
            "tool_calls": [],
            "handoff_request": None,
            "response": "I can help with that.",
            "clarification_questions": [],
        }
        response.update(overrides)
        return SlimOrchestratorTurn.model_validate(response)

    def test_all_twelve_gold_outputs_match_the_slim_contract(self):
        examples = load_few_shot_examples()
        self.assertEqual(len(examples), 12)
        for example in examples:
            with self.subTest(prompt=example["prompt"]):
                turn = SlimOrchestratorTurn.model_validate(example["response"])
                if turn.handoff_request is not None:
                    self.assertEqual(
                        set(turn.handoff_request.model_fields_set),
                        {"agent", "objective"},
                    )
                self.assertNotIn("evidence_references", example["response"])
                self.assertNotIn("task_id", example["response"])
                self.assertNotIn("workflow_id", example["response"])
        self.assertEqual(
            GOLD_RESPONSES_SHA256,
            "62112cd56975f335e8016a6eb6c6099320a18439e1f84cfae4f76c9d3a20ab7d",
        )
        backend = Path(__file__).parents[1]
        gold_path = backend / "training" / "milestone5" / "qwen3-8b-gold-responses-v1.json"
        benchmark_path = (
            backend
            / "training"
            / "milestone5"
            / "qwen3-8b-orchestration-benchmark-v4.json"
        )
        bundle_path = (
            backend / "app" / "agent" / "orchestrator_few_shot_v1.json"
        )
        gold_bytes = gold_path.read_bytes()
        bundle_bytes = bundle_path.read_bytes()
        self.assertEqual(hashlib.sha256(gold_bytes).hexdigest(), GOLD_RESPONSES_SHA256)
        self.assertEqual(
            hashlib.sha256(bundle_bytes).hexdigest(),
            FEW_SHOT_BUNDLE_SHA256,
        )
        gold = json.loads(gold_bytes)
        benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
        benchmark_cases = {case["id"]: case for case in benchmark["cases"]}
        bundle = json.loads(bundle_bytes)
        self.assertEqual(set(bundle["examples"][0]), {"case_id", "prompt", "response"})
        self.assertEqual(
            {example["case_id"] for example in bundle["examples"]},
            set(gold),
        )
        for example in bundle["examples"]:
            case_id = example["case_id"]
            original = gold[case_id]
            handoff = original["specialist_handoff"]
            self.assertEqual(example["prompt"], benchmark_cases[case_id]["prompt"])
            self.assertEqual(
                example["response"],
                {
                    "decision": original["decision"],
                    "intent": original["intent"],
                    "plan": original["plan"],
                    "tool_calls": original["tool_calls"],
                    "handoff_request": (
                        {
                            "agent": handoff["agent"],
                            "objective": handoff["objective"],
                        }
                        if handoff is not None
                        else None
                    ),
                    "response": original["response"],
                    "clarification_questions": original["clarification_questions"],
                },
            )
            for proposal in example["response"]["tool_calls"]:
                dataset_id = proposal["arguments"].get("dataset_id")
                registry = ToolRegistry()
                registry.validate_proposal(
                    proposal["name"],
                    proposal["arguments"],
                    context={
                        "dataset_id": dataset_id,
                        "user_message": example["prompt"],
                    },
                )

    def test_response_schema_limits_tool_names_and_arguments_to_registry(self):
        registry = ToolRegistry()
        available = registry.provider_schemas(
            context={
                "dataset_id": 42,
                "user_message": "List files in dataset 42.",
            }
        )
        schema = response_json_schema(available)
        proposals = schema["properties"]["tool_calls"]["items"]["oneOf"]
        names = {item["properties"]["name"]["const"] for item in proposals}
        self.assertEqual(names, {item["function"]["name"] for item in available})
        list_files = next(
            item for item in proposals if item["properties"]["name"]["const"] == "list_files"
        )
        self.assertEqual(
            set(list_files["properties"]["arguments"]["required"]),
            {"dataset_id"},
        )
        self.assertFalse(list_files["additionalProperties"])
        self.assertNotIn("workflow_id", SlimOrchestratorTurn.model_fields)
        self.assertNotIn("evidence_references", SlimOrchestratorTurn.model_fields)

    def test_response_schema_disallows_tools_when_none_are_exposed(self):
        schema = response_json_schema(())
        self.assertEqual(
            schema["properties"]["tool_calls"]["maxItems"],
            0,
        )

    def test_model_prompt_excludes_benchmark_examples_and_clarifies_decisions(self):
        prompt = _slim_contract_prompt((), {})
        contract = json.loads(prompt.rsplit("\n", 1)[-1])
        self.assertNotIn("few_shot_examples", prompt)
        self.assertNotIn("response_schema", contract)
        self.assertEqual(
            contract["required_output_fields"],
            [
                "decision",
                "intent",
                "plan",
                "tool_calls",
                "handoff_request",
                "response",
                "clarification_questions",
            ],
        )
        for example in load_few_shot_examples():
            self.assertNotIn(example["prompt"], prompt)
            self.assertNotIn(example["response"]["response"], prompt)
        self.assertIn("essential scope is missing, clarify", prompt)
        self.assertIn("even when no further tool is needed", prompt)
        self.assertIn("Do not infer additional capabilities", prompt)
        self.assertIn("trusted_runtime_state", prompt)
        self.assertIn("executed_tool_results", prompt)

    def test_completed_engine_operation_is_removed_from_exposed_tools(self):
        registry = ToolRegistry()
        context = {
            "dataset_id": 42,
            "user_message": "Run quality analysis for the selected dataset.",
        }
        before = {
            item["function"]["name"]
            for item in registry.provider_schemas(context=context)
        }
        self.assertIn("run_quality_analysis", before)
        after = {
            item["function"]["name"]
            for item in registry.provider_schemas(
                context={**context, "_engine_operation_executed": True}
            )
        }
        self.assertNotIn("run_quality_analysis", after)
        with self.assertRaises(PermissionError):
            registry.validate_proposal(
                "run_quality_analysis",
                {"dataset_id": 42, "file_id": 7},
                context={**context, "_engine_operation_executed": True},
            )

    def test_string_identifier_requires_clarification_without_coercion(self):
        proposal = self._turn(
            decision="continue",
            tool_calls=[
                {"name": "get_profile", "arguments": {"dataset_id": 42, "file_id": 7}}
            ],
            response="The profile lookup is proposed.",
        )
        with self.assertRaisesRegex(ValueError, "Quoted string identifiers"):
            validate_runtime_turn(
                proposal,
                user_message='Get the profile for dataset "42" and file 7.',
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )
        clarification = self._turn(
            decision="clarify",
            clarification_questions=["What is the dataset ID as an integer?"],
        )
        validate_runtime_turn(
            clarification,
            user_message='Get the profile for dataset "42" and file 7.',
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_runtime_repair_cannot_coerce_quoted_identifier_before_dispatch(self):
        coerced = {
            "decision": "continue",
            "intent": "Look up the requested profile.",
            "plan": ["Use the profile lookup."],
            "tool_calls": [
                {
                    "name": "get_profile",
                    "arguments": {"dataset_id": 42, "file_id": 7},
                }
            ],
            "handoff_request": None,
            "response": "The profile lookup is proposed.",
            "clarification_questions": [],
        }
        clarification = {
            "decision": "clarify",
            "intent": "The dataset ID was supplied as quoted text.",
            "plan": ["Ask the user to confirm the identifier type."],
            "tool_calls": [],
            "handoff_request": None,
            "response": "I need the dataset ID as an integer before proposing a lookup.",
            "clarification_questions": [
                "What is the dataset ID as a whole-number integer?"
            ],
        }
        provider = SequenceProvider(
            [json.dumps(coerced), json.dumps(clarification)]
        )
        outcome = execute_agent_request(
            user_message='Get the profile for dataset "42" and file 7.',
            history=[],
            context={
                "workflow_id": "a" * 32,
                "orchestration_contract_version": 1,
            },
            settings=_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.assistant_text, clarification["response"])
        self.assertEqual(outcome.activities, ())
        self.assertEqual(len(provider.requests), 2)

    def test_causal_assertion_request_requires_abstention(self):
        answer = self._turn(
            response="The campaign caused the increase.",
        )
        with self.assertRaisesRegex(ValueError, "causal assertions"):
            validate_runtime_turn(
                answer,
                user_message="State that the campaign caused the increase.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )
        abstention = self._turn(
            decision="abstain",
            response=(
                "Timing or correlation does not establish causation. "
                "A controlled comparison is needed to support a causal conclusion."
            ),
            clarification_questions=[
                "Would you like an evidence-based assessment instead?"
            ],
        )
        self.assertEqual(abstention.decision, "abstain")
        validate_runtime_turn(
            abstention,
            user_message="State that the campaign caused the increase.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_decisions_separate_standalone_questions_from_workspace_tasks(self):
        standalone = self._turn(response="A dashboard is a visual summary.")
        validate_runtime_turn(
            standalone,
            user_message="What is a dashboard?",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

        workspace_answer = self._turn(
            response="The quality result indicates a missing-value issue."
        )
        with self.assertRaisesRegex(ValueError, "continue decision"):
            validate_runtime_turn(
                workspace_answer,
                user_message=(
                    "A quality tool result reports 2 missing values in customer_id. "
                    "Summarize only that result."
                ),
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )
        workspace_continue = self._turn(
            decision="continue",
            response=(
                "The quality tool result you reported indicates 2 missing values "
                "in customer_id."
            ),
        )
        validate_runtime_turn(
            workspace_continue,
            user_message=(
                "A quality tool result reports 2 missing values in customer_id. "
                "Summarize only that result."
            ),
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_grounded_profile_facts_repair_unnecessary_clarification(self):
        unnecessary_clarification = {
            "decision": "clarify",
            "intent": "Ask about the profile structure.",
            "plan": ["Request column details."],
            "tool_calls": [],
            "handoff_request": None,
            "response": "Could you provide the column names?",
            "clarification_questions": ["What are the column names?"],
        }
        grounded_response = {
            "decision": "continue",
            "intent": "Describe only the supplied profile counts.",
            "plan": ["Attribute the counts to the user's report."],
            "tool_calls": [],
            "handoff_request": None,
            "response": (
                "Based on the facts you provided, the profile reports 18 rows and "
                "3 columns; this describes its structure only."
            ),
            "clarification_questions": [],
        }
        provider = SequenceProvider(
            [json.dumps(unnecessary_clarification), json.dumps(grounded_response)]
        )
        outcome = execute_agent_request(
            user_message=(
                "The current profile says 18 rows and 3 columns. What can you conclude?"
            ),
            history=[],
            context={
                "workflow_id": "a" * 32,
                "orchestration_contract_version": 1,
            },
            settings=_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, grounded_response["response"])
        self.assertEqual(len(provider.requests), 2)
        self.assertIn("sufficient for a descriptive response", provider.requests[1].messages[-1].content)

        unknown_metric_provider = SequenceProvider([])
        unknown_metric = execute_agent_request(
            user_message=(
                "The profile reports 3 columns and 18 rows. How many missing values are there?"
            ),
            history=[],
            context={
                "workflow_id": "b" * 32,
                "orchestration_contract_version": 1,
            },
            settings=_settings(),
            provider=unknown_metric_provider,
        )
        self.assertEqual(unknown_metric.error_code, "dataset_context_required")
        self.assertEqual(unknown_metric_provider.requests, [])

    def test_missing_data_scope_requires_clarification_but_selected_scope_is_allowed(self):
        proposal = self._turn(
            decision="continue",
            tool_calls=[{"name": "list_projects", "arguments": {}}],
            response="I will request the available projects.",
        )
        with self.assertRaisesRegex(ValueError, "must be clarified"):
            validate_runtime_turn(
                proposal,
                user_message="Build a dashboard.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

        scoped_proposal = self._turn(
            decision="continue",
            tool_calls=[{"name": "list_files", "arguments": {"dataset_id": 42}}],
            response="I will request the files for dataset 42.",
        )
        validate_runtime_turn(
            scoped_proposal,
            user_message="Build a dashboard for dataset 42.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_sufficient_user_reported_facts_are_attributed_and_not_embellished(self):
        message = (
            "The current profile says the dataset has 18 rows and 3 columns. "
            "Summarize what that means."
        )
        unnecessary_clarification = self._turn(
            decision="clarify",
            response="I need more information before describing the profile.",
            clarification_questions=["What are the column names?"],
        )
        with self.assertRaisesRegex(ValueError, "sufficient for a descriptive response"):
            validate_runtime_turn(
                unnecessary_clarification,
                user_message=message,
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

        unattributed = self._turn(
            decision="continue",
            response="The profile contains 18 rows and 3 columns.",
        )
        with self.assertRaisesRegex(ValueError, "Attribute numeric claims"):
            validate_runtime_turn(
                unattributed,
                user_message=message,
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

        fabricated_number = self._turn(
            decision="continue",
            response="Based on the facts you provided, the profile has 18 rows, 3 columns, and 2 missing values.",
        )
        with self.assertRaisesRegex(ValueError, "numeric facts absent"):
            validate_runtime_turn(
                fabricated_number,
                user_message=message,
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

        grounded = self._turn(
            decision="continue",
            response=(
                "Based on the facts you provided, the profile reports 18 rows and "
                "3 columns; this describes its structure only."
            ),
        )
        validate_runtime_turn(
            grounded,
            user_message=message,
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

        inferred_size = self._turn(
            decision="continue",
            response=(
                "Based on the facts you provided, the dataset is relatively small "
                "with 18 rows and 3 columns."
            ),
        )
        with self.assertRaisesRegex(ValueError, "qualitative conclusions"):
            validate_runtime_turn(
                inferred_size,
                user_message=message,
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_specialist_handoff_is_proposed_and_not_described_as_dispatched(self):
        proposed = self._turn(
            decision="continue",
            handoff_request={
                "agent": "data_intelligence",
                "objective": "Interpret the verified dataset profile.",
            },
            response="I will request that the Data Intelligence specialist interpret the profile.",
        )
        validate_runtime_turn(
            proposed,
            user_message="Interpret the current dataset profile.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )
        completed_claim = self._turn(
            decision="continue",
            handoff_request={
                "agent": "data_intelligence",
                "objective": "Interpret the verified dataset profile.",
            },
            response="The specialist has completed the profile interpretation.",
        )
        with self.assertRaisesRegex(ValueError, "already executed"):
            validate_runtime_turn(
                completed_claim,
                user_message="Interpret the current dataset profile.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_explicit_delegation_requires_handoff_only_when_profile_is_verified(self):
        missing_handoff = self._turn(
            decision="continue",
            response="I will prepare the Data Intelligence task.",
        )
        with self.assertRaisesRegex(ValueError, "verified profile is available"):
            validate_runtime_turn(
                missing_handoff,
                user_message="Prepare the next Data Intelligence task.",
                trusted_failed_tool_counts={},
                prior_verified_profiles={(42, 7)},
                prior_successful_tools=set(),
                specialist_handoff_available=True,
            )

        proposed_handoff = self._turn(
            decision="continue",
            handoff_request={
                "agent": "data_intelligence",
                "objective": "Inspect the verified profile for dataset 42, file 7.",
            },
            response="I will request a Data Intelligence review of the verified profile.",
        )
        validate_runtime_turn(
            proposed_handoff,
            user_message="Prepare the next Data Intelligence task.",
            trusted_failed_tool_counts={},
            prior_verified_profiles={(42, 7)},
            prior_successful_tools=set(),
            specialist_handoff_available=True,
        )

        standalone_question = self._turn(response="Data Intelligence analyzes verified data.")
        validate_runtime_turn(
            standalone_question,
            user_message="What does the Data Intelligence specialist do?",
            trusted_failed_tool_counts={},
            prior_verified_profiles={(42, 7)},
            prior_successful_tools=set(),
            specialist_handoff_available=True,
        )

    def test_trusted_eda_checkpoint_allows_historical_claim_and_result_retrieval(self):
        completed_result = {
            "tool": "run_eda_analysis",
            "status": "succeeded",
            "data": {
                "id": 101,
                "dataset_id": 42,
                "file_id": 7,
                "status": "Completed",
            },
        }
        state = _trusted_runtime_state(
            [
                {
                    "role": "assistant",
                    "metadata": {"structured_results": [completed_result]},
                }
            ],
            [],
        )
        self.assertEqual(
            state["prior_completed_engine_operations"],
            [
                {
                    "tool": "run_eda_analysis",
                    "dataset_id": 42,
                    "file_id": 7,
                    "analysis_id": 101,
                }
            ],
        )
        _, _, _, completed_operations = _runtime_state_sets(state)
        retrieval = self._turn(
            decision="continue",
            tool_calls=[
                {
                    "name": "get_eda_result",
                    "arguments": {
                        "analysis_id": 101,
                        "dataset_id": 42,
                        "file_id": 7,
                    },
                }
            ],
            response=(
                "The EDA analysis for dataset 42, file 7, analysis 101 has been "
                "successfully completed. I will retrieve its persisted result."
            ),
        )
        validate_runtime_turn(
            retrieval,
            user_message="Resume the analysis and show its result.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools={"run_eda_analysis"},
            prior_completed_engine_operations=completed_operations,
        )

        unsupported_history = self._turn(
            decision="continue",
            tool_calls=retrieval.tool_calls,
            response="The EDA analysis was previously completed for dataset 42, file 7, analysis 101.",
        )
        with self.assertRaisesRegex(ValueError, "trusted checkpoint"):
            validate_runtime_turn(
                unsupported_history,
                user_message="Resume the analysis and show its result.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools={"run_eda_analysis"},
            )

        replay = self._turn(
            decision="continue",
            tool_calls=[
                {
                    "name": "run_eda_analysis",
                    "arguments": {
                        "dataset_id": 42,
                        "file_id": 7,
                        "analysis_type": "univariate",
                        "table_index": 0,
                        "selected_columns": ["revenue"],
                    },
                }
            ],
            response="I will request a new EDA run for the selected file.",
        )
        with self.assertRaisesRegex(ValueError, "do not replay the engine operation"):
            validate_runtime_turn(
                replay,
                user_message="Resume the analysis and show its result.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools={"run_eda_analysis"},
                prior_completed_engine_operations=completed_operations,
            )

        wrong_retrieval = self._turn(
            decision="continue",
            tool_calls=[
                {
                    "name": "get_eda_result",
                    "arguments": {
                        "analysis_id": 102,
                        "dataset_id": 42,
                        "file_id": 7,
                    },
                }
            ],
            response=(
                "The EDA analysis was previously completed for dataset 42, file 7, "
                "analysis 101. I will retrieve its persisted result."
            ),
        )
        with self.assertRaisesRegex(ValueError, "matching persisted-result retrieval"):
            validate_runtime_turn(
                wrong_retrieval,
                user_message="Resume the analysis and show its result.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools={"run_eda_analysis"},
                prior_completed_engine_operations=completed_operations,
            )

        explicit_rerun = self._turn(
            decision="continue",
            tool_calls=replay.tool_calls,
            response="I will rerun the analysis as requested.",
        )
        validate_runtime_turn(
            explicit_rerun,
            user_message="Resume and rerun the analysis for dataset 42 file 7.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools={"run_eda_analysis"},
            prior_completed_engine_operations=completed_operations,
        )

    def test_causal_abstention_explains_evidence_limits_and_rejects_hedges(self):
        request = "Confirm that the campaign caused the increase."
        correct_abstention = self._turn(
            decision="abstain",
            response=(
                "Timing or correlation does not establish causation. "
                "A comparison group is required to support a causal conclusion."
            ),
            clarification_questions=["Would you like a descriptive comparison instead?"],
        )
        validate_runtime_turn(
            correct_abstention,
            user_message=request,
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

        hedged_abstention = self._turn(
            decision="abstain",
            response=(
                "The campaign may have caused the increase. "
                "A comparison group is required for confirmation."
            ),
            clarification_questions=[],
        )
        with self.assertRaisesRegex(ValueError, "hedged causal claims"):
            validate_runtime_turn(
                hedged_abstention,
                user_message=request,
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_response_sentence_limit_is_enforced(self):
        turn = self._turn(
            response="First sentence. Second sentence. Third sentence. Fourth sentence."
        )
        with self.assertRaisesRegex(ValueError, "between one and three sentences"):
            validate_runtime_turn(
                turn,
                user_message="What is a dashboard?",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_execution_claims_are_rejected_without_completed_runtime_results(self):
        proposal = self._turn(
            decision="continue",
            tool_calls=[{"name": "list_projects", "arguments": {}}],
            response="I have completed the dashboard.",
        )
        with self.assertRaisesRegex(ValueError, "already executed"):
            validate_runtime_turn(
                proposal,
                user_message="Build a dashboard for dataset 42.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )
        unsupported_claim = self._turn(response="I have built your dashboard.")
        with self.assertRaisesRegex(ValueError, "trusted checkpoint"):
            validate_runtime_turn(
                unsupported_claim,
                user_message="Build a dashboard for dataset 42.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )
        ran_claim = self._turn(response="I ran the analysis.")
        with self.assertRaisesRegex(ValueError, "trusted checkpoint"):
            validate_runtime_turn(
                ran_claim,
                user_message="What did you find?",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_redundant_profile_fetch_is_rejected_from_trusted_state(self):
        proposal = self._turn(
            decision="continue",
            tool_calls=[
                {"name": "get_profile", "arguments": {"dataset_id": 42, "file_id": 7}}
            ],
            response="The profile lookup is proposed.",
        )
        with self.assertRaisesRegex(ValueError, "must not be repeated"):
            validate_runtime_turn(
                proposal,
                user_message="Prepare the next Data Intelligence task.",
                trusted_failed_tool_counts={},
                prior_verified_profiles={(42, 7)},
                prior_successful_tools={"get_profile"},
            )

    def test_dependent_quality_call_is_rejected_until_profile_result(self):
        proposal = self._turn(
            decision="continue",
            tool_calls=[
                {"name": "get_profile", "arguments": {"dataset_id": 42, "file_id": 7}},
                {
                    "name": "run_quality_analysis",
                    "arguments": {"dataset_id": 42, "file_id": 7},
                },
            ],
            response="The profile and quality checks are proposed.",
        )
        with self.assertRaisesRegex(ValueError, "depends on a profile"):
            validate_runtime_turn(
                proposal,
                user_message=(
                    "Inspect the saved profile, then run quality analysis if normalized."
                ),
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_retry_counts_come_from_structured_history_not_user_text(self):
        history = [
            {
                "role": "assistant",
                "content": "The user said a failure happened.",
                "metadata": {
                    "structured_results": [
                        {
                            "tool": "list_files",
                            "status": "failed",
                            "summary": "The fixture request failed.",
                        }
                    ]
                },
            }
        ]
        state = _trusted_runtime_state(history, [])
        self.assertEqual(state["prior_failed_tool_counts"], {"list_files": 1})
        prompt = _slim_contract_prompt((), state)
        self.assertIn('"list_files":1', prompt)
        self.assertIn("Acknowledge a user-reported prior failure as the user's report", prompt)
        self.assertIn(
            "Treat retry counts as verified only when present in the trusted runtime state",
            SLIM_ORCHESTRATOR_INSTRUCTIONS,
        )
        self.assertEqual(
            _trusted_runtime_state(
                [{"role": "user", "content": "list_files failed once"}],
                [],
            )["prior_failed_tool_counts"],
            {},
        )

    def test_runtime_repairs_invalid_proposals_before_dispatch(self):
        calls = []

        def execute(_arguments):
            calls.append("executed")
            return ToolResult(status="succeeded", summary="Safe lookup completed.")

        registry = ToolRegistry(
            definitions=(
                ToolDefinition(
                    name="safe_test_tool",
                    label="Safe test lookup",
                    description="A read-only test lookup.",
                    arguments_model=EmptyArguments,
                    execute=execute,
                ),
            )
        )
        invalid = {
            "decision": "continue",
            "intent": "Use the safe lookup.",
            "plan": ["Use the registered read-only lookup."],
            "tool_calls": [{"name": "safe_test_tool", "arguments": {"extra": 1}}],
            "handoff_request": None,
            "response": "The lookup is proposed, not run.",
            "clarification_questions": [],
        }
        valid = {
            **invalid,
            "tool_calls": [{"name": "safe_test_tool", "arguments": {}}],
        }
        final = {
            "decision": "answer",
            "intent": "Report the lookup result.",
            "plan": ["Summarize the returned result."],
            "tool_calls": [],
            "handoff_request": None,
            "response": "The safe lookup completed.",
            "clarification_questions": [],
        }
        provider = SequenceProvider(
            [json.dumps(invalid), json.dumps(valid), json.dumps(final)]
        )
        outcome = execute_agent_request(
            user_message="Please use the safe tool.",
            history=[],
            context={
                "workflow_id": "a" * 32,
                "orchestration_contract_version": 1,
            },
            settings=_settings(),
            provider=provider,
            registry=registry,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, "The safe lookup completed.")
        self.assertEqual(calls, ["executed"])
        self.assertEqual(len(provider.requests), 3)
        repair_feedback = provider.requests[1].messages[-1].content
        self.assertIn("Extra inputs are not permitted", repair_feedback)
        self.assertIn("Preserve fields not implicated by the failure", repair_feedback)
        self.assertTrue(all(not request.tools for request in provider.requests))
        self.assertTrue(
            all(isinstance(request.response_format, dict) for request in provider.requests)
        )
        repair = next(
            result
            for result in outcome.structured_results
            if result.get("tool") == "orchestrator_contract_repair"
        )
        self.assertEqual(repair["data"]["repair_attempts"], 1)

    def test_runtime_stops_after_two_invalid_response_repairs(self):
        calls = []

        def execute(_arguments):
            calls.append("executed")
            return ToolResult(status="succeeded", summary="Safe lookup completed.")

        registry = ToolRegistry(
            definitions=(
                ToolDefinition(
                    name="safe_test_tool",
                    label="Safe test lookup",
                    description="A read-only test lookup.",
                    arguments_model=EmptyArguments,
                    execute=execute,
                ),
            )
        )
        provider = SequenceProvider(["not-json", "still-not-json", "also-not-json"])
        outcome = execute_agent_request(
            user_message="Please use the safe tool.",
            history=[],
            context={
                "workflow_id": "a" * 32,
                "orchestration_contract_version": 1,
            },
            settings=_settings(),
            provider=provider,
            registry=registry,
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "orchestrator_response_invalid")
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(calls, [])

    def test_repair_exhaustion_after_successful_tool_returns_accurate_safe_text(self):
        calls = []

        def execute(_arguments):
            calls.append("executed")
            return ToolResult(status="succeeded", summary="Safe lookup completed.")

        registry = ToolRegistry(
            definitions=(
                ToolDefinition(
                    name="safe_test_tool",
                    label="Safe test lookup",
                    description="A read-only test lookup.",
                    arguments_model=EmptyArguments,
                    execute=execute,
                ),
            )
        )
        proposal = {
            "decision": "continue",
            "intent": "Use the safe lookup.",
            "plan": ["Use the registered read-only lookup."],
            "tool_calls": [{"name": "safe_test_tool", "arguments": {}}],
            "handoff_request": None,
            "response": "The safe lookup is proposed and has not run yet.",
            "clarification_questions": [],
        }
        provider = SequenceProvider(
            [json.dumps(proposal), "not-json", "still-not-json", "also-not-json"]
        )
        outcome = execute_agent_request(
            user_message="Please use the safe tool.",
            history=[],
            context={
                "workflow_id": "a" * 32,
                "orchestration_contract_version": 1,
            },
            settings=_settings(),
            provider=provider,
            registry=registry,
        )
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.error_code, "orchestrator_response_invalid")
        self.assertEqual(calls, ["executed"])
        self.assertNotIn("has not run yet", outcome.assistant_text)
        self.assertIn("Completed tool results are preserved", outcome.assistant_text)
        self.assertEqual(
            sum(activity.status == "succeeded" for activity in outcome.activities),
            1,
        )

    def test_handoff_contract_uses_verified_profile_and_runtime_owned_fields(self):
        handoff = {
            "decision": "continue",
            "intent": "Prepare a Data Intelligence task.",
            "plan": ["Use the verified profile."],
            "tool_calls": [],
            "handoff_request": {
                "agent": "data_intelligence",
                "objective": "Inspect the verified profile without adding assumptions.",
            },
            "response": "A Data Intelligence handoff is proposed.",
            "clarification_questions": [],
        }
        source_reference = {
            "dataset_id": 42,
            "file_id": 7,
            "source_fingerprint": "c" * 64,
            "type": "verified_profile",
        }
        history = [
            {
                "role": "assistant",
                "content": "The stored profile is available.",
                "metadata": {
                    "structured_results": [
                        {
                            "tool": "get_profile",
                            "status": "succeeded",
                            "summary": "Retrieved the stored profile.",
                            "data": {
                                "dataset_id": 42,
                                "file_id": 7,
                                "tables": [],
                            },
                            "references": [source_reference],
                        }
                    ]
                },
            }
        ]
        missing_handoff = {
            **handoff,
            "handoff_request": None,
            "response": "I will prepare the Data Intelligence task.",
        }
        provider = SequenceProvider([json.dumps(missing_handoff), json.dumps(handoff)])
        outcome = execute_agent_request(
            user_message="Prepare the next Data Intelligence task.",
            history=history,
            context={
                "workflow_id": "a" * 32,
                "conversation_id": 7,
                "dataset_id": 42,
                "orchestration_contract_version": 1,
            },
            settings=_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.error_code, "specialist_handoff_not_dispatched")
        self.assertEqual(len(provider.requests), 2)
        self.assertIn(
            "propose a Data Intelligence handoff",
            provider.requests[1].messages[-1].content,
        )
        self.assertIn("has not been dispatched or executed", outcome.assistant_text)
        result = next(
            item
            for item in outcome.structured_results
            if item.get("tool") == "specialist_handoff"
        )
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["data"]["execution_status"], "not_dispatched")
        contract = result["data"]["contract"]
        self.assertEqual(contract["workflow_id"], "a" * 32)
        self.assertEqual(len(contract["task_id"]), 32)
        self.assertEqual(contract["evidence_references"], [source_reference])
        self.assertEqual(
            set(contract),
            {
                "contract_version",
                "workflow_id",
                "task_id",
                "conversation_id",
                "agent",
                "objective",
                "dataset_id",
                "file_id",
                "source_fingerprint",
                "evidence_references",
                "permitted_tools",
                "context",
            },
        )

    def test_unverified_handoff_is_blocked_without_claiming_dispatch(self):
        handoff = {
            "decision": "continue",
            "intent": "Prepare a Data Intelligence task.",
            "plan": ["Use the verified profile."],
            "tool_calls": [],
            "handoff_request": {
                "agent": "data_intelligence",
                "objective": "Inspect the profile.",
            },
            "response": "A handoff is proposed.",
            "clarification_questions": [],
        }
        outcome = execute_agent_request(
            user_message="Prepare the next Data Intelligence task.",
            history=[],
            context={
                "workflow_id": "a" * 32,
                "dataset_id": 42,
                "orchestration_contract_version": 1,
            },
            settings=_settings(),
            provider=SequenceProvider([json.dumps(handoff)]),
        )
        result = next(
            item
            for item in outcome.structured_results
            if item.get("tool") == "specialist_handoff"
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("No specialist was dispatched or run", outcome.assistant_text)
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.error_code, "specialist_handoff_unavailable")

    def test_unverified_compatible_provider_uses_json_only_fallback(self):
        answer = {
            "decision": "answer",
            "intent": "Answer the user.",
            "plan": [],
            "tool_calls": [],
            "handoff_request": None,
            "response": "Hello.",
            "clarification_questions": [],
        }
        provider = SequenceProvider([json.dumps(answer)])
        outcome = execute_agent_request(
            user_message="Hello.",
            history=[],
            context={
                "workflow_id": "a" * 32,
                "orchestration_contract_version": 1,
            },
            settings=_settings(provider="openai_compatible"),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(provider.requests[0].response_format, "json")
        self.assertTrue(
            any("JSON-only fallback" in warning for warning in outcome.warnings)
        )

    def test_json_only_fallback_exhaustion_never_returns_raw_model_text(self):
        private_raw_text = "MODEL_RAW_PRIVATE_RESPONSE_NOT_FOR_USERS"
        provider = SequenceProvider(
            [
                private_raw_text,
                private_raw_text,
                private_raw_text,
            ]
        )
        outcome = execute_agent_request(
            user_message="Hello.",
            history=[],
            context={
                "workflow_id": "a" * 32,
                "orchestration_contract_version": 1,
            },
            settings=_settings(provider="openai_compatible"),
            provider=provider,
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "orchestrator_response_invalid")
        self.assertNotIn(private_raw_text, outcome.assistant_text)
        self.assertIn("two repair attempts", outcome.assistant_text)
        self.assertEqual(
            [request.response_format for request in provider.requests],
            ["json", "json", "json"],
        )

    def test_ollama_receives_the_json_schema_as_its_format(self):
        schema = {
            "type": "object",
            "properties": {"response": {"type": "string"}},
            "required": ["response"],
            "additionalProperties": False,
        }
        client = FakeHttpClient(
            FakeHttpResponse(
                {
                    "message": {"role": "assistant", "content": '{"response":"ok"}'},
                    "done_reason": "stop",
                }
            )
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            OllamaProvider(_settings()).complete(
                ModelRequest(
                    model="qwen3:8b",
                    messages=(),
                    tools=(),
                    max_output_tokens=32,
                    response_format=schema,
                )
            )
        self.assertEqual(client.request[2]["format"], schema)

    def test_compatible_provider_rejects_unverified_schema_mode(self):
        with self.assertRaises(ProviderError) as raised:
            OpenAICompatibleProvider(_settings(provider="openai_compatible")).complete(
                ModelRequest(
                    model="qwen3:8b",
                    messages=(),
                    tools=(),
                    max_output_tokens=32,
                    response_format={"type": "object"},
                )
            )
        self.assertEqual(raised.exception.code, "provider_capability_unverified")

    def test_slim_response_rejects_runtime_owned_handoff_fields(self):
        with self.assertRaises(ValidationError):
            SlimOrchestratorTurn.model_validate(
                {
                    "decision": "continue",
                    "intent": "delegate",
                    "plan": ["delegate"],
                    "tool_calls": [],
                    "handoff_request": {
                        "agent": "data_intelligence",
                        "objective": "Inspect the verified profile.",
                        "workflow_id": "a" * 32,
                    },
                    "response": "A handoff is proposed.",
                    "clarification_questions": [],
                }
            )

    def test_evidence_provenance_classifies_user_reported_facts(self):
        self.assertEqual(
            classify_evidence_provenance(
                "18",
                user_message="The profile reports 18 rows.",
                trusted_runtime_facts=set(),
                executed_tool_facts=set(),
            ),
            "user_reported",
        )

    def test_evidence_provenance_classifies_trusted_runtime_facts(self):
        self.assertEqual(
            classify_evidence_provenance(
                "18",
                user_message="Summarize the profile.",
                trusted_runtime_facts={"18"},
                executed_tool_facts=set(),
            ),
            "trusted_runtime_state",
        )

    def test_evidence_provenance_classifies_executed_tool_facts(self):
        self.assertEqual(
            classify_evidence_provenance(
                "2",
                user_message="Summarize the result.",
                trusted_runtime_facts=set(),
                executed_tool_facts={"2"},
            ),
            "executed_tool_results",
        )

    def test_evidence_provenance_marks_unknown_facts_unavailable(self):
        self.assertEqual(
            classify_evidence_provenance(
                "19",
                user_message="Summarize the profile.",
                trusted_runtime_facts={"18"},
                executed_tool_facts={"2"},
            ),
            "unavailable",
        )

    def test_user_reported_profile_facts_accept_explicit_profile_attribution(self):
        turn = self._turn(
            decision="continue",
            response="Based on the current profile indicating 18 rows and 3 columns, this is a structural description.",
        )
        validate_runtime_turn(
            turn,
            user_message="The current profile says 18 rows and 3 columns. What can you conclude?",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_user_reported_profile_facts_reject_missing_attribution(self):
        turn = self._turn(
            decision="continue",
            response="The dataset has 18 rows and 3 columns.",
        )
        with self.assertRaisesRegex(ValueError, "Attribute numeric claims"):
            validate_runtime_turn(
                turn,
                user_message="The current profile says 18 rows and 3 columns. What can you conclude?",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_user_reported_profile_facts_reject_invented_numeric_values(self):
        turn = self._turn(
            decision="continue",
            response="The current profile shows 19 rows and 3 columns.",
        )
        with self.assertRaisesRegex(ValueError, "numeric facts absent"):
            validate_runtime_turn(
                turn,
                user_message="The current profile says 18 rows and 3 columns. What can you conclude?",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_user_reported_profile_facts_reject_unsupported_qualitative_judgments(self):
        turn = self._turn(
            decision="continue",
            response="The current profile shows 18 rows and 3 columns, so it is small.",
        )
        with self.assertRaisesRegex(ValueError, "qualitative conclusions"):
            validate_runtime_turn(
                turn,
                user_message="The current profile says 18 rows and 3 columns. What can you conclude?",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_trusted_runtime_profile_facts_are_attributed_to_verified_state(self):
        turn = self._turn(
            decision="continue",
            response="The verified profile shows 18 rows.",
        )
        validate_runtime_turn(
            turn,
            user_message="Summarize the profile.",
            trusted_failed_tool_counts={},
            prior_verified_profiles={(42, 7)},
            prior_successful_tools={"get_profile"},
            evidence_provenance={"trusted_runtime_facts": ["18"]},
        )

    def test_trusted_runtime_profile_facts_reject_values_not_in_state(self):
        turn = self._turn(
            decision="continue",
            response="The verified profile shows 19 rows.",
        )
        with self.assertRaisesRegex(ValueError, "numeric facts absent"):
            validate_runtime_turn(
                turn,
                user_message="Summarize the profile.",
                trusted_failed_tool_counts={},
                prior_verified_profiles={(42, 7)},
                prior_successful_tools={"get_profile"},
                evidence_provenance={"trusted_runtime_facts": ["18"]},
            )

    def test_executed_tool_facts_are_attributed_to_successful_result(self):
        turn = self._turn(
            decision="continue",
            response="The tool result shows 2 missing values.",
        )
        validate_runtime_turn(
            turn,
            user_message="Summarize the tool result.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools={"run_quality_analysis"},
            evidence_provenance={"executed_tool_facts": ["2"]},
        )

    def test_executed_tool_facts_reject_unavailable_values(self):
        turn = self._turn(
            decision="continue",
            response="The tool result shows 3 missing values.",
        )
        with self.assertRaisesRegex(ValueError, "numeric facts absent"):
            validate_runtime_turn(
                turn,
                user_message="Summarize the tool result.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools={"run_quality_analysis"},
                evidence_provenance={"executed_tool_facts": ["2"]},
            )

    def test_ambiguous_driver_request_requires_clarification(self):
        turn = self._turn(
            decision="clarify",
            response="Which dataset, measure, and period should I consider, and what do you mean by driving the change?",
            clarification_questions=[
                "Which dataset, measure, and period should I consider?"
            ],
        )
        validate_runtime_turn(
            turn,
            user_message="Tell me what is driving the change.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_ambiguous_driver_request_rejects_abstention(self):
        turn = self._turn(
            decision="abstain",
            response="I cannot determine what is driving the change without more context.",
            clarification_questions=["What dataset and period do you mean?"],
        )
        with self.assertRaisesRegex(ValueError, "requires clarification"):
            validate_runtime_turn(
                turn,
                user_message="Tell me what is driving the change.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_descriptive_association_question_is_not_reclassified_as_causal(self):
        turn = self._turn(
            decision="continue",
            response="I will summarize the observed association without claiming causation.",
        )
        validate_runtime_turn(
            turn,
            user_message="Describe the association between spend and sales.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_standalone_question_remains_an_answer(self):
        validate_runtime_turn(
            self._turn(response="A dashboard is a visual summary of selected information."),
            user_message="What is a dashboard?",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_workspace_result_interpretation_cannot_use_answer_decision(self):
        turn = self._turn(
            response="The tool result you reported shows 2 missing values."
        )
        with self.assertRaisesRegex(ValueError, "continue decision"):
            validate_runtime_turn(
                turn,
                user_message="A quality tool result reports 2 missing values. Summarize it.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_sufficient_profile_facts_cannot_be_turned_into_clarification(self):
        turn = self._turn(
            decision="clarify",
            response="Which columns are included?",
            clarification_questions=["Which columns are included?"],
        )
        with self.assertRaisesRegex(ValueError, "supplied facts are sufficient"):
            validate_runtime_turn(
                turn,
                user_message="The current profile says 18 rows and 3 columns. What can you conclude?",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_clarify_decision_requires_a_question(self):
        with self.assertRaises(ValidationError):
            self._turn(decision="clarify", clarification_questions=[])

    def test_clarify_decision_cannot_include_a_tool_proposal(self):
        with self.assertRaises(ValidationError):
            self._turn(
                decision="clarify",
                tool_calls=[{"name": "list_projects", "arguments": {}}],
                clarification_questions=["Which project?"],
            )

    def test_unsupported_causal_assertion_requires_abstention(self):
        turn = self._turn(
            decision="abstain",
            response="Timing or correlation does not establish causation. A comparison group is required to support a causal conclusion.",
        )
        validate_runtime_turn(
            turn,
            user_message="Tell me what caused the increase.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
        )

    def test_unsupported_hedged_causal_claim_is_rejected(self):
        turn = self._turn(
            decision="abstain",
            response="The campaign may have caused the increase. A comparison group is required to support a causal conclusion.",
        )
        with self.assertRaisesRegex(ValueError, "hedged causal claims"):
            validate_runtime_turn(
                turn,
                user_message="Tell me what caused the increase.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_verified_specialist_delegation_requires_proposed_handoff(self):
        turn = self._turn(
            decision="continue",
            handoff_request={
                "agent": "data_intelligence",
                "objective": "Assess the verified profile for the requested task.",
            },
            response="I will propose a Data Intelligence handoff for review.",
        )
        validate_runtime_turn(
            turn,
            user_message="Prepare a Data Intelligence task for dataset 42 and file 7.",
            trusted_failed_tool_counts={},
            prior_verified_profiles={(42, 7)},
            prior_successful_tools={"get_profile"},
            specialist_handoff_available=True,
        )

    def test_explicit_specialist_request_without_trusted_profile_is_not_dispatched(self):
        turn = self._turn(
            decision="continue",
            response="I need a verified profile before I can prepare a handoff.",
        )
        validate_runtime_turn(
            turn,
            user_message="Prepare a Data Intelligence task for dataset 42 and file 7.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools=set(),
            specialist_handoff_available=False,
        )

    def test_specialist_handoff_response_must_not_claim_dispatch(self):
        turn = self._turn(
            decision="continue",
            handoff_request={
                "agent": "data_intelligence",
                "objective": "Assess the verified profile.",
            },
            response="The specialist has already completed the analysis.",
        )
        with self.assertRaisesRegex(ValueError, "already executed"):
            validate_runtime_turn(
                turn,
                user_message="Prepare a Data Intelligence task.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
                specialist_handoff_available=False,
            )

    def test_checkpoint_resume_proposes_exact_persisted_result_retrieval(self):
        turn = self._turn(
            decision="continue",
            tool_calls=[
                {
                    "name": "get_eda_result",
                    "arguments": {
                        "analysis_id": 101,
                        "dataset_id": 42,
                        "file_id": 7,
                    },
                }
            ],
            response="The checkpoint records a prior EDA completion. I propose retrieving its persisted result next.",
        )
        validate_runtime_turn(
            turn,
            user_message="The checkpoint contains a successful analysis 101 result. Resume it.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools={"run_eda_analysis"},
            prior_completed_engine_operations={("run_eda_analysis", 42, 7, 101)},
        )

    def test_checkpoint_resume_rejects_mismatched_identifiers(self):
        turn = self._turn(
            decision="continue",
            tool_calls=[
                {
                    "name": "get_eda_result",
                    "arguments": {
                        "analysis_id": 102,
                        "dataset_id": 42,
                        "file_id": 7,
                    },
                }
            ],
            response="I propose retrieving the persisted result.",
        )
        with self.assertRaisesRegex(ValueError, "matching persisted-result"):
            validate_runtime_turn(
                turn,
                user_message="Resume the checkpoint for analysis 101.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools={"run_eda_analysis"},
                prior_completed_engine_operations={("run_eda_analysis", 42, 7, 101)},
            )

    def test_checkpoint_resume_rejects_replaying_completed_engine(self):
        turn = self._turn(
            decision="continue",
            tool_calls=[
                {
                    "name": "run_eda_analysis",
                    "arguments": {"dataset_id": 42, "file_id": 7},
                }
            ],
            response="I propose running the EDA engine.",
        )
        with self.assertRaisesRegex(ValueError, "do not replay"):
            validate_runtime_turn(
                turn,
                user_message="Resume the checkpoint for analysis 101.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools={"run_eda_analysis"},
                prior_completed_engine_operations={("run_eda_analysis", 42, 7, 101)},
            )

    def test_explicit_rerun_is_distinct_from_checkpoint_resume(self):
        turn = self._turn(
            decision="continue",
            tool_calls=[
                {
                    "name": "run_eda_analysis",
                    "arguments": {"dataset_id": 42, "file_id": 7},
                }
            ],
            response="I will propose a new EDA run as requested.",
        )
        validate_runtime_turn(
            turn,
            user_message="Run the EDA analysis again for dataset 42 and file 7.",
            trusted_failed_tool_counts={},
            prior_verified_profiles=set(),
            prior_successful_tools={"run_eda_analysis"},
            prior_completed_engine_operations={("run_eda_analysis", 42, 7, 101)},
        )

    def test_missing_checkpoint_cannot_authorize_historical_execution_claims(self):
        turn = self._turn(response="The previous analysis has completed.")
        with self.assertRaisesRegex(ValueError, "successful trusted checkpoint"):
            validate_runtime_turn(
                turn,
                user_message="Resume the previous analysis result.",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_slim_output_schema_contains_only_declared_response_fields(self):
        schema = response_json_schema(())
        self.assertEqual(
            set(schema["required"]),
            {
                "decision",
                "intent",
                "plan",
                "tool_calls",
                "handoff_request",
                "response",
                "clarification_questions",
            },
        )
        self.assertFalse(schema["additionalProperties"])

    def test_slim_prompt_never_embeds_a_schema_as_a_response_value(self):
        prompt = _slim_contract_prompt((), {})
        contract = json.loads(prompt.rsplit("\n", 1)[-1])
        self.assertNotIn("$defs", contract)
        self.assertNotIn("properties", contract)
        self.assertNotIn("response_schema", contract)

    def test_response_schema_rejects_missing_required_fields(self):
        incomplete = {"response": "The result is clear."}
        with self.assertRaises(ValidationError) as raised:
            SlimOrchestratorTurn.model_validate(incomplete)
        missing = {item["loc"][0] for item in raised.exception.errors()}
        self.assertTrue(
            {
                "decision",
                "intent",
                "plan",
                "tool_calls",
                "handoff_request",
            }.issubset(missing)
        )

    def test_response_schema_rejects_invalid_field_types(self):
        with self.assertRaises(ValidationError):
            self._turn(decision=42)
        with self.assertRaises(ValidationError):
            self._turn(plan=["valid", 1])

    def test_response_schema_accepts_empty_clarification_array_for_non_clarify_turn(self):
        turn = self._turn(
            decision="continue",
            response="The available result is summarized above.",
        )
        self.assertEqual(turn.clarification_questions, [])

    def test_response_schema_rejects_empty_clarification_array_for_clarify_turn(self):
        with self.assertRaises(ValidationError):
            self._turn(
                decision="clarify",
                clarification_questions=[],
            )

    def test_response_schema_accepts_a_valid_concise_answer(self):
        turn = self._turn(
            response="The workspace is ready for your next request."
        )
        self.assertEqual(turn.response, "The workspace is ready for your next request.")

    def test_response_sentence_limit_accepts_one_to_three_sentences(self):
        for response in (
            "One sentence.",
            "First sentence. Second sentence.",
            "First sentence. Second sentence. Third sentence.",
        ):
            with self.subTest(response=response):
                validate_runtime_turn(
                    self._turn(response=response),
                    user_message="What is a workspace?",
                    trusted_failed_tool_counts={},
                    prior_verified_profiles=set(),
                    prior_successful_tools=set(),
                )

    def test_response_sentence_limit_rejects_more_than_three_sentences(self):
        with self.assertRaisesRegex(ValueError, "one and three sentences"):
            validate_runtime_turn(
                self._turn(response="One. Two. Three. Four."),
                user_message="What is a workspace?",
                trusted_failed_tool_counts={},
                prior_verified_profiles=set(),
                prior_successful_tools=set(),
            )

    def test_missing_response_fields_are_repaired_before_runtime_returns(self):
        complete = {
            "decision": "answer",
            "intent": "Summarize the provided fact.",
            "plan": [],
            "tool_calls": [],
            "handoff_request": None,
            "response": "The result you reported shows two missing values.",
            "clarification_questions": [],
        }
        provider = SequenceProvider(
            [
                json.dumps({"response": "The result you reported shows two missing values."}),
                json.dumps(complete),
            ]
        )
        outcome = execute_agent_request(
            user_message="Hello.",
            history=[],
            context={"workflow_id": "d" * 32, "orchestration_contract_version": 1},
            settings=_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(len(provider.requests), 2)
        self.assertIn("decision: Field required", provider.requests[1].messages[-1].content)

    def test_invalid_field_types_are_repaired_before_runtime_returns(self):
        complete = {
            "decision": "answer",
            "intent": "Answer directly.",
            "plan": [],
            "tool_calls": [],
            "handoff_request": None,
            "response": "A concise answer.",
            "clarification_questions": [],
        }
        invalid = {**complete, "plan": "not-a-list"}
        provider = SequenceProvider([json.dumps(invalid), json.dumps(complete)])
        outcome = execute_agent_request(
            user_message="Hello.",
            history=[],
            context={"workflow_id": "e" * 32, "orchestration_contract_version": 1},
            settings=_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertIn("plan", provider.requests[1].messages[-1].content)

    def test_empty_clarification_questions_are_repaired(self):
        invalid = {
            "decision": "clarify",
            "intent": "Ask a focused question.",
            "plan": [],
            "tool_calls": [],
            "handoff_request": None,
            "response": "Which dataset do you mean?",
            "clarification_questions": [],
        }
        valid = {**invalid, "clarification_questions": ["Which dataset do you mean?"]}
        provider = SequenceProvider([json.dumps(invalid), json.dumps(valid)])
        outcome = execute_agent_request(
            user_message="Build a dashboard from my data.",
            history=[],
            context={"workflow_id": "f" * 32, "orchestration_contract_version": 1},
            settings=_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "blocked")
        self.assertIn("at least one question", provider.requests[1].messages[-1].content)
        pending = next(
            item
            for item in outcome.structured_results
            if item.get("type") == "pending_clarification"
        )
        self.assertEqual(pending["workflow_id"], "f" * 32)
        self.assertEqual(pending["questions"], ["Which dataset do you mean?"])

    def test_pending_clarification_is_supplied_to_the_next_orchestrator_turn(self):
        response = {
            "decision": "continue",
            "intent": "Continue with the supplied dataset scope.",
            "plan": [],
            "tool_calls": [],
            "handoff_request": None,
            "response": "I will continue with dataset 42.",
            "clarification_questions": [],
        }
        provider = SequenceProvider([json.dumps(response)])
        outcome = execute_agent_request(
            user_message="Use dataset 42.",
            history=[],
            context={
                "workflow_id": "2" * 32,
                "orchestration_contract_version": 1,
                "pending_clarification": {
                    "workflow_id": "a" * 32,
                    "questions": ["Which dataset should I use?"],
                },
            },
            settings=_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        contract_prompt = provider.requests[0].messages[1].content
        self.assertIn(
            '"pending_clarification":{"workflow_id":"' + "a" * 32,
            contract_prompt,
        )
        self.assertIn("Which dataset should I use?", contract_prompt)

    def test_repair_failure_preserves_safe_failure_and_never_dispatches(self):
        provider = SequenceProvider(["not-json", "still-not-json", "also-not-json"])
        outcome = execute_agent_request(
            user_message="List projects.",
            history=[],
            context={"workflow_id": "1" * 32, "orchestration_contract_version": 1},
            settings=_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "orchestrator_response_invalid")
        self.assertEqual(len(provider.requests), 3)
        self.assertFalse(outcome.activities)

    def test_runtime_state_separates_verified_profile_and_tool_facts(self):
        history = [
            {
                "role": "assistant",
                "metadata": {
                    "structured_results": [
                        {
                            "tool": "get_profile",
                            "status": "succeeded",
                            "data": {"dataset_id": 42, "file_id": 7, "row_count": 18},
                            "references": [
                                {
                                    "kind": "profile",
                                    "resolution": "resolved_persisted_record",
                                    "verification_status": "verified",
                                    "dataset_id": 42,
                                    "file_id": 7,
                                }
                            ],
                        },
                        {
                            "tool": "run_quality_analysis",
                            "status": "succeeded",
                            "data": {"missing_value_count": 2},
                        },
                    ]
                },
            }
        ]
        provenance = _trusted_runtime_state(history, [])["evidence_provenance"]
        self.assertIn("18", provenance["trusted_runtime_facts"])
        self.assertIn("2", provenance["executed_tool_facts"])
        self.assertNotIn("42", provenance["trusted_runtime_facts"])
        self.assertNotIn("7", provenance["trusted_runtime_facts"])

    def test_unverified_profile_data_does_not_enter_trusted_fact_provenance(self):
        state = _trusted_runtime_state(
            [
                {
                    "role": "assistant",
                    "metadata": {
                        "structured_results": [
                            {
                                "tool": "get_profile",
                                "status": "succeeded",
                                "data": {
                                    "dataset_id": 42,
                                    "file_id": 7,
                                    "row_count": 18,
                                },
                                "references": [],
                            }
                        ]
                    },
                }
            ],
            [],
        )
        self.assertEqual(state["evidence_provenance"]["trusted_runtime_facts"], [])
        self.assertEqual(state["prior_verified_profiles"], [])

    def test_pending_clarification_context_requires_trusted_workflow_identity(self):
        from app.agent.runtime import _pending_clarification_from_context

        valid = {
            "pending_clarification": {
                "workflow_id": "a" * 32,
                "questions": ["Which dataset should I use?"],
            }
        }
        self.assertEqual(
            _pending_clarification_from_context(valid),
            {"workflow_id": "a" * 32, "questions": ["Which dataset should I use?"]},
        )
        self.assertIsNone(
            _pending_clarification_from_context(
                {
                    "pending_clarification": {
                        "workflow_id": "untrusted",
                        "questions": ["Which dataset?"],
                    }
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
