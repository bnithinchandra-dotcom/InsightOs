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
)
from app.agent.model_router import AgentModelRouter


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

