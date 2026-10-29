"""
Deterministic arithmetic unit tests for InsightOS (Task A & Task D1).
Verifies that standalone arithmetic is evaluated safely and deterministically
without requiring an LLM call.
"""

from __future__ import annotations

import unittest

from app.agent.arithmetic import (
    ArithmeticSafetyError,
    evaluate_expression,
    execute_deterministic_arithmetic,
    extract_arithmetic_expression,
)
from app.agent.config import AgentSettings
from app.agent.contracts import ModelResponse, ProviderMessage, UsageMetadata
from app.agent.runtime import execute_agent_request


class MockSequenceProvider:
    """Mock provider to record whether any provider calls were attempted."""

    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ModelResponse(
            message=ProviderMessage(role="assistant", content="MOCK_SHOULD_NOT_BE_CALLED"),
            finish_reason="stop",
            usage=UsageMetadata(input_tokens=10, output_tokens=10),
        )


def _test_settings() -> AgentSettings:
    return AgentSettings(
        provider="ollama",
        model="qwen3:8b",
        base_url="http://localhost:11434",
        api_key=None,
        timeout_seconds=30,
        max_output_tokens=1024,
        max_model_turns=4,
        max_tool_calls=6,
        max_history_messages=10,
    )


class ArithmeticEvaluatorTests(unittest.TestCase):
    """Direct unit tests for the AST arithmetic evaluator."""

    def test_terse_expression_with_leading_zeros(self):
        result = evaluate_expression("301+90+07")
        self.assertEqual(result, 398)

    def test_basic_addition(self):
        self.assertEqual(evaluate_expression("10+10"), 20)

    def test_operator_precedence(self):
        # Multiplication before addition: 2 + 12 = 14
        self.assertEqual(evaluate_expression("2 + 3 * 4"), 14)

    def test_parentheses_override_precedence(self):
        # Parentheses: 12 * 6 = 72
        self.assertEqual(evaluate_expression("12 * (4 + 2)"), 72)

    def test_division_and_integer_formatting(self):
        self.assertEqual(evaluate_expression("100 / 4"), 25.0)

    def test_floor_division_and_modulo(self):
        self.assertEqual(evaluate_expression("10 // 3"), 3)
        self.assertEqual(evaluate_expression("10 % 3"), 1)

    def test_unary_operators(self):
        self.assertEqual(evaluate_expression("-5 + 10"), 5)
        self.assertEqual(evaluate_expression("+7 - (-3)"), 10)

    def test_division_by_zero_raises_safety_error(self):
        with self.assertRaises(ArithmeticSafetyError):
            evaluate_expression("10 / 0")

    def test_modulo_by_zero_raises_safety_error(self):
        with self.assertRaises(ArithmeticSafetyError):
            evaluate_expression("10 % 0")

    def test_forbidden_ast_nodes_rejected(self):
        # Variables / Names
        with self.assertRaises(ArithmeticSafetyError):
            evaluate_expression("x + 1")

        # Function calls
        with self.assertRaises(ArithmeticSafetyError):
            evaluate_expression("abs(-5)")

        # Attribute access
        with self.assertRaises(ArithmeticSafetyError):
            evaluate_expression("(1).__class__")

        # Imports or exec
        with self.assertRaises(ArithmeticSafetyError):
            evaluate_expression("__import__('os')")

    def test_oversized_expression_rejected(self):
        with self.assertRaises(ArithmeticSafetyError):
            evaluate_expression("1 + " * 100 + "1")

    def test_deeply_nested_expression_rejected(self):
        # Nesting depth > 12 (operations nested deeper than MAX_EVAL_DEPTH)
        deep_expr = "1 + (" * 15 + "1" + ")" * 15
        with self.assertRaises(ArithmeticSafetyError):
            evaluate_expression(deep_expr)


class ArithmeticIntentTests(unittest.TestCase):
    """Tests for intent extraction and classification."""

    def test_extracts_from_terse_with_equals(self):
        self.assertEqual(extract_arithmetic_expression("301+90+07="), "301+90+07")

    def test_extracts_from_natural_language_question(self):
        self.assertEqual(extract_arithmetic_expression("What is 301 + 90 + 7?"), "301 + 90 + 7")

    def test_extracts_from_calculate_command(self):
        self.assertEqual(extract_arithmetic_expression("Calculate 12 * (4 + 2)"), "12 * (4 + 2)")

    def test_extracts_from_compute_command(self):
        self.assertEqual(extract_arithmetic_expression("Compute 100 / 4"), "100 / 4")

    def test_rejects_dataset_dependent_requests(self):
        self.assertIsNone(extract_arithmetic_expression("Calculate total revenue from my dataset"))
        self.assertIsNone(extract_arithmetic_expression("What is the average order value in table 1?"))
        self.assertIsNone(extract_arithmetic_expression("Forecast sales for next month"))
        self.assertIsNone(extract_arithmetic_expression("Count missing values in column price"))

    def test_rejects_general_explanations(self):
        self.assertIsNone(extract_arithmetic_expression("Explain how addition works."))
        self.assertIsNone(extract_arithmetic_expression("Why did sales increase by 12%?"))
        self.assertIsNone(extract_arithmetic_expression("What does the metric represent?"))


class ArithmeticRuntimeIntegrationTests(unittest.TestCase):
    """End-to-end tests for runtime arithmetic integration."""

    def test_terse_expression_returns_398_without_llm_call(self):
        provider = MockSequenceProvider()
        outcome = execute_agent_request(
            user_message="301+90+07=",
            history=[],
            settings=_test_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, "301 + 90 + 7 = 398")
        self.assertEqual(len(provider.requests), 0, "No LLM provider call should occur for standalone arithmetic.")

    def test_natural_language_expression_returns_398_without_llm_call(self):
        provider = MockSequenceProvider()
        outcome = execute_agent_request(
            user_message="What is 301 + 90 + 7?",
            history=[],
            settings=_test_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, "301 + 90 + 7 = 398")
        self.assertEqual(len(provider.requests), 0)

    def test_precedence_and_grouping_in_runtime(self):
        provider = MockSequenceProvider()
        outcome = execute_agent_request(
            user_message="Calculate 12 * (4 + 2)",
            history=[],
            settings=_test_settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, "12 * (4 + 2) = 72")
        self.assertEqual(len(provider.requests), 0)

    def test_dataset_context_bypasses_standalone_arithmetic(self):
        provider = MockSequenceProvider()
        # With dataset context attached, arithmetic intent does not intercept
        outcome = execute_agent_request(
            user_message="301+90+07=",
            history=[],
            context={"dataset_id": 42},
            settings=_test_settings(),
            provider=provider,
        )
        # Bypasses standalone arithmetic evaluator because dataset is present
        self.assertEqual(len(provider.requests), 1)

    def test_unsupported_ambiguous_expression_falls_through_to_llm(self):
        provider = MockSequenceProvider()
        outcome = execute_agent_request(
            user_message="Explain what 5 squared means.",
            history=[],
            settings=_test_settings(),
            provider=provider,
        )
        self.assertEqual(len(provider.requests), 1)
