import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from app.agent.config import AgentSettings
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderMessage,
    RequestTelemetry,
    UsageMetadata,
)
from app.agent.model_router import AgentModelRouter, WorkflowTokenBudget


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


class ModelRouterTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.provider.complete.return_value = ModelResponse(
            message=ProviderMessage(role="assistant", content="{}"),
            finish_reason="stop",
        )

    def test_role_override_routes_without_changing_provider(self):
        router = AgentModelRouter(
            settings=_settings(),
            provider=self.provider,
            model_overrides={
                "orchestrator": "qwen3:8b",
                "data_intelligence": "qwen3:4b",
                "analytics_modelling": "qwen3:4b",
                "insights_visualization": "qwen3:0.6b",
            },
        )
        request = ModelRequest(
            model="ignored",
            messages=(ProviderMessage(role="user", content="route"),),
            tools=(),
            max_output_tokens=4096,
        )

        selected = [
            router.complete(role, request).model
            for role in (
                "orchestrator",
                "data_intelligence",
                "analytics_modelling",
                "insights_visualization",
            )
        ]

        self.assertEqual(
            selected,
            ["qwen3:8b", "qwen3:4b", "qwen3:4b", "qwen3:0.6b"],
        )
        self.assertEqual(self.provider.complete.call_count, 4)
        for call in self.provider.complete.call_args_list:
            routed_request = call.args[0]
            self.assertLessEqual(routed_request.max_output_tokens, 512)

    def test_role_configuration_falls_back_to_existing_selected_model(self):
        router = AgentModelRouter(settings=_settings(), provider=self.provider)
        with patch.dict("os.environ", {"INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE": ""}):
            selected = router.complete(
                "data_intelligence",
                ModelRequest(
                    model="old",
                    messages=(ProviderMessage(role="user", content="route"),),
                    tools=(),
                    max_output_tokens=128,
                ),
            )
        self.assertEqual(selected.model, "qwen3:8b")
        self.assertEqual(self.provider.complete.call_args.args[0].model, "qwen3:8b")

    def test_role_environment_override_is_applied(self):
        router = AgentModelRouter(settings=_settings(), provider=self.provider)
        with patch.dict(
            "os.environ",
            {"INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE": "qwen3:4b"},
        ):
            selected = router.complete(
                "data_intelligence",
                ModelRequest(
                    model="old",
                    messages=(ProviderMessage(role="user", content="route"),),
                    tools=(),
                    max_output_tokens=128,
                ),
            )
        self.assertEqual(selected.model, "qwen3:4b")

    def test_task_specific_output_budgets_and_global_hard_cap(self):
        configured = replace(_settings(), max_output_tokens=65_535)
        router = AgentModelRouter(settings=configured, provider=self.provider)
        categories = {
            "lightweight": 1024,
            "routing": 768,
            "conversation": 3072,
            "analytical": 4096,
            "complex_reasoning": 6144,
            "verification": 2048,
        }
        for category, expected in categories.items():
            with self.subTest(category=category):
                router.complete(
                    "orchestrator",
                    ModelRequest(
                        model="ignored",
                        messages=(ProviderMessage(role="user", content="test"),),
                        tools=(),
                        max_output_tokens=65_535,
                        task_category=category,
                        is_final=True,
                    ),
                )
                routed = self.provider.complete.call_args.args[0]
                self.assertEqual(routed.max_output_tokens, expected)
                self.assertLessEqual(routed.max_output_tokens, 16_384)

    def test_router_applies_task_timeout_without_exceeding_global_timeout(self):
        configured = replace(_settings(), timeout_seconds=90)
        router = AgentModelRouter(settings=configured, provider=self.provider)
        router.complete(
            "orchestrator",
            ModelRequest(
                model="ignored",
                messages=(ProviderMessage(role="user", content="route"),),
                tools=(),
                max_output_tokens=32,
                task_category="routing",
            ),
        )
        self.assertEqual(
            self.provider.complete.call_args.args[0].timeout_seconds,
            20.0,
        )

    def test_workflow_budget_aggregates_actual_usage_and_reserves_final_capacity(self):
        settings = replace(
            _settings(),
            max_output_tokens=16_384,
            workflow_token_budget=5_000,
            workflow_final_reserve_tokens=1_000,
        )
        self.provider.complete.side_effect = [
            ModelResponse(
                ProviderMessage(role="assistant", content="routed"),
                finish_reason="stop",
                usage=UsageMetadata(input_tokens=200, output_tokens=300),
            ),
            ModelResponse(
                ProviderMessage(role="assistant", content="inspected"),
                finish_reason="stop",
                usage=UsageMetadata(input_tokens=100, output_tokens=200),
            ),
            ModelResponse(
                ProviderMessage(role="assistant", content="verified"),
                finish_reason="stop",
                usage=UsageMetadata(input_tokens=100, output_tokens=100),
            ),
        ]
        telemetry = []
        router = AgentModelRouter(
            settings=settings,
            provider=self.provider,
            workflow_budget=WorkflowTokenBudget(5_000, 1_000),
            telemetry_callback=telemetry.append,
        )
        for role in (
            "orchestrator",
            "data_intelligence",
            "analytics_modelling",
        ):
            router.complete(
                role,
                ModelRequest(
                    model="ignored",
                    messages=(ProviderMessage(role="user", content="private"),),
                    tools=(),
                    max_output_tokens=16_384,
                    task_category="analytical",
                    workflow_id="a" * 32,
                ),
            )
        self.assertEqual(telemetry[-1].cumulative_input_tokens, 400)
        self.assertEqual(telemetry[-1].cumulative_output_tokens, 600)
        self.assertEqual(telemetry[-1].workflow_budget_tokens, 5_000)
        self.assertNotIn("private", str(telemetry[-1].as_dict()))
        self.assertEqual(self.provider.complete.call_args_list[0].args[0].max_output_tokens, 4_000)

    def test_workflow_budget_fails_closed_when_reserved_capacity_is_exhausted(self):
        budget = WorkflowTokenBudget(1_000, 400)
        self.assertEqual(budget.reserve_output(768, is_final=False), 600)
        budget.record(600, UsageMetadata(input_tokens=350, output_tokens=250))
        with self.assertRaises(ProviderError) as raised:
            budget.reserve_output(768, is_final=False)
        self.assertEqual(raised.exception.code, "workflow_token_budget_exhausted")

    def test_router_marks_length_finish_reason_incomplete(self):
        self.provider.complete.return_value = ModelResponse(
            message=ProviderMessage(role="assistant", content="Partial"),
            finish_reason="length",
            usage=UsageMetadata(input_tokens=8, output_tokens=12),
        )
        telemetry = []
        router = AgentModelRouter(
            settings=replace(_settings(), max_output_tokens=16_384),
            provider=self.provider,
            telemetry_callback=telemetry.append,
        )
        result = router.complete(
            "orchestrator",
            ModelRequest(
                model="ignored",
                messages=(ProviderMessage(role="user", content="private"),),
                tools=(),
                max_output_tokens=3072,
                task_category="conversation",
                workflow_id="b" * 32,
            ),
        )
        self.assertTrue(result.response.telemetry.incomplete)
        self.assertEqual(telemetry[0].input_tokens, 8)
        self.assertEqual(telemetry[0].output_tokens, 12)

    def test_failed_generation_telemetry_is_sanitized_and_reports_failure(self):
        self.provider.complete.side_effect = ProviderError(
            "provider_timeout",
            "Private provider detail must not be logged.",
            retryable=True,
        )
        telemetry = []
        router = AgentModelRouter(
            settings=_settings(),
            provider=self.provider,
            telemetry_callback=telemetry.append,
        )
        with self.assertRaises(ProviderError):
            router.complete(
                "orchestrator",
                ModelRequest(
                    model="ignored",
                    messages=(
                        ProviderMessage(
                            role="user",
                            content="private dataset prompt",
                        ),
                    ),
                    tools=(),
                    max_output_tokens=32,
                    task_category="conversation",
                    workflow_id="c" * 32,
                ),
            )
        self.assertEqual(len(telemetry), 1)
        self.assertEqual(telemetry[0].failure_reason, "provider_timeout")
        self.assertTrue(telemetry[0].incomplete)
        self.assertEqual(telemetry[0].retries, 0)
        self.assertIsNone(telemetry[0].input_tokens)
        self.assertNotIn("private dataset prompt", str(telemetry[0].as_dict()))
        self.assertNotIn("Private provider detail", str(telemetry[0].as_dict()))

    def test_waiting_for_inference_slot_times_out_without_calling_provider(self):
        lock = threading.BoundedSemaphore(1)
        self.assertTrue(lock.acquire(blocking=False))
        router = AgentModelRouter(
            settings=replace(_settings(), timeout_seconds=0.01),
            provider=self.provider,
        )
        request = ModelRequest(
            model="ignored",
            messages=(ProviderMessage(role="user", content="wait"),),
            tools=(),
            max_output_tokens=16,
        )

        with patch.object(AgentModelRouter, "_generation_lock", lock):
            with self.assertRaises(ProviderError) as raised:
                router.complete("orchestrator", request)

        self.assertEqual(raised.exception.code, "provider_timeout")
        self.assertTrue(raised.exception.retryable)
        self.provider.complete.assert_not_called()
        lock.release()

    def test_inference_slot_is_released_when_provider_raises(self):
        lock = threading.BoundedSemaphore(1)
        failing_provider = Mock()
        failing_provider.complete.side_effect = RuntimeError("provider failure")
        router = AgentModelRouter(settings=_settings(), provider=failing_provider)
        request = ModelRequest(
            model="ignored",
            messages=(ProviderMessage(role="user", content="release"),),
            tools=(),
            max_output_tokens=16,
        )

        with patch.object(AgentModelRouter, "_generation_lock", lock):
            with self.assertRaisesRegex(RuntimeError, "provider failure"):
                router.complete("orchestrator", request)
            self.provider.complete.return_value = ModelResponse(
                message=ProviderMessage(role="assistant", content="{}"),
                finish_reason="stop",
            )
            succeeding_router = AgentModelRouter(
                settings=_settings(),
                provider=self.provider,
            )
            result = succeeding_router.complete("orchestrator", request)

        self.assertEqual(result.response.message.content, "{}")


# ======================================================================
# Multi-Model Registry & keep_alive Tests
# ======================================================================

"""Additional tests for registry gate and keep_alive plumbing.

Appended to test_agent_model_router.py; the original ModelRouterTests class
is defined above these additions.
"""

import json
import unittest
from unittest.mock import patch, Mock

from app.agent.config import AgentSettings
from app.agent.contracts import ModelRequest, ModelResponse, ProviderError, ProviderMessage
from app.agent.model_registry import REGISTRY
from app.agent.model_router import AgentModelRouter
from app.agent.providers import OllamaProvider


# Reuse the helpers from the top of the file
def _settings_ollama(**overrides):
    base = dict(
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
    base.update(overrides)
    return AgentSettings(**base)


class FakeHttpResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.body = body

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def iter_bytes(self): yield self.body


class FakeHttpClient:
    def __init__(self, response, **_kwargs):
        self.response = response
        self.request = None

    def __enter__(self): return self
    def __exit__(self, *_): return False

    def stream(self, method, url, json=None, headers=None):
        self.request = (method, url, json, headers)
        return self.response


class RegistryGateTests(unittest.TestCase):
    """Registry gate behavior added by the multi-model integration."""

    def _mock_provider(self):
        p = Mock()
        p.complete.return_value = ModelResponse(
            message=ProviderMessage(role="assistant", content="{}"),
            finish_reason="stop",
        )
        return p

    def _request(self):
        return ModelRequest(
            model="ignored",
            messages=(ProviderMessage(role="user", content="test"),),
            tools=(),
            max_output_tokens=64,
        )

    # ------------------------------------------------------------------
    # Registry gate — unknown model blocked for ollama provider
    # ------------------------------------------------------------------

    def test_registry_gate_blocks_unknown_ollama_model(self):
        """model_for() raises ProviderError when the model is not registered."""
        router = AgentModelRouter(
            settings=_settings_ollama(),
            provider=self._mock_provider(),
            model_overrides={"orchestrator": "not-in-registry:model"},
        )
        with self.assertRaises(ProviderError) as raised:
            router.complete("orchestrator", self._request())
        self.assertEqual(raised.exception.code, "provider_model_not_registered")

    def test_registry_gate_blocks_ineligible_ollama_model(self):
        """model_for() raises ProviderError when model is registered but ineligible for role."""
        router = AgentModelRouter(
            settings=_settings_ollama(),
            provider=self._mock_provider(),
            model_overrides={"orchestrator": "qwen3:0.6b"},  # only eligible for insights_visualization
        )
        with self.assertRaises(ProviderError) as raised:
            router.complete("orchestrator", self._request())
        self.assertEqual(raised.exception.code, "provider_model_ineligible")

    def test_existing_qwen3_8b_passes_registry_check(self):
        """Default model passes registry gate for every role without raising."""
        router = AgentModelRouter(
            settings=_settings_ollama(model="qwen3:8b"),
            provider=self._mock_provider(),
        )
        for role in ("orchestrator", "data_intelligence", "analytics_modelling", "insights_visualization"):
            with self.subTest(role=role):
                result = router.complete(role, self._request())
                self.assertEqual(result.model, "qwen3:8b")

    # ------------------------------------------------------------------
    # keep_alive propagation to Ollama payload
    # ------------------------------------------------------------------

    def test_keep_alive_propagated_to_ollama_payload(self):
        """When ollama_keep_alive_seconds is set, keep_alive appears in POST body."""
        ollama_settings = _settings_ollama(ollama_keep_alive_seconds=300)
        provider = OllamaProvider(ollama_settings)
        client = FakeHttpClient(FakeHttpResponse({
            "message": {"role": "assistant", "content": "ok"},
            "done_reason": "stop",
        }))
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            provider.complete(ModelRequest("qwen3:8b", (), (), 32))
        self.assertIn("keep_alive", client.request[2])
        self.assertEqual(client.request[2]["keep_alive"], 300)

    def test_keep_alive_absent_when_not_configured(self):
        """When ollama_keep_alive_seconds is None, keep_alive is NOT in POST body."""
        ollama_settings = _settings_ollama()  # ollama_keep_alive_seconds=None by default
        provider = OllamaProvider(ollama_settings)
        client = FakeHttpClient(FakeHttpResponse({
            "message": {"role": "assistant", "content": "ok"},
            "done_reason": "stop",
        }))
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            provider.complete(ModelRequest("qwen3:8b", (), (), 32))
        self.assertNotIn("keep_alive", client.request[2])

    def test_keep_alive_zero_is_included_in_payload(self):
        """keep_alive=0 is a valid value (unload immediately) and must appear in body."""
        ollama_settings = _settings_ollama(ollama_keep_alive_seconds=0)
        provider = OllamaProvider(ollama_settings)
        client = FakeHttpClient(FakeHttpResponse({
            "message": {"role": "assistant", "content": "ok"},
            "done_reason": "stop",
        }))
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            provider.complete(ModelRequest("qwen3:8b", (), (), 32))
        self.assertIn("keep_alive", client.request[2])
        self.assertEqual(client.request[2]["keep_alive"], 0)


if __name__ == "__main__":
    unittest.main()
