from __future__ import annotations

import asyncio
import threading
import time
import unittest
from dataclasses import replace

from app.agent.config import AgentSettings
from app.agent.contracts import ModelRequest, ModelResponse, ProviderError, ProviderMessage
from app.agent.model_lifecycle import ModelLifecycleManager
from app.agent.model_router import AgentModelRouter
from app.agent.providers import OllamaProvider


def _settings() -> AgentSettings:
    return AgentSettings(
        provider="ollama",
        base_url="http://provider.invalid",
        model="qwen3:8b",
        api_key=None,
        timeout_seconds=2,
        max_output_tokens=128,
        max_model_turns=2,
        max_tool_calls=2,
        max_history_messages=4,
    )


class FakeLifecycleProvider:
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.events: list[tuple[str, str]] = []
        self.generation_active = False
        self.fail_load = False

    def loaded_models(self) -> frozenset[str]:
        return frozenset(self.loaded)

    def load_model(self, model: str) -> None:
        self.events.append(("load", model))
        if self.fail_load:
            return
        self.loaded.add(model)

    def unload_model(self, model: str) -> None:
        if self.generation_active:
            raise AssertionError("A model was unloaded during active generation.")
        self.events.append(("unload", model))
        self.loaded.discard(model)

    def complete(self, _request: ModelRequest) -> ModelResponse:
        self.generation_active = True
        try:
            if not self.loaded:
                raise AssertionError("Generation started without a resident model.")
            time.sleep(0.01)
            return ModelResponse(
                message=ProviderMessage(role="assistant", content="ok"),
                finish_reason="stop",
            )
        finally:
            self.generation_active = False


class ModelLifecycleTests(unittest.TestCase):
    def test_activation_release_and_reconciliation_follow_ollama_residency(self):
        provider = FakeLifecycleProvider()
        manager = ModelLifecycleManager(provider)

        activated = manager.activate("qwen3:8b")
        self.assertEqual(activated.state, "active")
        self.assertEqual(activated.active_model, "qwen3:8b")
        self.assertGreaterEqual(activated.activation_duration_ms, 0)

        provider.loaded.clear()
        reconciled = manager.reconcile()
        self.assertEqual(reconciled.state, "inactive")
        self.assertIsNone(reconciled.active_model)

        manager.activate("qwen3:8b")
        released = manager.release()
        self.assertEqual(released.state, "inactive")
        self.assertIsNone(released.active_model)
        self.assertGreaterEqual(released.release_duration_ms, 0)

    def test_generation_lease_prevents_concurrent_unload(self):
        provider = FakeLifecycleProvider()
        manager = ModelLifecycleManager(provider, transition_timeout_seconds=1)
        release_started = threading.Event()
        released = threading.Event()

        with manager.generation_lease("qwen3:8b", release_after=False):
            def release_model() -> None:
                release_started.set()
                manager.release()
                released.set()

            worker = threading.Thread(target=release_model)
            worker.start()
            self.assertTrue(release_started.wait(1))
            time.sleep(0.03)
            self.assertFalse(released.is_set())
            self.assertIn("qwen3:8b", provider.loaded)

        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(released.is_set())
        self.assertNotIn("qwen3:8b", provider.loaded)

    def test_failed_activation_is_reported_and_state_is_not_success_shaped(self):
        provider = FakeLifecycleProvider()
        provider.fail_load = True
        manager = ModelLifecycleManager(provider)

        with self.assertRaises(ProviderError) as raised:
            manager.activate("qwen3:8b")
        self.assertEqual(
            raised.exception.code,
            "provider_model_activation_unverified",
        )
        self.assertEqual(manager.snapshot().state, "failed")
        self.assertIsNone(manager.active_model)

    def test_router_holds_generation_lease_and_switches_models_serially(self):
        provider = FakeLifecycleProvider()
        settings = replace(_settings(), max_output_tokens=1024)
        router = AgentModelRouter(
            settings=settings,
            provider=provider,
            model_overrides={"data_intelligence": "qwen3:4b"},
            manage_model_lifecycle=True,
        )
        request = ModelRequest(
            model="ignored",
            messages=(ProviderMessage(role="user", content="run"),),
            tools=(),
            max_output_tokens=64,
        )

        self.assertEqual(router.complete("orchestrator", request).model, "qwen3:8b")
        self.assertEqual(
            router.complete("data_intelligence", request).model,
            "qwen3:4b",
        )
        self.assertEqual(
            provider.events,
            [
                ("load", "qwen3:8b"),
                ("unload", "qwen3:8b"),
                ("load", "qwen3:4b"),
                ("unload", "qwen3:4b"),
            ],
        )
        self.assertIsNone(router.model_lifecycle.active_model)

    def test_ollama_provider_uses_load_unload_and_residency_endpoints(self):
        provider = OllamaProvider(_settings())
        posted: list[tuple[str, dict]] = []

        def post(url, payload, _headers, **_kwargs):
            posted.append((url, payload))
            return {}

        provider._post = post
        provider.load_model("qwen3:8b")
        provider.unload_model("qwen3:8b")
        self.assertEqual(posted[0][0], "http://provider.invalid/api/generate")
        self.assertEqual(posted[0][1]["keep_alive"], -1)
        self.assertEqual(posted[1][1]["keep_alive"], 0)

        provider._get = lambda _url, _headers: {
            "models": [{"name": "qwen3:8b"}]
        }
        self.assertEqual(provider.loaded_models(), frozenset({"qwen3:8b"}))

    def test_generation_cancellation_releases_the_managed_model(self):
        provider = FakeLifecycleProvider()
        manager = ModelLifecycleManager(provider)

        with self.assertRaises(asyncio.CancelledError):
            with manager.generation_lease("qwen3:8b"):
                raise asyncio.CancelledError()

        self.assertNotIn("qwen3:8b", provider.loaded)
        self.assertEqual(manager.snapshot().state, "cancelled")

    def test_generation_failure_is_distinguished_from_cancellation(self):
        provider = FakeLifecycleProvider()
        manager = ModelLifecycleManager(provider)

        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            with manager.generation_lease("qwen3:8b"):
                raise RuntimeError("generation failed")

        self.assertNotIn("qwen3:8b", provider.loaded)
        self.assertEqual(manager.snapshot().state, "failed")

    def test_release_failure_keeps_actual_residency_visible(self):
        provider = FakeLifecycleProvider()
        manager = ModelLifecycleManager(provider)
        manager.activate("qwen3:8b")

        def fail_unload(_model):
            raise ProviderError(
                "provider_model_lifecycle_failed",
                "unload failed",
            )

        provider.unload_model = fail_unload
        with self.assertRaises(ProviderError):
            manager.release()

        snapshot = manager.snapshot()
        self.assertEqual(snapshot.state, "failed")
        self.assertEqual(snapshot.active_model, "qwen3:8b")
        self.assertIn("qwen3:8b", snapshot.loaded_models)

    def test_async_generation_lease_releases_without_blocking_its_caller(self):
        provider = FakeLifecycleProvider()
        manager = ModelLifecycleManager(provider)

        async def run_generation():
            async with manager.async_generation_lease("qwen3:8b"):
                self.assertIn("qwen3:8b", provider.loaded)

        asyncio.run(run_generation())
        self.assertNotIn("qwen3:8b", provider.loaded)
