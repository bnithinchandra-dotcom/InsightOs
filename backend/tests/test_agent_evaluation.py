import unittest

from app.agent.config import AgentSettings
from app.agent.contracts import (
    ModelResponse,
    ProviderHealth,
    ProviderMessage,
    ToolCall,
    UsageMetadata,
)
from app.agent.evaluation import (
    EVALUATION_CASES,
    _failure_category,
    run_evaluation,
)


def settings():
    return AgentSettings(
        provider="ollama",
        base_url="http://provider.invalid",
        model="fixture-model",
        api_key=None,
        timeout_seconds=1,
        max_output_tokens=128,
        max_model_turns=1,
        max_tool_calls=1,
        max_history_messages=1,
    )


def response(content, finish_reason="stop", tool_calls=(), usage=None):
    return ModelResponse(
        message=ProviderMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        ),
        finish_reason=finish_reason,
        usage=usage or UsageMetadata(),
    )


def responses_for_cases():
    expected_content = {
        "instruction_following": "READY",
        "synthetic_context_extraction": "17",
        "business_metric_definition": (
            "Gross margin is (revenue - COGS) / revenue."
        ),
        "schema_interpretation": (
            "ordered_at is a date, net_revenue is decimal, and customer_id is "
            "an identifier. Its units and business meaning are not specified."
        ),
        "structured_output": (
            '{"status":"needs_clarification","questions":['
            '{"field":"time_period","reason":"A time period was not provided."}]}'
        ),
        "evidence_abstention": "The note does not state the revenue.",
        "ambiguity_clarification": "Which metric and time period should I compare?",
        "format_constraints": "1. Cedar\n2. Maple\n3. Birch",
        "tool_result_grounding": "Revenue is not available in the supplied profile.",
        "unsupported_action": "I cannot start a new analysis with these tools.",
        "repeat_consistency": "ORBIT-72",
    }
    responses = []
    for case in EVALUATION_CASES:
        for _ in range(case.repetitions):
            if case.expected_tool_name:
                responses.append(
                    response(
                        "",
                        tool_calls=(
                            ToolCall(
                                id="call-1",
                                name=case.expected_tool_name,
                                arguments=case.expected_arguments,
                            ),
                        ),
                        usage=UsageMetadata(12, 4),
                    )
                )
            else:
                responses.append(
                    response(
                        expected_content[case.name],
                        usage=UsageMetadata(12, 4),
                    )
                )
    return responses


class EvaluationProvider:
    def __init__(self, responses, health_status="healthy"):
        self.responses = iter(responses)
        self.health_status = health_status
        self.requests = []

    def check_health(self):
        return ProviderHealth(status=self.health_status)

    def complete(self, request):
        self.requests.append(request)
        return next(self.responses)


class AgentEvaluationTests(unittest.TestCase):
    def test_evaluation_runs_synthetic_cases_and_reports_results(self):
        provider = EvaluationProvider(responses_for_cases())

        report = run_evaluation(settings=settings(), provider=provider)

        self.assertEqual(report.test_count, len(EVALUATION_CASES))
        self.assertEqual(report.executed_count, len(EVALUATION_CASES))
        self.assertEqual(report.pass_count, len(EVALUATION_CASES))
        self.assertEqual(report.fail_count, 0)
        self.assertEqual(report.skipped_count, 0)
        self.assertEqual(
            len(provider.requests),
            sum(case.repetitions for case in EVALUATION_CASES),
        )
        self.assertEqual(report.category_results["tool_selection"]["passed"], 2)
        self.assertEqual(report.total_input_tokens, 12 * len(provider.requests))
        self.assertEqual(report.total_output_tokens, 4 * len(provider.requests))
        self.assertTrue(all(case.prompt for case in report.cases))
        self.assertTrue(all(case.raw_responses for case in report.cases))
        self.assertEqual(
            next(
                request
                for request in provider.requests
                if request.response_format == "json"
            ).response_format,
            "json",
        )
        self.assertTrue(
            all(
                request.tools
                for request in provider.requests
                if request.messages[-1].content
                in {
                    case.prompt
                    for case in EVALUATION_CASES
                    if case.expected_tool_name or case.name == "unsupported_action"
                }
            )
        )
        self.assertTrue(
            all(
                request.messages[0].role == "system"
                for request in provider.requests
            )
        )

    def test_evaluation_skips_cases_when_provider_health_is_unavailable(self):
        report = run_evaluation(
            settings=settings(),
            provider=EvaluationProvider([], health_status="unavailable"),
        )

        self.assertEqual(report.executed_count, 0)
        self.assertEqual(report.skipped_count, len(EVALUATION_CASES))
        self.assertEqual(
            report.failure_categories,
            {"provider_unavailable": len(EVALUATION_CASES)},
        )

    def test_evaluation_reports_unconfigured_provider_without_attempting_inference(self):
        no_provider = AgentSettings(
            provider="none",
            base_url=None,
            model=None,
            api_key=None,
            timeout_seconds=1,
            max_output_tokens=128,
            max_model_turns=1,
            max_tool_calls=1,
            max_history_messages=1,
        )

        report = run_evaluation(settings=no_provider)

        self.assertEqual(report.provider, "none")
        self.assertEqual(report.model, None)
        self.assertEqual(report.executed_count, 0)
        self.assertEqual(report.skipped_count, len(EVALUATION_CASES))
        self.assertEqual(
            report.failure_categories,
            {"provider_not_configured": len(EVALUATION_CASES)},
        )

    def test_evaluation_classifies_truncation_and_malformed_json(self):
        structured_case = next(
            case for case in EVALUATION_CASES if case.name == "structured_output"
        )
        self.assertEqual(
            _failure_category(
                structured_case,
                [response('{"status":"ready"', finish_reason="length")],
            ),
            "output_truncated",
        )
        self.assertEqual(
            _failure_category(structured_case, [response("not JSON")]),
            "malformed_structured_output",
        )

    def test_evaluation_rejects_inconsistent_repeated_answers(self):
        repeat_case = next(
            case for case in EVALUATION_CASES if case.name == "repeat_consistency"
        )
        self.assertEqual(
            _failure_category(
                repeat_case,
                [response("ORBIT-72"), response("ORBIT-73")],
            ),
            "repeat_consistency_mismatch",
        )

    def test_evidence_abstention_accepts_explicit_uncertainty(self):
        abstention_case = next(
            case for case in EVALUATION_CASES if case.name == "evidence_abstention"
        )
        self.assertIsNone(
            _failure_category(
                abstention_case,
                [response("The note does not state the revenue.")],
            )
        )

    def test_grounding_rejects_unsupported_inference_even_with_abstention(self):
        grounding_case = next(
            case
            for case in EVALUATION_CASES
            if case.name == "tool_result_grounding"
        )

        self.assertEqual(
            _failure_category(
                grounding_case,
                [
                    response(
                        "Revenue is not available. The order_id column indicates "
                        "the number of rows."
                    )
                ],
            ),
            "unsupported_claim",
        )

    def test_format_constraints_ignore_trailing_whitespace_on_lines(self):
        format_case = next(
            case for case in EVALUATION_CASES if case.name == "format_constraints"
        )

        self.assertIsNone(
            _failure_category(
                format_case,
                [response("1. Cedar  \n2. Maple  \n3. Birch")],
            )
        )

    def test_tool_selection_requires_the_expected_tool_and_schema_valid_arguments(self):
        profile_case = next(
            case for case in EVALUATION_CASES if case.name == "tool_get_profile"
        )
        wrong_tool = response(
            "",
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="list_files",
                    arguments={"dataset_id": 7},
                ),
            ),
        )
        invalid_arguments = response(
            "",
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="get_profile",
                    arguments={"dataset_id": True, "file_id": 2},
                ),
            ),
        )

        self.assertEqual(
            _failure_category(profile_case, [wrong_tool]),
            "tool_selection_mismatch",
        )
        self.assertEqual(
            _failure_category(profile_case, [invalid_arguments]),
            "tool_arguments_invalid",
        )

    def test_metric_definition_scoring_rejects_markup_denominator(self):
        metric_case = next(
            case
            for case in EVALUATION_CASES
            if case.name == "business_metric_definition"
        )

        self.assertEqual(
            _failure_category(
                metric_case,
                [response("Gross margin is (revenue - COGS) / COGS.")],
            ),
            "metric_definition_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
