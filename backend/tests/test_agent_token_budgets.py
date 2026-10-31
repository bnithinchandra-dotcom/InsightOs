import unittest
from unittest.mock import Mock

from app.agent.contracts import ModelResponse, ProviderMessage
from app.agent.model_router import RoutedCompletion
from app.agent.specialist_agents import (
    DataIntelligenceResult,
    SpecialistAgentError,
    _invoke,
)


class SpecialistCompletenessTests(unittest.TestCase):
    def _invoke_with_output(self, content: str, finish_reason: str) -> Mock:
        router = Mock()
        router.complete.return_value = RoutedCompletion(
            role="data_intelligence",
            model="fixture-model",
            response=ModelResponse(
                message=ProviderMessage(role="assistant", content=content),
                finish_reason=finish_reason,
            ),
        )
        return router

    def _invoke(self, router: Mock):
        return _invoke(
            router,
            role="data_intelligence",
            instruction="Review verified inspection evidence.",
            context={
                "workflow_id": "a" * 32,
                "objective": "Inspect this dataset",
                "operation": "run_quality_analysis",
            },
            model=DataIntelligenceResult,
        )

    def test_truncated_specialist_json_is_incomplete_not_successful(self):
        router = self._invoke_with_output(
            '{"contract_version":1,"status":"completed"',
            "length",
        )
        with self.assertRaises(SpecialistAgentError) as raised:
            self._invoke(router)
        self.assertEqual(raised.exception.code, "agent_response_incomplete")

    def test_missing_required_specialist_fields_are_rejected(self):
        router = self._invoke_with_output(
            '{"contract_version":1}',
            "stop",
        )
        with self.assertRaises(SpecialistAgentError) as raised:
            self._invoke(router)
        self.assertEqual(raised.exception.code, "agent_response_invalid")

    def test_invalid_specialist_json_is_rejected(self):
        router = self._invoke_with_output("{", "stop")
        with self.assertRaises(SpecialistAgentError) as raised:
            self._invoke(router)
        self.assertEqual(raised.exception.code, "agent_response_invalid")


if __name__ == "__main__":
    unittest.main()
