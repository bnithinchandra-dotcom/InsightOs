"""
InsightOS Post-Integration Hardening Tests
===========================================
Workstream A: CORS / chat transport
Workstream B: Model eligibility and default preservation
Workstream C: Inference serialization / lock behavior
Workstream D: keep_alive RAM residency configuration

All tests are fully offline: no live Ollama calls, no DB writes, no file I/O.
"""

from __future__ import annotations

import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, Mock, patch

from fastapi.testclient import TestClient

from app.agent.config import AgentSettings, get_agent_settings
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderMessage,
)
from app.agent.model_registry import REGISTRY
from app.agent.model_router import AgentModelRouter
from app.agent.providers import OllamaProvider


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _settings(
    provider: str = "ollama",
    model: str = "qwen3:8b",
    *,
    timeout_seconds: float = 30,
    max_output_tokens: int = 512,
    max_model_turns: int = 3,
    max_tool_calls: int = 4,
    max_history_messages: int = 10,
    ollama_keep_alive_seconds: int | None = None,
) -> AgentSettings:
    return AgentSettings(
        provider=provider,
        base_url="http://127.0.0.1:11434",
        model=model,
        api_key=None,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
        max_model_turns=max_model_turns,
        max_tool_calls=max_tool_calls,
        max_history_messages=max_history_messages,
        ollama_keep_alive_seconds=ollama_keep_alive_seconds,
    )


def _mock_provider(content: str = "{}") -> Mock:
    p = Mock()
    p.complete.return_value = ModelResponse(
        message=ProviderMessage(role="assistant", content=content),
        finish_reason="stop",
    )
    return p


def _request(model: str = "qwen3:8b") -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=(ProviderMessage(role="user", content="hi"),),
        tools=(),
        max_output_tokens=64,
    )


# ---------------------------------------------------------------------------
# Workstream A — CORS and chat transport
# ---------------------------------------------------------------------------

class CORSTransportTests(unittest.TestCase):
    """
    Verifies CORSMiddleware is registered and handles preflight/cross-origin
    requests correctly for the Vite dev server and preview server origins.

    Uses FastAPI's TestClient with a custom `origin` header to simulate browser
    preflight requests. No model calls are made.
    """

    def setUp(self):
        from app.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_cors_preflight_from_vite_dev_origin_returns_200(self):
        """OPTIONS preflight from localhost:5173 must return 200, not 405."""
        response = self.client.options(
            "/api/v1/chat/provider-status",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        self.assertIn(
            response.status_code,
            {200, 204},
            f"Preflight returned {response.status_code}: {response.text}",
        )
        self.assertIn(
            "access-control-allow-origin",
            {k.lower() for k in response.headers},
            "CORS allow-origin header missing from preflight response",
        )

    def test_cors_preflight_from_preview_origin_returns_200(self):
        """OPTIONS preflight from localhost:4173 (preview) must be allowed."""
        response = self.client.options(
            "/api/v1/chat/provider-status",
            headers={
                "Origin": "http://localhost:4173",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertIn(response.status_code, {200, 204})
        self.assertIn(
            "access-control-allow-origin",
            {k.lower() for k in response.headers},
        )

    def test_cors_preflight_from_unknown_origin_is_not_allowed(self):
        """Requests from unlisted origins must not receive an allow-origin header."""
        response = self.client.options(
            "/api/v1/chat/provider-status",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        acao = response.headers.get("access-control-allow-origin", "")
        self.assertNotEqual(
            acao,
            "*",
            "Wildcard CORS origin must not be used",
        )
        self.assertNotIn(
            "evil.example.com",
            acao,
            "Untrusted origin must not be reflected in CORS headers",
        )

    def test_cors_get_from_allowed_origin_includes_allow_header(self):
        """Actual GET from allowed origin must carry ACAO header in response."""
        response = self.client.get(
            "/api/v1/chat/provider-status",
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "access-control-allow-origin",
            {k.lower() for k in response.headers},
        )

    def test_chat_post_options_preflight_for_conversation_message(self):
        """Preflight for the actual chat message POST endpoint must succeed."""
        response = self.client.options(
            "/api/v1/chat/conversations/abc123/messages",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        self.assertIn(response.status_code, {200, 204})

    def test_analytics_capabilities_route_is_registered(self):
        """GET /api/v1/chat/analytics/capabilities must exist (not 404/405)."""
        response = self.client.get(
            "/api/v1/chat/analytics/capabilities",
            headers={"Origin": "http://localhost:5173"},
        )
        # Expect 200 or 409 (workspace_profile_required), not 404 or 405
        self.assertNotEqual(
            response.status_code,
            404,
            "/api/v1/chat/analytics/capabilities returned 404 — route is missing",
        )
        self.assertNotEqual(
            response.status_code,
            405,
            "/api/v1/chat/analytics/capabilities returned 405 — method not registered",
        )


# ---------------------------------------------------------------------------
# Workstream B — Model eligibility and default preservation
# ---------------------------------------------------------------------------

class ModelEligibilityTests(unittest.TestCase):
    """
    Confirms default model selection is unchanged, candidate models are blocked
    for ineligible roles, and unknown IDs are rejected before provider dispatch.
    """

    def test_qwen3_8b_is_validated_default(self):
        assert REGISTRY.default_model_id() == "qwen3:8b"
        entry = REGISTRY.require("qwen3:8b")
        self.assertEqual(entry.validation_status, "validated")
        self.assertTrue(entry.is_default)

    def test_default_model_resolves_to_qwen3_8b_from_env(self):
        """get_agent_settings with OLLAMA_MODEL=qwen3:8b produces qwen3:8b."""
        with patch.dict(
            "os.environ",
            {
                "INSIGHTOS_LLM_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:8b",
            },
            clear=True,
        ):
            s = get_agent_settings()
        self.assertEqual(s.model, "qwen3:8b")
        self.assertEqual(s.provider, "ollama")

    def test_default_model_is_dispatched_when_no_override(self):
        """Router uses qwen3:8b when no env overrides are set."""
        provider = _mock_provider()
        router = AgentModelRouter(settings=_settings(), provider=provider)
        with patch.dict("os.environ", {}, clear=True):
            result = router.complete("orchestrator", _request())
        self.assertEqual(result.model, "qwen3:8b")

    def test_candidate_qwen3_4b_accepted_for_data_intelligence(self):
        """Candidate model eligible for role passes gate and reaches provider."""
        provider = _mock_provider()
        router = AgentModelRouter(
            settings=_settings(),
            provider=provider,
            model_overrides={"data_intelligence": "qwen3:4b"},
        )
        result = router.complete("data_intelligence", _request())
        self.assertEqual(result.model, "qwen3:4b")
        provider.complete.assert_called_once()

    def test_candidate_qwen3_4b_instruct_accepted_for_analytics_modelling(self):
        provider = _mock_provider()
        router = AgentModelRouter(
            settings=_settings(),
            provider=provider,
            model_overrides={"analytics_modelling": "qwen3:4b-instruct"},
        )
        result = router.complete("analytics_modelling", _request())
        self.assertEqual(result.model, "qwen3:4b-instruct")

    def test_candidate_qwen3_0_6b_accepted_for_insights_visualization(self):
        provider = _mock_provider()
        router = AgentModelRouter(
            settings=_settings(),
            provider=provider,
            model_overrides={"insights_visualization": "qwen3:0.6b"},
        )
        result = router.complete("insights_visualization", _request())
        self.assertEqual(result.model, "qwen3:0.6b")

    def test_unknown_model_id_rejected_before_provider_call(self):
        """Unknown model ID raises ProviderError and never calls provider.complete."""
        provider = _mock_provider()
        router = AgentModelRouter(
            settings=_settings(),
            provider=provider,
            model_overrides={"orchestrator": "totally:unknown-model"},
        )
        with self.assertRaises(ProviderError) as raised:
            router.complete("orchestrator", _request())
        self.assertEqual(raised.exception.code, "provider_model_not_registered")
        provider.complete.assert_not_called()

    def test_candidate_model_rejected_for_ineligible_role(self):
        """qwen3:0.6b (presentation-only) blocked from orchestrator before provider call."""
        provider = _mock_provider()
        router = AgentModelRouter(
            settings=_settings(),
            provider=provider,
            model_overrides={"orchestrator": "qwen3:0.6b"},
        )
        with self.assertRaises(ProviderError) as raised:
            router.complete("orchestrator", _request())
        self.assertEqual(raised.exception.code, "provider_model_ineligible")
        provider.complete.assert_not_called()

    def test_model_missing_capability_rejected_before_provider_call(self):
        """Model eligible for orchestrator but missing required capabilities is blocked."""
        from app.agent.model_registry import ModelEntry, ModelRegistry
        partial = ModelEntry(
            model_id="partial:model",
            provider="ollama",
            description="test",
            memory_tier="small",
            declared_capabilities=frozenset({"text_generation"}),
            eligible_roles=frozenset({"orchestrator"}),
            validation_status="candidate",
            is_default=True,
        )
        reg = ModelRegistry(entries=(partial,))
        with self.assertRaises(ProviderError) as raised:
            reg.require_eligible("partial:model", "orchestrator")
        self.assertEqual(raised.exception.code, "provider_model_capability_missing")

    def test_env_override_resolution_for_role(self):
        """INSIGHTOS_LLM_MODEL_ANALYTICS env var routes analytics_modelling role."""
        provider = _mock_provider()
        router = AgentModelRouter(settings=_settings(), provider=provider)
        with patch.dict(
            "os.environ",
            {"INSIGHTOS_LLM_MODEL_ANALYTICS": "qwen3:4b"},
        ):
            result = router.complete("analytics_modelling", _request())
        self.assertEqual(result.model, "qwen3:4b")

    def test_candidate_model_does_not_become_validated_automatically(self):
        """No environment variable can elevate candidate to validated status."""
        for model_id in ("qwen3:4b-instruct", "qwen3:4b", "qwen3:0.6b"):
            entry = REGISTRY.require(model_id)
            self.assertEqual(
                entry.validation_status,
                "candidate",
                f"{model_id} must remain candidate — do not promote in this milestone",
            )

    def test_openai_provider_bypasses_registry_gate(self):
        """Registry gate is Ollama-only; OpenAI-compatible provider skips it."""
        provider = _mock_provider()
        openai_settings = AgentSettings(
            provider="openai_compatible",
            base_url="http://llm.internal/v1",
            model="custom-openai-model",
            api_key="test-key",
            timeout_seconds=30,
            max_output_tokens=512,
            max_model_turns=3,
            max_tool_calls=4,
            max_history_messages=10,
        )
        router = AgentModelRouter(settings=openai_settings, provider=provider)
        # Should not raise even though custom-openai-model is not in the registry
        result = router.model_for("orchestrator")
        self.assertEqual(result, "custom-openai-model")


# ---------------------------------------------------------------------------
# Workstream C — Lock behavior and inference serialization
# ---------------------------------------------------------------------------

class InferenceLockTests(unittest.TestCase):
    """
    Confirms _generation_lock serialises inference within the process,
    is released on success and failure, and is not acquired for rejected models.
    """

    def _make_router(self, provider: Mock, timeout: float = 30) -> AgentModelRouter:
        return AgentModelRouter(
            settings=_settings(timeout_seconds=timeout),
            provider=provider,
        )

    def test_lock_is_released_after_successful_completion(self):
        """After a successful call the semaphore is available again."""
        lock = threading.BoundedSemaphore(1)
        router = self._make_router(_mock_provider())
        with patch.object(AgentModelRouter, "_generation_lock", lock):
            router.complete("orchestrator", _request())
        # Semaphore should be back to 1 — try acquiring immediately
        acquired = lock.acquire(blocking=False)
        self.assertTrue(acquired, "Lock was not released after successful completion")
        lock.release()

    def test_lock_is_released_after_provider_failure(self):
        """Even when the provider raises, the semaphore must be released."""
        lock = threading.BoundedSemaphore(1)
        failing_provider = Mock()
        failing_provider.complete.side_effect = RuntimeError("provider boom")
        router = self._make_router(failing_provider)
        with patch.object(AgentModelRouter, "_generation_lock", lock):
            with self.assertRaises(RuntimeError):
                router.complete("orchestrator", _request())
        acquired = lock.acquire(blocking=False)
        self.assertTrue(acquired, "Lock was not released after provider failure")
        lock.release()

    def test_lock_is_NOT_acquired_when_registry_rejects_model(self):
        """Registry rejection happens before lock.acquire; lock stays free."""
        lock = threading.BoundedSemaphore(1)
        provider = _mock_provider()
        router = AgentModelRouter(
            settings=_settings(),
            provider=provider,
            model_overrides={"orchestrator": "unregistered:model"},
        )
        with patch.object(AgentModelRouter, "_generation_lock", lock):
            with self.assertRaises(ProviderError) as raised:
                router.complete("orchestrator", _request())
        self.assertEqual(raised.exception.code, "provider_model_not_registered")
        provider.complete.assert_not_called()
        acquired = lock.acquire(blocking=False)
        self.assertTrue(acquired, "Lock should not have been acquired for a rejected model")
        lock.release()

    def test_timeout_waiting_for_lock_raises_provider_timeout(self):
        """When the lock is held, a new request times out correctly."""
        lock = threading.BoundedSemaphore(1)
        lock.acquire()  # Hold the lock
        router = AgentModelRouter(
            settings=_settings(timeout_seconds=0.01),
            provider=_mock_provider(),
        )
        with patch.object(AgentModelRouter, "_generation_lock", lock):
            with self.assertRaises(ProviderError) as raised:
                router.complete("orchestrator", _request())
        self.assertEqual(raised.exception.code, "provider_timeout")
        self.assertTrue(raised.exception.retryable)
        lock.release()

    def test_two_concurrent_requests_are_serialized(self):
        """Two threads cannot be inside the inference section simultaneously."""
        lock = threading.BoundedSemaphore(1)
        concurrency_violations = []
        inside = threading.Event()
        barrier = threading.Barrier(2)

        def slow_complete(_request):
            if inside.is_set():
                concurrency_violations.append(True)
            inside.set()
            time.sleep(0.05)
            inside.clear()
            return ModelResponse(
                message=ProviderMessage(role="assistant", content="ok"),
                finish_reason="stop",
            )

        slow_provider = Mock()
        slow_provider.complete.side_effect = slow_complete

        router = AgentModelRouter(settings=_settings(), provider=slow_provider)

        results = []

        def run():
            barrier.wait()
            try:
                r = router.complete("orchestrator", _request())
                results.append(r)
            except Exception as exc:
                results.append(exc)

        t1 = threading.Thread(target=run)
        t2 = threading.Thread(target=run)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertEqual(len(concurrency_violations), 0, "Concurrent inference detected!")
        self.assertEqual(len(results), 2)

    def test_lock_is_class_level_not_instance_level(self):
        """_generation_lock is a class attribute shared across all router instances."""
        r1 = AgentModelRouter(settings=_settings(), provider=_mock_provider())
        r2 = AgentModelRouter(settings=_settings(), provider=_mock_provider())
        self.assertIs(
            AgentModelRouter._generation_lock,
            r1._generation_lock if hasattr(r1, "_generation_lock") else AgentModelRouter._generation_lock,
        )
        # Both share the same lock object
        self.assertIs(r1.__class__._generation_lock, r2.__class__._generation_lock)

    def test_worker_performs_no_inference(self):
        """Worker process source must not reference model/ollama/agent inference."""
        import worker.app.main as worker_main
        import inspect
        src = inspect.getsource(worker_main)
        forbidden = ["OllamaProvider", "create_provider", "execute_agent_request", "AgentModelRouter"]
        for term in forbidden:
            self.assertNotIn(
                term,
                src,
                f"Worker references {term!r} — it may be performing inference",
            )


# ---------------------------------------------------------------------------
# Workstream D — keep_alive RAM residency configuration
# ---------------------------------------------------------------------------

class FakeHttpResponse:
    def __init__(self, payload, status_code: int = 200):
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        import json
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def iter_bytes(self): yield self.body


class FakeHttpClient:
    def __init__(self, response, **_kw):
        self.response = response
        self.request = None

    def __enter__(self): return self
    def __exit__(self, *_): return False

    def stream(self, method, url, json=None, headers=None):
        self.request = (method, url, json, headers)
        return self.response


_OLLAMA_RESPONSE = {
    "message": {"role": "assistant", "content": "ok"},
    "done_reason": "stop",
}


class KeepAliveTests(unittest.TestCase):
    """Verifies keep_alive is plumbed correctly and does not corrupt the payload."""

    def _make_client_for_settings(self, s: AgentSettings) -> tuple[OllamaProvider, FakeHttpClient]:
        client = FakeHttpClient(FakeHttpResponse(_OLLAMA_RESPONSE))
        provider = OllamaProvider(s)
        return provider, client

    def test_keep_alive_absent_when_not_configured(self):
        """Unconfigured keep_alive: key must be absent from payload."""
        s = _settings(ollama_keep_alive_seconds=None)
        provider, client = self._make_client_for_settings(s)
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            provider.complete(ModelRequest("qwen3:8b", (), (), 32))
        self.assertNotIn("keep_alive", client.request[2])

    def test_keep_alive_present_when_configured_300(self):
        """keep_alive=300 must appear in the payload."""
        s = _settings(ollama_keep_alive_seconds=300)
        provider, client = self._make_client_for_settings(s)
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            provider.complete(ModelRequest("qwen3:8b", (), (), 32))
        self.assertEqual(client.request[2]["keep_alive"], 300)

    def test_keep_alive_zero_is_included_not_omitted(self):
        """keep_alive=0 (unload immediately) must appear in payload, not be treated as falsy."""
        s = _settings(ollama_keep_alive_seconds=0)
        provider, client = self._make_client_for_settings(s)
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            provider.complete(ModelRequest("qwen3:8b", (), (), 32))
        self.assertIn("keep_alive", client.request[2])
        self.assertEqual(client.request[2]["keep_alive"], 0)

    def test_keep_alive_not_propagated_for_openai_provider(self):
        """keep_alive is an Ollama-specific field; OpenAI payload must not include it."""
        from app.agent.providers import OpenAICompatibleProvider
        openai_settings = AgentSettings(
            provider="openai_compatible",
            base_url="http://llm.internal/v1",
            model="gpt-test",
            api_key="key",
            timeout_seconds=30,
            max_output_tokens=64,
            max_model_turns=3,
            max_tool_calls=4,
            max_history_messages=10,
        )
        import json
        resp = FakeHttpResponse({
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        })
        client = FakeHttpClient(resp)
        provider = OpenAICompatibleProvider(openai_settings)
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            provider.complete(ModelRequest("gpt-test", (), (), 32))
        self.assertNotIn("keep_alive", client.request[2])

    def test_invalid_keep_alive_config_raises_provider_error(self):
        """Negative INSIGHTOS_OLLAMA_KEEP_ALIVE must raise ProviderError."""
        with patch.dict(
            "os.environ",
            {
                "INSIGHTOS_LLM_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:8b",
                "INSIGHTOS_OLLAMA_KEEP_ALIVE": "-5",
            },
            clear=True,
        ):
            with self.assertRaises(ProviderError) as raised:
                get_agent_settings()
        self.assertEqual(raised.exception.code, "provider_configuration_invalid")

    def test_non_integer_keep_alive_config_raises_provider_error(self):
        """Non-integer INSIGHTOS_OLLAMA_KEEP_ALIVE must raise ProviderError."""
        with patch.dict(
            "os.environ",
            {
                "INSIGHTOS_LLM_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:8b",
                "INSIGHTOS_OLLAMA_KEEP_ALIVE": "five",
            },
            clear=True,
        ):
            with self.assertRaises(ProviderError) as raised:
                get_agent_settings()
        self.assertEqual(raised.exception.code, "provider_configuration_invalid")

    def test_keep_alive_env_var_zero_is_valid(self):
        """INSIGHTOS_OLLAMA_KEEP_ALIVE=0 must parse to 0 (not rejected)."""
        with patch.dict(
            "os.environ",
            {
                "INSIGHTOS_LLM_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:8b",
                "INSIGHTOS_OLLAMA_KEEP_ALIVE": "0",
            },
            clear=True,
        ):
            s = get_agent_settings()
        self.assertEqual(s.ollama_keep_alive_seconds, 0)

    def test_lock_released_when_provider_raises_during_inference(self):
        """keep_alive configured; if provider fails, lock must still be released."""
        lock = threading.BoundedSemaphore(1)
        failing = Mock()
        failing.complete.side_effect = ProviderError("provider_inference_failed", "boom")
        router = AgentModelRouter(
            settings=_settings(ollama_keep_alive_seconds=300),
            provider=failing,
        )
        with patch.object(AgentModelRouter, "_generation_lock", lock):
            with self.assertRaises(ProviderError):
                router.complete("orchestrator", _request())
        acquired = lock.acquire(blocking=False)
        self.assertTrue(acquired, "Lock not released after provider failure with keep_alive set")
        lock.release()


if __name__ == "__main__":
    unittest.main()

