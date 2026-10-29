import json
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import httpx2

from app.agent.config import AgentSettings, get_agent_settings
from app.agent.contracts import (
    ModelIdentity,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderHealth,
    ProviderError,
    ProviderMessage,
    ToolCall,
    ToolResult,
    UsageMetadata,
)
from app.agent.model_router import AgentModelRouter
from app.agent.providers import OpenAICompatibleProvider, OllamaProvider, create_provider
from app.agent.runtime import SYSTEM_INSTRUCTIONS, execute_agent_request
from app.agent.tools import StrictArguments, ToolDefinition, ToolRegistry


def settings(provider="ollama", model="qwen3:8b", api_key=None):
    return AgentSettings(
        provider=provider,
        base_url="http://provider.invalid",
        model=model,
        api_key=api_key,
        timeout_seconds=1,
        max_output_tokens=64,
        max_model_turns=3,
        max_tool_calls=2,
        max_history_messages=4,
    )


class FakeHttpResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self.body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield self.body


class FakeHttpClient:
    def __init__(self, response, **_kwargs):
        self.response = response
        self.request = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url, json=None, headers=None):
        self.request = (method, url, json, headers)
        return self.response


class EmptyArguments(StrictArguments):
    pass


class SequenceProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return next(self.responses)


class AgentProviderTests(unittest.TestCase):
    def test_explicit_qwen_configuration_selects_ollama_without_an_api_key(self):
        with patch.dict(
            os.environ,
            {
                "INSIGHTOS_LLM_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen3:4b-instruct",
            },
            clear=True,
        ):
            selected = get_agent_settings()

        self.assertEqual(selected.provider, "ollama")
        self.assertEqual(selected.model, "qwen3:4b-instruct")
        self.assertIsNone(selected.api_key)
        self.assertIsInstance(create_provider(selected), OllamaProvider)

    def test_system_instructions_define_finance_and_execution_evidence_rules(self):
        self.assertIn("Answer general questions and standalone arithmetic directly", SYSTEM_INSTRUCTIONS)
        self.assertIn("profit equals revenue minus expenses, not an outflow", SYSTEM_INSTRUCTIONS)
        self.assertIn("If revenue is missing or zero", SYSTEM_INSTRUCTIONS)
        self.assertIn("Check date parsing and numeric values", SYSTEM_INSTRUCTIONS)
        self.assertIn("Group valid records by calendar month", SYSTEM_INSTRUCTIONS)
        self.assertIn("Zero revenue makes margin mathematically undefined", SYSTEM_INSTRUCTIONS)
        self.assertIn("Flag negative revenue for validation or business-policy review", SYSTEM_INSTRUCTIONS)
        self.assertIn("do not automatically classify it as invalid or exclude it without that policy", SYSTEM_INSTRUCTIONS)
        self.assertIn("defer its treatment until the applicable policy is known", SYSTEM_INSTRUCTIONS)
        self.assertIn("Do not claim division by a negative number is mathematically undefined", SYSTEM_INSTRUCTIONS)
        self.assertIn("actual columns cannot be confirmed", SYSTEM_INSTRUCTIONS)
        self.assertIn("customer count, churn count, and time period separately", SYSTEM_INSTRUCTIONS)
        self.assertIn("ask a concise clarification", SYSTEM_INSTRUCTIONS)
        self.assertIn("only when the corresponding tool result is present", SYSTEM_INSTRUCTIONS)

    def test_default_registry_only_advertises_read_only_tools(self):
        registry = ToolRegistry()
        names = {
            tool["function"]["name"]
            for tool in registry.provider_schemas()
        }
        self.assertEqual(
            names,
            {
                "list_projects",
                "list_datasets",
                "list_files",
                "get_profile",
                "get_quality_report",
                "get_inspection_handoff",
                "get_eda_result",
                "list_eda_results",
                "list_schema_analyses",
                "get_schema_analysis",
            },
        )
        analytics_names = {
            tool["function"]["name"]
            for tool in registry.provider_schemas(
                context={
                    "dataset_id": 1,
                    "user_message": "Recommend join keys across my datasets.",
                }
            )
        }
        self.assertIn("recommend_multi_dataset_joins", analytics_names)
        self.assertIn("analyze_multiple_datasets", analytics_names)
        self.assertIn("generate_evidence_report", analytics_names)
        self.assertIn("generate_eda_evidence_report", analytics_names)
        for name in ("run_eda", "analyze_schema"):
            output, result, _label = registry.execute(name, {})
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "unknown_tool")
            self.assertNotIn('"status":"succeeded"', output)
        output, result, _label = registry.execute("run_quality_analysis", {})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "engine_execution_not_authorized")
        self.assertNotIn('"status":"succeeded"', output)

    def test_ollama_response_and_usage_are_parsed(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "message": {"role": "assistant", "content": "Ready."},
                "done_reason": "stop",
                "prompt_eval_count": 4,
                "eval_count": 2,
            })
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            response = OllamaProvider(settings()).complete(
                ModelRequest("fixture-model", (), (), 32)
            )
        self.assertEqual(response.message.content, "Ready.")
        self.assertEqual(response.usage, UsageMetadata(4, 2))
        self.assertEqual(client.request[1], "http://provider.invalid/api/chat")
        self.assertNotIn("think", client.request[2])

    def test_ollama_uses_role_routed_model_for_generation(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "message": {"role": "assistant", "content": "Ready."},
                "done_reason": "stop",
            })
        )
        router = AgentModelRouter(
            settings=settings(model="qwen3:8b"),
            provider=OllamaProvider(settings(model="qwen3:8b")),
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            with patch.dict(
                os.environ,
                {"INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE": "qwen3:4b"},
            ):
                router.complete(
                    "data_intelligence",
                    ModelRequest("ignored", (), (), 32),
                )

        self.assertEqual(client.request[2]["model"], "qwen3:4b")

    def test_ollama_default_model_fallback_reaches_generation_payload(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "message": {"role": "assistant", "content": "Ready."},
                "done_reason": "stop",
            })
        )
        configured = settings(model="qwen3:8b")
        router = AgentModelRouter(
            settings=configured,
            provider=OllamaProvider(configured),
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            with patch.dict(
                os.environ,
                {"INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE": ""},
            ):
                selected = router.complete(
                    "data_intelligence",
                    ModelRequest("ignored", (), (), 32),
                )

        self.assertEqual(selected.model, "qwen3:8b")
        self.assertEqual(client.request[2]["model"], "qwen3:8b")

    def test_ollama_disables_thinking_for_qwen3_models(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "message": {"role": "assistant", "content": "10 + 10 = 20."},
                "done_reason": "stop",
            })
        )
        qwen_settings = settings(model="qwen3:0.6b")
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            OllamaProvider(qwen_settings).complete(
                ModelRequest("qwen3:0.6b", (), (), 32)
            )

        self.assertIs(client.request[2]["think"], False)

    def test_ollama_empty_and_thinking_only_responses_are_rejected(self):
        responses = (
            {"message": {"role": "assistant", "content": ""}},
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "thinking": "private reasoning",
                },
            },
        )
        for payload in responses:
            with self.subTest(has_thinking="thinking" in payload["message"]):
                client = FakeHttpClient(FakeHttpResponse(payload))
                with patch(
                    "app.agent.providers.httpx2.Client",
                    return_value=client,
                ), self.assertRaises(ProviderError) as raised:
                    OllamaProvider(settings()).complete(
                        ModelRequest("fixture-model", (), (), 32)
                    )
                self.assertEqual(
                    raised.exception.code,
                    "provider_response_invalid",
                )
                self.assertNotIn("private reasoning", raised.exception.message)

    def test_ollama_thinking_is_preserved_separately_from_user_facing_text(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "message": {
                    "role": "assistant",
                    "content": "20",
                    "thinking": "private reasoning",
                },
                "done_reason": "stop",
            })
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            response = OllamaProvider(settings()).complete(
                ModelRequest("fixture-model", (), (), 32)
            )

        self.assertEqual(response.message.content, "20")
        self.assertEqual(response.message.thinking, "private reasoning")

    def test_ollama_generation_errors_and_missing_models_have_distinct_codes(self):
        failures = (
            (
                FakeHttpResponse({"error": "private model loading detail"}),
                "provider_inference_failed",
            ),
            (
                FakeHttpResponse(b'{"error":"private not found"}', status_code=404),
                "provider_model_unavailable",
            ),
            (
                FakeHttpResponse(b'{"error":"private memory detail"}', status_code=500),
                "provider_inference_failed",
            ),
        )
        for response, expected_code in failures:
            with self.subTest(expected_code=expected_code):
                client = FakeHttpClient(response)
                with patch(
                    "app.agent.providers.httpx2.Client",
                    return_value=client,
                ), self.assertRaises(ProviderError) as raised:
                    OllamaProvider(settings()).complete(
                        ModelRequest("fixture-model", (), (), 32)
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn("private", raised.exception.message)

    def test_ollama_tool_call_is_parsed(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "list_projects",
                            "arguments": {},
                        },
                    }],
                },
                "done_reason": "tool_calls",
            })
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            response = OllamaProvider(settings()).complete(
                ModelRequest(
                    "fixture-model",
                    (),
                    ({"type": "function", "function": {"name": "list_projects"}},),
                    32,
                )
            )
        self.assertEqual(response.message.tool_calls[0].name, "list_projects")
        self.assertEqual(response.finish_reason, "tool_calls")

    def test_ollama_tool_exchange_uses_native_argument_objects(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "message": {"role": "assistant", "content": "Done."},
                "done_reason": "stop",
            })
        )
        messages = (
            ProviderMessage(
                role="assistant",
                content="",
                tool_calls=(
                    ToolCall("call-1", "list_projects", {"limit": 5}),
                ),
            ),
            ProviderMessage(
                role="tool",
                content='{"status":"succeeded"}',
                tool_call_id="call-1",
                name="list_projects",
            ),
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            OllamaProvider(settings()).complete(
                ModelRequest("fixture-model", messages, (), 32)
            )

        self.assertEqual(
            client.request[2]["messages"],
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "list_projects",
                            "arguments": {"limit": 5},
                        },
                    }],
                },
                {"role": "tool", "content": '{"status":"succeeded"}'},
            ],
        )

    def test_openai_compatible_tool_call_is_parsed(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call-1",
                            "function": {
                                "name": "list_projects",
                                "arguments": "{}",
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            })
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            response = OpenAICompatibleProvider(
                settings("openai_compatible", api_key="fixture-secret")
            ).complete(ModelRequest("fixture-model", (), (), 32))
        self.assertEqual(response.message.tool_calls[0].name, "list_projects")
        self.assertEqual(response.message.tool_calls[0].arguments, {})
        self.assertEqual(response.usage, UsageMetadata(5, 3))
        self.assertEqual(client.request[3]["Authorization"], "Bearer fixture-secret")

    def test_openai_compatible_uses_role_routed_model_for_generation(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "choices": [{
                    "message": {"role": "assistant", "content": "Ready."},
                    "finish_reason": "stop",
                }],
            })
        )
        configured = settings(
            "openai_compatible",
            model="default-model",
            api_key="fixture-secret",
        )
        router = AgentModelRouter(
            settings=configured,
            provider=OpenAICompatibleProvider(configured),
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            with patch.dict(
                os.environ,
                {"INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE": "role-specific-model"},
            ):
                router.complete(
                    "data_intelligence",
                    ModelRequest("ignored", (), (), 32),
                )

        self.assertEqual(client.request[2]["model"], "role-specific-model")

    def test_openai_default_model_fallback_reaches_generation_payload(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "choices": [{
                    "message": {"role": "assistant", "content": "Ready."},
                    "finish_reason": "stop",
                }],
            })
        )
        configured = settings(
            "openai_compatible",
            model="default-model",
            api_key="fixture-secret",
        )
        router = AgentModelRouter(
            settings=configured,
            provider=OpenAICompatibleProvider(configured),
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            with patch.dict(
                os.environ,
                {"INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE": ""},
            ):
                selected = router.complete(
                    "data_intelligence",
                    ModelRequest("ignored", (), (), 32),
                )

        self.assertEqual(selected.model, "default-model")
        self.assertEqual(client.request[2]["model"], "default-model")

    def test_openai_json_mode_is_forwarded_to_provider(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "choices": [{
                    "message": {"role": "assistant", "content": '{"ok":true}'},
                    "finish_reason": "stop",
                }],
            })
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            OpenAICompatibleProvider(
                settings("openai_compatible", api_key="fixture-secret")
            ).complete(
                ModelRequest(
                    "fixture-model",
                    (),
                    (),
                    32,
                    response_format="json",
                )
            )
        self.assertEqual(
            client.request[2]["response_format"],
            {"type": "json_object"},
        )

    def test_ollama_json_mode_is_forwarded_to_provider(self):
        client = FakeHttpClient(
            FakeHttpResponse({
                "message": {"role": "assistant", "content": '{"ok":true}'},
                "done_reason": "stop",
            })
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            OllamaProvider(settings()).complete(
                ModelRequest(
                    "fixture-model",
                    (),
                    (),
                    32,
                    response_format="json",
                )
            )
        self.assertEqual(client.request[2]["format"], "json")

    def test_ollama_health_requires_configured_model_in_runtime_model_list(self):
        available_client = FakeHttpClient(
            FakeHttpResponse({
                "models": [{
                    "name": "qwen3:8b",
                    "digest": "fixture-digest",
                }],
            })
        )
        version_client = FakeHttpClient(
            FakeHttpResponse({"version": "0.13.0"})
        )
        with patch(
            "app.agent.providers.httpx2.Client",
            side_effect=[available_client, version_client],
        ):
            health = OllamaProvider(settings()).check_health()
        self.assertEqual(
            health,
            ProviderHealth(
                status="healthy",
                runtime_version="0.13.0",
                model_identity=ModelIdentity(
                    provider="ollama",
                    model="qwen3:8b",
                    digest="fixture-digest",
                ),
                capabilities=OllamaProvider.capabilities,
            ),
        )
        self.assertEqual(available_client.request[:2], ("GET", "http://provider.invalid/api/tags"))
        self.assertEqual(version_client.request[:2], ("GET", "http://provider.invalid/api/version"))

        missing_client = FakeHttpClient(FakeHttpResponse({"models": []}))
        with patch(
            "app.agent.providers.httpx2.Client",
            return_value=missing_client,
        ):
            health = OllamaProvider(settings()).check_health()
        self.assertEqual(health.status, "unavailable")

    def test_openai_health_uses_authenticated_model_list(self):
        client = FakeHttpClient(
            FakeHttpResponse({"data": [{"id": "qwen3:8b"}]})
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            health = OpenAICompatibleProvider(
                settings("openai_compatible", api_key="fixture-secret")
            ).check_health()
        self.assertEqual(
            health,
            ProviderHealth(
                status="healthy",
                model_identity=ModelIdentity(
                    provider="openai_compatible",
                    model="qwen3:8b",
                ),
                capabilities=OpenAICompatibleProvider.capabilities,
            ),
        )
        self.assertEqual(client.request[:2], ("GET", "http://provider.invalid/models"))
        self.assertEqual(client.request[3]["Authorization"], "Bearer fixture-secret")

    def test_openai_compatible_adapter_rejects_openai_hosted_endpoints_before_network(self):
        for base_url in (
            "https://api.openai.com/v1",
            "https://chat.openai.com/v1",
            "https://api.openai.com./v1",
        ):
            with self.subTest(base_url=base_url), patch(
                "app.agent.providers.httpx2.Client"
            ) as client:
                provider_settings = replace(
                    settings("openai_compatible", api_key="fixture-secret"),
                    base_url=base_url,
                )
                with self.assertRaises(ProviderError) as raised:
                    OpenAICompatibleProvider(provider_settings).check_health()
                self.assertEqual(
                    raised.exception.code,
                    "provider_configuration_invalid",
                )
                client.assert_not_called()

    def test_provider_rejects_unsupported_requested_capabilities(self):
        provider = OllamaProvider(settings())
        provider.capabilities = ProviderCapabilities(
            text_generation="supported",
            tool_calling="unsupported",
            structured_output="unsupported",
            streaming="unsupported",
            local_inference="supported",
        )
        with patch("app.agent.providers.httpx2.Client") as client:
            with self.assertRaises(ProviderError) as tool_error:
                provider.complete(
                    ModelRequest("fixture-model", (), ({"function": {}},), 32)
                )
            with self.assertRaises(ProviderError) as json_error:
                provider.complete(
                    ModelRequest(
                        "fixture-model",
                        (),
                        (),
                        32,
                        response_format="json",
                    )
                )
        self.assertEqual(tool_error.exception.code, "provider_capability_unsupported")
        self.assertEqual(json_error.exception.code, "provider_capability_unsupported")
        client.assert_not_called()

    def test_provider_sanitizes_timeout_and_connection_failures(self):
        failures = (
            (httpx2.TimeoutException("private timeout"), "provider_timeout"),
            (httpx2.ConnectError("private address"), "provider_unavailable"),
        )
        for exception, expected_code in failures:
            with self.subTest(expected_code=expected_code):
                with patch(
                    "app.agent.providers.httpx2.Client",
                    side_effect=exception,
                ):
                    with self.assertRaises(ProviderError) as raised:
                        OllamaProvider(settings()).complete(
                            ModelRequest("fixture-model", (), (), 32)
                        )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn("private", raised.exception.message)

    def test_provider_rejects_malformed_json_responses(self):
        client = FakeHttpClient(FakeHttpResponse(b"not JSON"))
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            with self.assertRaises(ProviderError) as raised:
                OllamaProvider(settings()).complete(
                    ModelRequest("fixture-model", (), (), 32)
                )
        self.assertEqual(raised.exception.code, "provider_response_invalid")

    def test_provider_error_does_not_include_raw_payload_or_credentials(self):
        client = FakeHttpClient(
            FakeHttpResponse({"secret": "provider response"}, status_code=401)
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            with self.assertRaises(ProviderError) as raised:
                OpenAICompatibleProvider(
                    settings("openai_compatible", api_key="fixture-secret")
                ).complete(ModelRequest("fixture-model", (), (), 32))
        self.assertEqual(raised.exception.code, "provider_authentication_failed")
        self.assertNotIn("fixture-secret", raised.exception.message)
        self.assertNotIn("provider response", raised.exception.message)

    def test_provider_rejects_oversized_response_before_parsing(self):
        client = FakeHttpClient(
            FakeHttpResponse(b" " * (1024 * 1024 + 1))
        )
        with patch("app.agent.providers.httpx2.Client", return_value=client):
            with self.assertRaises(ProviderError) as raised:
                OllamaProvider(settings()).complete(
                    ModelRequest("fixture-model", (), (), 32)
                )
        self.assertEqual(raised.exception.code, "provider_response_too_large")

    def test_runtime_returns_final_answer_without_tools(self):
        provider = SequenceProvider([
            ModelResponse(
                ProviderMessage(role="assistant", content="Here is the summary."),
                finish_reason="stop",
            )
        ])
        outcome = execute_agent_request(
            user_message="Explain a short story",
            history=[],
            settings=settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, "Here is the summary.")
        self.assertEqual(outcome.activities, ())

    def test_multi_dataset_recommendation_runs_through_workflow_scheduler(self):
        arguments = {
            "sources": [
                {"alias": "selected", "dataset_id": 1, "file_id": 11},
                {"alias": "other", "dataset_id": 2, "file_id": 22},
            ]
        }
        analysis_arguments = {
            **arguments,
            "joins": [
                {
                    "left_key": {"alias": "selected", "column": "id"},
                    "right_key": {"alias": "other", "column": "id"},
                    "join_type": "inner",
                    "allow_many_to_many": False,
                }
            ],
            "dimensions": [{"alias": "selected", "column": "id"}],
            "metrics": [{"name": "rows", "operation": "count", "column": None}],
        }
        provider = SequenceProvider(
            [
                ModelResponse(
                    ProviderMessage(
                        role="assistant",
                        content="",
                        tool_calls=(
                            ToolCall(
                                "recommendation-call",
                                "recommend_multi_dataset_joins",
                                arguments,
                            ),
                        ),
                    ),
                    finish_reason="tool_calls",
                ),
                ModelResponse(
                    ProviderMessage(
                        role="assistant",
                        content="",
                        tool_calls=(
                            ToolCall(
                                "analysis-call",
                                "analyze_multiple_datasets",
                                analysis_arguments,
                            ),
                        ),
                    ),
                    finish_reason="tool_calls",
                ),
                ModelResponse(
                    ProviderMessage(
                        role="assistant",
                        content="I found candidate keys. Review the evidence before approving a join.",
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        scheduled = []

        def scheduler(name, tool_arguments, execute, **kwargs):
            scheduled.append((name, tool_arguments, kwargs))
            raw, result, label = execute()
            return raw, result, label, "workflow-task-1"

        from app.main import MultiDatasetRecommendationRequest
        from app.main import MultiDatasetAnalysisRequest

        fake_main = SimpleNamespace(
            MultiDatasetRecommendationRequest=MultiDatasetRecommendationRequest,
            MultiDatasetAnalysisRequest=MultiDatasetAnalysisRequest,
            recommend_multi_dataset_relationships=lambda request: {
                "recommendations": [
                    {
                        "left": {"alias": "selected", "column": "id"},
                        "right": {"alias": "other", "column": "id"},
                        "confidence": 0.9,
                    }
                ]
            },
            analyze_multiple_datasets=lambda request: {
                "input_sources": [
                    {
                        "alias": "selected",
                        "dataset_id": 1,
                        "file_id": 11,
                        "table_index": 0,
                        "source_sha256": "a" * 64,
                    },
                    {
                        "alias": "other",
                        "dataset_id": 2,
                        "file_id": 22,
                        "table_index": 0,
                        "source_sha256": "b" * 64,
                    },
                ],
                "execution_fingerprint": "c" * 64,
                "joined_row_count": 2,
                "results": [{"selected.id": 1, "rows": 1}],
            },
        )
        with patch("app.agent.tools._main", return_value=fake_main):
            outcome = execute_agent_request(
                user_message="Recommend join keys between my selected datasets.",
                history=[],
                context={"dataset_id": 1},
                settings=settings(),
                provider=provider,
                workflow_scheduler=scheduler,
            )

        self.assertIn(outcome.status, {"completed", "partial"})
        self.assertEqual(
            [item[0] for item in scheduled],
            [
                "recommend_multi_dataset_joins",
                "analyze_multiple_datasets",
            ],
        )
        self.assertTrue(all(item[2]["idempotent"] for item in scheduled))
        self.assertEqual(
            [activity.status for activity in outcome.activities],
            ["succeeded", "succeeded"],
        )

    def test_evidence_report_runs_through_workflow_scheduler(self):
        report_arguments = {
            "sources": [
                {"alias": "selected", "dataset_id": 1, "file_id": 11},
                {"alias": "other", "dataset_id": 2, "file_id": 22},
            ],
            "joins": [
                {
                    "left_key": {"alias": "selected", "column": "id"},
                    "right_key": {"alias": "other", "column": "id"},
                    "join_type": "inner",
                }
            ],
            "dimensions": [{"alias": "selected", "column": "id"}],
            "metrics": [{"name": "rows", "operation": "count"}],
            "charts": [
                {
                    "title": "Rows by id",
                    "chart_type": "bar",
                    "x_field": "selected.id",
                    "y_field": "rows",
                }
            ],
        }
        provider = SequenceProvider([
            ModelResponse(
                ProviderMessage(
                    role="assistant",
                    content="",
                    tool_calls=(
                        ToolCall(
                            "report-call",
                            "generate_evidence_report",
                            report_arguments,
                        ),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            ModelResponse(
                ProviderMessage(role="assistant", content="The report is ready."),
                finish_reason="stop",
            ),
        ])
        scheduled = []

        def scheduler(name, tool_arguments, execute, **kwargs):
            scheduled.append((name, tool_arguments, kwargs))
            raw, result, label = execute()
            return raw, result, label, "report-task-1"

        from app.main import MultiDatasetReportRequest

        fake_main = SimpleNamespace(
            MultiDatasetReportRequest=MultiDatasetReportRequest,
            generate_analytics_report=lambda request: {
                "findings": [{"kind": "measured_fact", "supporting_metric": {"value": 1}}],
                "charts": [{"title": "Rows by id", "data": {"labels": ["1"]}}],
                "input_sources": [
                    {
                        "alias": "selected",
                        "dataset_id": 1,
                        "file_id": 11,
                        "table_index": 0,
                        "source_sha256": "a" * 64,
                    },
                    {
                        "alias": "other",
                        "dataset_id": 2,
                        "file_id": 22,
                        "table_index": 0,
                        "source_sha256": "b" * 64,
                    },
                ],
            },
        )
        with patch("app.agent.tools._main", return_value=fake_main):
            outcome = execute_agent_request(
                user_message="Create an evidence-backed dashboard report.",
                history=[],
                context={"dataset_id": 1},
                settings=settings(),
                provider=provider,
                workflow_scheduler=scheduler,
            )

        self.assertIn(outcome.status, {"completed", "partial"})
        self.assertEqual([item[0] for item in scheduled], ["generate_evidence_report"])
        self.assertTrue(scheduled[0][2]["idempotent"])
        self.assertEqual([activity.status for activity in outcome.activities], ["succeeded"])

    def test_data_response_without_structured_claims_is_not_presented(self):
        provider = SequenceProvider([
            ModelResponse(
                ProviderMessage(
                    role="assistant",
                    content="The dataset contains 999 rows.",
                ),
                finish_reason="stop",
            )
        ])
        outcome = execute_agent_request(
            user_message="Summarize my data",
            history=[],
            settings=settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "partial")
        self.assertIn("couldn't validate", outcome.assistant_text)
        self.assertNotIn("999", outcome.assistant_text)
        self.assertEqual(
            outcome.structured_results[-1]["data"]["claims"][0]["reason_code"],
            "claim_envelope_invalid",
        )

    def test_runtime_passes_arithmetic_prompt_to_provider_and_returns_its_answer(self):
        provider = SequenceProvider([
            ModelResponse(
                ProviderMessage(role="assistant", content="20"),
                finish_reason="stop",
            )
        ])
        outcome = execute_agent_request(
            user_message="10+10",
            history=[],
            settings=settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, "20")
        self.assertEqual(provider.requests[0].messages[-1].content, "10+10")

    def test_runtime_rejects_empty_responses_without_claiming_success(self):
        provider = SequenceProvider([
            ModelResponse(
                ProviderMessage(
                    role="assistant",
                    content="",
                    thinking="private reasoning",
                ),
                finish_reason="stop",
            )
        ])
        outcome = execute_agent_request(
            user_message="10+10",
            history=[],
            settings=settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "provider_response_invalid")
        self.assertIn("returned an empty response", outcome.assistant_text)
        self.assertNotIn("private reasoning", outcome.assistant_text)
        self.assertEqual(len(provider.requests), 1)

    def test_runtime_classifies_invalid_provider_response_as_failed(self):
        class InvalidResponseProvider:
            def complete(self, _request):
                raise ProviderError(
                    "provider_response_invalid",
                    "The configured model returned an empty response.",
                )

        outcome = execute_agent_request(
            user_message="10+10",
            history=[],
            settings=settings(),
            provider=InvalidResponseProvider(),
        )

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "provider_response_invalid")
        self.assertIn("returned an empty response", outcome.assistant_text)

    def test_runtime_marks_truncated_output_as_partial(self):
        provider = SequenceProvider([
            ModelResponse(
                ProviderMessage(role="assistant", content="The answer is"),
                finish_reason="length",
            )
        ])
        outcome = execute_agent_request(
            user_message="Summarize the result",
            history=[],
            settings=settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.error_code, "response_truncated")
        self.assertEqual(outcome.assistant_text, "The answer is")

    def _run_list_projects_tool(
        self,
        projects,
        *,
        user_message="List projects",
        context=None,
        final_text,
    ):
        executed = []

        def list_projects(_args):
            executed.append(True)
            return ToolResult(
                status="succeeded",
                summary=f"Found {len(projects)} project(s).",
                data=projects,
            )

        registry = ToolRegistry((
            ToolDefinition(
                name="list_projects",
                label="Finding projects",
                description="List projects.",
                arguments_model=EmptyArguments,
                execute=list_projects,
            ),
        ))
        provider = SequenceProvider([
            ModelResponse(
                ProviderMessage(
                    role="assistant",
                    content="",
                    tool_calls=(ToolCall("call-1", "list_projects", {}),),
                ),
                finish_reason="tool_calls",
            ),
            ModelResponse(
                ProviderMessage(role="assistant", content=final_text),
                finish_reason="stop",
            ),
        ])
        outcome = execute_agent_request(
            user_message=user_message,
            history=[],
            settings=settings(),
            provider=provider,
            registry=registry,
            context=context,
        )

        self.assertEqual(len(executed), 1)
        self.assertEqual(len(outcome.activities), 1)
        self.assertEqual(outcome.activities[0].tool_name, "list_projects")
        self.assertEqual(outcome.activities[0].status, "succeeded")
        self.assertEqual(
            outcome.structured_results[0]["data"],
            projects,
        )
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(provider.requests[1].messages[-1].role, "tool")
        return outcome, provider

    def test_runtime_finalizes_successful_project_listing_once(self):
        projects = [
            {"id": 1, "name": "Project One"},
            {"id": 2, "name": "Project Two"},
        ]
        outcome, provider = self._run_list_projects_tool(
            projects,
            final_text="The available projects are Project One and Project Two.",
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(
            outcome.assistant_text,
            "The available projects are Project One and Project Two.",
        )
        self.assertIsNone(provider.requests[1].response_format)
        self.assertFalse(
            any(
                result.get("tool") == "claim_validator"
                for result in outcome.structured_results
            )
        )

    def test_runtime_finalizes_empty_project_listing_truthfully(self):
        outcome, _provider = self._run_list_projects_tool(
            [],
            final_text="There are no projects available.",
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(
            outcome.assistant_text,
            "There are no projects available.",
        )

    def test_dataset_numerical_claim_after_read_only_tool_still_requires_evidence(self):
        outcome, provider = self._run_list_projects_tool(
            [{"id": 1, "name": "Project One"}],
            user_message="How many rows are in the selected dataset?",
            context={"dataset_id": 1},
            final_text="The selected dataset has 999 rows.",
        )

        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.error_code, "claim_validation_incomplete")
        self.assertIn("couldn't validate", outcome.assistant_text)
        self.assertNotIn("999", outcome.assistant_text)
        self.assertEqual(provider.requests[0].response_format, "json")
        self.assertEqual(provider.requests[1].response_format, "json")
        self.assertEqual(outcome.structured_results[-1]["tool"], "claim_validator")
        self.assertEqual(
            outcome.structured_results[-1]["data"]["claims"][0]["reason_code"],
            "claim_envelope_invalid",
        )

    def test_no_provider_returns_explicit_unavailable_outcome(self):
        outcome = execute_agent_request(
            user_message="Hello",
            history=[],
            settings=AgentSettings(
                provider="none",
                base_url=None,
                model=None,
                api_key=None,
                timeout_seconds=1,
                max_output_tokens=32,
                max_model_turns=1,
                max_tool_calls=1,
                max_history_messages=1,
            ),
        )
        self.assertEqual(outcome.status, "provider_unavailable")
        self.assertEqual(outcome.error_code, "provider_not_configured")


if __name__ == "__main__":
    unittest.main()
