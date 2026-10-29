"""
Response contract enforcement tests for InsightOS (Task B & Task D2).
Verifies that internal claims JSON envelopes never leak into user-facing chat,
invalid envelopes are rejected safely, and normal prose passes through intact.
"""

from __future__ import annotations

import json
import unittest

from app.agent.config import AgentSettings
from app.agent.contracts import ModelResponse, ProviderMessage, UsageMetadata
from app.agent.runtime import _is_raw_claims_envelope, execute_agent_request


class MockSequenceProvider:
    """Mock provider that returns pre-configured sequence of responses."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("Exhausted mock responses")
        return self._responses.pop(0)


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


class ClaimsEnvelopeDetectionTests(unittest.TestCase):
    """Direct tests for _is_raw_claims_envelope detector."""

    def test_detects_valid_claims_envelope(self):
        text = '{"claims": [{"claim_type": "arithmetic", "value": "408"}]}'
        self.assertTrue(_is_raw_claims_envelope(text))

    def test_detects_envelope_with_surrounding_whitespace(self):
        text = '  \n{"claims": []}\n  '
        self.assertTrue(_is_raw_claims_envelope(text))

    def test_rejects_ordinary_prose(self):
        self.assertFalse(_is_raw_claims_envelope("Hello! How can I help you?"))
        self.assertFalse(_is_raw_claims_envelope("301 + 90 + 7 = 398"))

    def test_rejects_non_claims_json(self):
        self.assertFalse(_is_raw_claims_envelope('{"status": "ok", "message": "done"}'))
        self.assertFalse(_is_raw_claims_envelope('{"data": [1, 2, 3]}'))

    def test_rejects_malformed_json_starting_with_brace(self):
        self.assertFalse(_is_raw_claims_envelope('{not valid json}'))
        self.assertFalse(_is_raw_claims_envelope('{"claims": '))


class ResponseContractEnforcementTests(unittest.TestCase):
    """Integration tests verifying the backend output guard in execute_agent_request."""

    def test_unvalidated_claims_envelope_in_chat_is_intercepted_and_not_leaked(self):
        # Case where model erroneously returns claims JSON for general question
        raw_claims_json = json.dumps({
            "claims": [
                {
                    "claim_type": "arithmetic",
                    "dataset_id": None,
                    "file_id": None,
                    "claim_key": "result",
                    "value": "408",
                    "unit": "number",
                    "denominator": None,
                    "evidence_reference": None,
                }
            ]
        })
        provider = MockSequenceProvider([
            ModelResponse(
                message=ProviderMessage(role="assistant", content=raw_claims_json),
                finish_reason="stop",
                usage=UsageMetadata(input_tokens=20, output_tokens=30),
            )
        ])

        outcome = execute_agent_request(
            user_message="Tell me a fun math riddle.",
            history=[],
            settings=_test_settings(),
            provider=provider,
        )

        # Must not succeed
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "unvalidated_claim_envelope")

        # Must NOT leak raw JSON or claims structure to user
        self.assertFalse(outcome.assistant_text.strip().startswith("{"), "Must not leak raw JSON to user.")
        self.assertNotIn('"claims":', outcome.assistant_text)
        self.assertNotIn("408", outcome.assistant_text, "Hallucinated number must not be surfaced as trusted value.")

        # Must contain helpful user-facing explanation
        self.assertIn("could not complete this request", outcome.assistant_text)
        self.assertTrue(any("unvalidated structured claims envelope" in w for w in outcome.warnings))

    def test_normal_conversational_prose_passes_through_intact(self):
        provider = MockSequenceProvider([
            ModelResponse(
                message=ProviderMessage(
                    role="assistant",
                    content="InsightOS supports dataset profiling, normalization, and analytics modeling.",
                ),
                finish_reason="stop",
                usage=UsageMetadata(input_tokens=15, output_tokens=25),
            )
        ])

        outcome = execute_agent_request(
            user_message="What is InsightOS?",
            history=[],
            settings=_test_settings(),
            provider=provider,
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(
            outcome.assistant_text,
            "InsightOS supports dataset profiling, normalization, and analytics modeling.",
        )
        self.assertIsNone(outcome.error_code)

    def test_malformed_json_response_does_not_crash_and_returns_cleanly(self):
        provider = MockSequenceProvider([
            ModelResponse(
                message=ProviderMessage(
                    role="assistant",
                    content='{"incomplete_json": [',
                ),
                finish_reason="stop",
                usage=UsageMetadata(input_tokens=10, output_tokens=10),
            )
        ])

        outcome = execute_agent_request(
            user_message="Hello!",
            history=[],
            settings=_test_settings(),
            provider=provider,
        )

        # Normal text pass-through for non-claims strings
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, '{"incomplete_json": [')

