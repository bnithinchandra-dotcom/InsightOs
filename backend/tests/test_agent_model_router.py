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


if __name__ == "__main__":
    unittest.main()
