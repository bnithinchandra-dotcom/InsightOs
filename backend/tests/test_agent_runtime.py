import json
import unittest
from unittest.mock import patch

from app.agent.config import AgentSettings
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderMessage,
    ToolCall,
    ToolResult,
    UsageMetadata,
)
from app.agent.providers import OpenAICompatibleProvider, OllamaProvider
from app.agent.runtime import execute_agent_request
from app.agent.tools import StrictArguments, ToolDefinition, ToolRegistry


def settings(provider="ollama", model="fixture-model", api_key=None):
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

    def stream(self, method, url, json, headers):
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
                "get_eda_result",
                "list_eda_results",
                "list_schema_analyses",
                "get_schema_analysis",
            },
        )
        for name in ("run_quality_analysis", "run_eda", "analyze_schema"):
            output, result, _label = registry.execute(name, {})
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "unknown_tool")
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
            user_message="Summarize my data",
            history=[],
            settings=settings(),
            provider=provider,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.assistant_text, "Here is the summary.")
        self.assertEqual(outcome.activities, ())

    def test_runtime_executes_allowlisted_tool_then_returns_answer(self):
        class Registry(ToolRegistry):
            def __init__(self):
                super().__init__((
                    ToolDefinition(
                        name="list_projects",
                        label="Finding projects",
                        description="List projects.",
                        arguments_model=EmptyArguments,
                        execute=lambda _args: ToolResult(
                            status="succeeded",
                            summary="Found one project.",
                            data=[{"id": 1, "name": "Project"}],
                        ),
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
                ProviderMessage(role="assistant", content="There is one project."),
                finish_reason="stop",
            ),
        ])
        outcome = execute_agent_request(
            user_message="List projects",
            history=[],
            settings=settings(),
            provider=provider,
            registry=Registry(),
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(len(outcome.activities), 1)
        self.assertEqual(outcome.structured_results[0]["data"], [{"id": 1, "name": "Project"}])
        self.assertEqual(provider.requests[1].messages[-1].role, "tool")

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
