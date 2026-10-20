from __future__ import annotations

import json
from typing import Any, Protocol

import httpx2

from app.agent.config import AgentSettings
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderMessage,
    ToolCall,
    UsageMetadata,
)


MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024


class ModelProvider(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


def _tool_call_id(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value and len(value) <= 128:
        return value
    return fallback


def _parse_tool_calls(raw_calls: Any, *, openai_format: bool) -> tuple[ToolCall, ...]:
    if raw_calls is None:
        return ()
    if not isinstance(raw_calls, list) or len(raw_calls) > 32:
        raise ProviderError(
            "provider_response_invalid",
            "The configured model returned an unsupported tool-call response.",
        )
    parsed = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an unsupported tool-call response.",
            )
        if openai_format:
            function = raw_call.get("function")
            arguments = function.get("arguments") if isinstance(function, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
        else:
            function = raw_call.get("function")
            arguments = function.get("arguments") if isinstance(function, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
        if isinstance(arguments, str):
            if len(arguments) > 32 * 1024:
                raise ProviderError(
                    "provider_response_invalid",
                    "The configured model returned an oversized tool request.",
                )
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                raise ProviderError(
                    "provider_response_invalid",
                    "The configured model returned malformed tool arguments.",
                ) from None
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an unsupported tool-call response.",
            )
        parsed.append(
            ToolCall(
                id=_tool_call_id(raw_call.get("id"), f"tool-{index + 1}"),
                name=name,
                arguments=arguments,
            )
        )
    return tuple(parsed)


def _usage(raw_usage: Any, *, input_key: str, output_key: str) -> UsageMetadata:
    if not isinstance(raw_usage, dict):
        return UsageMetadata()
    input_tokens = raw_usage.get(input_key)
    output_tokens = raw_usage.get(output_key)
    return UsageMetadata(
        input_tokens=(
            input_tokens
            if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0
            else None
        ),
        output_tokens=(
            output_tokens
            if isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens >= 0
            else None
        ),
    )


def _provider_message(raw_message: Any, *, openai_format: bool) -> ProviderMessage:
    if not isinstance(raw_message, dict) or raw_message.get("role") != "assistant":
        raise ProviderError(
            "provider_response_invalid",
            "The configured model returned an invalid response.",
        )
    content = raw_message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str) or len(content) > 64 * 1024:
        raise ProviderError(
            "provider_response_invalid",
            "The configured model returned an invalid response.",
        )
    raw_calls = raw_message.get("tool_calls")
    if openai_format and isinstance(raw_calls, list):
        raw_calls = [
            {
                "id": item.get("id"),
                "function": item.get("function"),
            }
            if isinstance(item, dict)
            else item
            for item in raw_calls
        ]
    return ProviderMessage(
        role="assistant",
        content=content,
        tool_calls=_parse_tool_calls(raw_calls, openai_format=openai_format),
    )


def _message_dict(message: ProviderMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id:
        result["tool_call_id"] = message.tool_call_id
    if message.name:
        result["name"] = message.name
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        call.arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }
            for call in message.tool_calls
        ]
    return result


class _HttpProvider:
    def __init__(self, settings: AgentSettings, *, transport=None):
        self.settings = settings
        self.transport = transport

    def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        try:
            with httpx2.Client(
                timeout=self.settings.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status_code == 429:
                        raise ProviderError(
                            "provider_rate_limited",
                            "The configured model provider is rate limiting requests.",
                            retryable=True,
                        )
                    if response.status_code in {401, 403}:
                        raise ProviderError(
                            "provider_authentication_failed",
                            "The configured model provider rejected its credentials.",
                        )
                    if response.status_code >= 500:
                        raise ProviderError(
                            "provider_unavailable",
                            "The configured model provider is unavailable.",
                            retryable=True,
                        )
                    if not response.is_success:
                        raise ProviderError(
                            "provider_request_rejected",
                            "The configured model provider rejected the request.",
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(body) + len(chunk) > MAX_PROVIDER_RESPONSE_BYTES:
                            raise ProviderError(
                                "provider_response_too_large",
                                "The configured model returned a response that exceeded the safe size limit.",
                            )
                        body.extend(chunk)
        except httpx2.TimeoutException:
            raise ProviderError(
                "provider_timeout",
                "The configured model did not respond before the request timed out.",
                retryable=True,
            ) from None
        except httpx2.RequestError:
            raise ProviderError(
                "provider_unavailable",
                "The configured model provider is unavailable.",
                retryable=True,
            ) from None
        try:
            payload_result = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an invalid response.",
            ) from None
        if not isinstance(payload_result, dict):
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an invalid response.",
            )
        return payload_result


class OllamaProvider(_HttpProvider):
    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.settings.model:
            raise ProviderError(
                "provider_not_configured",
                "Configure OLLAMA_MODEL before using the local model provider.",
            )
        payload = self._post(
            f"{self.settings.base_url}/api/chat",
            {
                "model": self.settings.model,
                "messages": [_message_dict(message) for message in request.messages],
                "tools": list(request.tools),
                "stream": False,
                "options": {"num_predict": request.max_output_tokens},
            },
            {},
        )
        message = _provider_message(payload.get("message"), openai_format=False)
        if not message.tool_calls and not message.content.strip():
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an empty response.",
            )
        return ModelResponse(
            message=message,
            finish_reason=str(payload.get("done_reason") or "stop"),
            usage=_usage(
                payload,
                input_key="prompt_eval_count",
                output_key="eval_count",
            ),
        )


class OpenAICompatibleProvider(_HttpProvider):
    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.settings.model or not self.settings.api_key:
            raise ProviderError(
                "provider_not_configured",
                "Configure OPENAI_COMPATIBLE_MODEL and OPENAI_COMPATIBLE_API_KEY before using this provider.",
            )
        base_url = self.settings.base_url
        if base_url is None:
            raise ProviderError(
                "provider_configuration_invalid",
                "OPENAI_COMPATIBLE_BASE_URL must be configured.",
            )
        endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url.rstrip('/')}/chat/completions"
        payload = self._post(
            endpoint,
            {
                "model": self.settings.model,
                "messages": [_message_dict(message) for message in request.messages],
                "tools": list(request.tools),
                "tool_choice": "auto",
                "max_tokens": request.max_output_tokens,
            },
            {"Authorization": f"Bearer {self.settings.api_key}"},
        )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an invalid response.",
            )
        choice = choices[0]
        message = _provider_message(choice.get("message"), openai_format=True)
        if not message.tool_calls and not message.content.strip():
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an empty response.",
            )
        return ModelResponse(
            message=message,
            finish_reason=str(choice.get("finish_reason") or "stop"),
            usage=_usage(
                payload.get("usage"),
                input_key="prompt_tokens",
                output_key="completion_tokens",
            ),
        )


def create_provider(settings: AgentSettings, *, transport=None) -> ModelProvider:
    if settings.provider == "ollama":
        return OllamaProvider(settings, transport=transport)
    if settings.provider == "openai_compatible":
        return OpenAICompatibleProvider(settings, transport=transport)
    raise ProviderError(
        "provider_not_configured",
        "No model provider is configured. Configure Ollama or an OpenAI-compatible provider to use the assistant.",
    )
