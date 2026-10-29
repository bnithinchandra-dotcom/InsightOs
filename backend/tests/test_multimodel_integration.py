"""
Multi-model integration and routing verification tests for InsightOS (Task C & Task D3).
Verifies that alternate models can be configured and routed, model IDs reach
the provider payload unchanged, candidate gates are enforced, and qwen3:8b remains default.
"""

from __future__ import annotations

import unittest

from app.agent.config import AgentSettings
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderMessage,
    UsageMetadata,
)
from app.agent.model_registry import REGISTRY
from app.agent.model_router import AgentModelRouter


class RecordingProvider:
    """Mock provider that records the exact ModelRequest sent to it."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            message=ProviderMessage(role="assistant", content="Routed successfully."),
            finish_reason="stop",
            usage=UsageMetadata(input_tokens=10, output_tokens=15),
        )


def _base_settings(model: str = "qwen3:8b") -> AgentSettings:
    return AgentSettings(
        provider="ollama",
        model=model,
        base_url="http://localhost:11434",
        api_key=None,
        timeout_seconds=30,
        max_output_tokens=1024,
        max_model_turns=4,
        max_tool_calls=6,
        max_history_messages=10,
    )


class MultiModelRoutingTests(unittest.TestCase):
    """Tests for model selection, routing, and provider payload fidelity."""

    def test_default_model_is_qwen3_8b_and_validated(self):
        default_id = REGISTRY.default_model_id()
        self.assertEqual(default_id, "qwen3:8b")
        entry = REGISTRY.require("qwen3:8b")
        self.assertEqual(entry.validation_status, "validated")
        self.assertTrue(entry.is_default)

    def test_alternate_model_override_reaches_provider_payload(self):
        # Configure data_intelligence role with qwen3:4b-instruct candidate override
        provider = RecordingProvider()
        router = AgentModelRouter(
            settings=_base_settings(model="qwen3:8b"),
            provider=provider,
            model_overrides={"data_intelligence": "qwen3:4b-instruct"},
        )

        # Dispatch data_intelligence role
        completion = router.complete(
            "data_intelligence",
            ModelRequest(
                model="qwen3:8b",  # Initial request model before routing
                messages=(ProviderMessage(role="user", content="Analyze schema"),),
                tools=(),
                max_output_tokens=500,
            ),
        )

        self.assertEqual(completion.model, "qwen3:4b-instruct")
        self.assertEqual(len(provider.requests), 1)
        # Assert provider payload received the exact overridden model identifier
        self.assertEqual(provider.requests[0].model, "qwen3:4b-instruct")

    def test_orchestrator_preserves_default_when_specialist_is_overridden(self):
        provider = RecordingProvider()
        router = AgentModelRouter(
            settings=_base_settings(model="qwen3:8b"),
            provider=provider,
            model_overrides={"data_intelligence": "qwen3:4b-instruct"},
        )

        # Orchestrator should still dispatch qwen3:8b
        completion = router.complete(
            "orchestrator",
            ModelRequest(
                model="qwen3:8b",
                messages=(ProviderMessage(role="user", content="Plan workflow"),),
                tools=(),
                max_output_tokens=500,
            ),
        )

        self.assertEqual(completion.model, "qwen3:8b")
        self.assertEqual(provider.requests[0].model, "qwen3:8b")

    def test_unregistered_model_raises_provider_error(self):
        provider = RecordingProvider()
        router = AgentModelRouter(
            settings=_base_settings(model="unregistered-custom-model:latest"),
            provider=provider,
        )

        with self.assertRaises(ProviderError) as ctx:
            router.complete(
                "orchestrator",
                ModelRequest(
                    model="unregistered-custom-model:latest",
                    messages=(ProviderMessage(role="user", content="Hi"),),
                    tools=(),
                    max_output_tokens=500,
                ),
            )
        self.assertEqual(ctx.exception.code, "provider_model_not_registered")
        self.assertEqual(len(provider.requests), 0)

    def test_candidate_model_missing_tool_calling_blocked_from_orchestrator(self):
        # qwen3:4b-instruct does not declare tool_calling capability
        provider = RecordingProvider()
        router = AgentModelRouter(
            settings=_base_settings(model="qwen3:4b-instruct"),
            provider=provider,
        )

        with self.assertRaises(ProviderError) as ctx:
            router.complete(
                "orchestrator",
                ModelRequest(
                    model="qwen3:4b-instruct",
                    messages=(ProviderMessage(role="user", content="Plan"),),
                    tools=(),
                    max_output_tokens=500,
                ),
            )
        self.assertIn(ctx.exception.code, {"provider_model_ineligible", "provider_model_capability_missing"})
        self.assertEqual(len(provider.requests), 0)

    def test_presentation_candidate_model_0_6b_eligible_for_presentation_only(self):
        entry = REGISTRY.require("qwen3:0.6b")
        self.assertEqual(entry.eligible_roles, frozenset({"insights_visualization"}))
        self.assertEqual(entry.validation_status, "candidate")

        provider = RecordingProvider()
        router = AgentModelRouter(
            settings=_base_settings(model="qwen3:8b"),
            provider=provider,
            model_overrides={"insights_visualization": "qwen3:0.6b"},
        )

        completion = router.complete(
            "insights_visualization",
            ModelRequest(
                model="qwen3:8b",
                messages=(ProviderMessage(role="user", content="Format summary"),),
                tools=(),
                max_output_tokens=256,
            ),
        )
        self.assertEqual(completion.model, "qwen3:0.6b")
        self.assertEqual(provider.requests[0].model, "qwen3:0.6b")
