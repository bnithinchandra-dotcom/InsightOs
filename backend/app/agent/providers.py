from __future__ import annotations

import json
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx2

from app.agent.config import AgentSettings
from app.agent.contracts import (
    ModelIdentity,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderHealth,
    ProviderError,
    ProviderMessage,
    RequestTelemetry,
    ToolCall,
    UsageMetadata,
)


MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024


class ModelProvider(Protocol):
    capabilities: ProviderCapabilities

    def complete(self, request: ModelRequest) -> ModelResponse: ...

    def check_health(self) -> ProviderHealth: ...


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


def _usage(raw_usage: Any, *, input_key: str, output_key: str, cached_key: str | None = None, reasoning_key: str | None = None) -> UsageMetadata:
    if not isinstance(raw_usage, dict):
        return UsageMetadata()
    input_tokens = raw_usage.get(input_key)
    output_tokens = raw_usage.get(output_key)
    cached_tokens = raw_usage.get(cached_key) if cached_key else None
    reasoning_tokens = raw_usage.get(reasoning_key) if reasoning_key else None
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
        cached_tokens=(
            cached_tokens
            if isinstance(cached_tokens, int) and not isinstance(cached_tokens, bool) and cached_tokens >= 0
            else None
        ),
        reasoning_tokens=(
            reasoning_tokens
            if isinstance(reasoning_tokens, int) and not isinstance(reasoning_tokens, bool) and reasoning_tokens >= 0
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
    thinking = raw_message.get("thinking") if not openai_format else None
    if thinking is not None and (
        not isinstance(thinking, str) or len(thinking) > 64 * 1024
    ):
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
        thinking=thinking,
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


def _ollama_message_dict(message: ProviderMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.thinking:
        result["thinking"] = message.thinking
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "function": {
                    "name": call.name,
                    "arguments": call.arguments,
                },
            }
            for call in message.tool_calls
        ]
    return result


class _HttpProvider:
    capabilities = ProviderCapabilities(
        text_generation="supported",
        tool_calling="supported",
        structured_output="supported",
        streaming="unsupported",
        local_inference="unverified",
    )

    def __init__(self, settings: AgentSettings, *, transport=None):
        self.settings = settings
        self.transport = transport

    def _validate_request_capabilities(self, request: ModelRequest) -> None:
        if request.tools and self.capabilities.tool_calling != "supported":
            raise ProviderError(
                "provider_capability_unsupported",
                "The selected model provider does not support the requested tool-calling feature.",
            )
        if (
            request.response_format is not None
            and self.capabilities.structured_output != "supported"
        ):
            raise ProviderError(
                "provider_capability_unsupported",
                "The selected model provider does not support the requested structured-output feature.",
            )

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        try:
            with httpx2.Client(
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else self.settings.timeout_seconds
                ),
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                request_options: dict[str, Any] = {"headers": headers}
                if payload is not None:
                    request_options["json"] = payload
                with client.stream(method, url, **request_options) as response:
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
                    if (
                        response.status_code == 404
                        and self.settings.provider == "ollama"
                        and url.endswith("/api/chat")
                    ):
                        raise ProviderError(
                            "provider_model_unavailable",
                            "The configured Ollama model is unavailable.",
                        )
                    if response.status_code >= 500:
                        if (
                            self.settings.provider == "ollama"
                            and url.endswith("/api/chat")
                        ):
                            raise ProviderError(
                                "provider_inference_failed",
                                "Ollama could not generate a response with the configured model.",
                            )
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

    def _post(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            url,
            headers,
            payload,
            timeout_seconds=timeout_seconds,
        )

    def _post_for_request(
        self,
        request: ModelRequest,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        return self._post(
            url,
            payload,
            headers,
            timeout_seconds=request.timeout_seconds,
        )

    def _get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        return self._request("GET", url, headers)


class OllamaProvider(_HttpProvider):
    capabilities = ProviderCapabilities(
        text_generation="supported",
        tool_calling="supported",
        structured_output="supported",
        streaming="unsupported",
        local_inference="supported",
    )

    def check_health(self) -> ProviderHealth:
        if not self.settings.model:
            raise ProviderError(
                "provider_not_configured",
                "Configure OLLAMA_MODEL before using the local model provider.",
            )
        payload = self._get(f"{self.settings.base_url}/api/tags", {})
        models = payload.get("models")
        if not isinstance(models, list):
            raise ProviderError(
                "provider_response_invalid",
                "The configured model provider returned an invalid model list.",
            )
        model_info = next(
            (
                item
                for item in models
                if isinstance(item, dict)
                and self.settings.model in {item.get("name"), item.get("model")}
            ),
            None,
        )
        if model_info is None:
            return ProviderHealth(status="unavailable")
        version_response = self._get(f"{self.settings.base_url}/api/version", {})
        version = version_response.get("version")
        return ProviderHealth(
            status="healthy",
            runtime_version=version if isinstance(version, str) else None,
            model_identity=ModelIdentity(
                provider="ollama",
                model=self.settings.model,
                digest=(
                    model_info.get("digest")
                    if isinstance(model_info.get("digest"), str)
                    else None
                ),
            ),
            capabilities=self.capabilities,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        self._validate_request_capabilities(request)
        if not isinstance(request.model, str) or not request.model.strip():
            raise ProviderError(
                "provider_not_configured",
                "Configure a model before using the local model provider.",
            )
        thinking_option = {}
        if request.model.casefold().startswith("qwen3") and not request.reasoning_enabled:
            thinking_option = {"think": False}
        configured_num_ctx = getattr(self.settings, "num_ctx", None)
        request_num_ctx = request.context_token_limit
        num_ctx = (
            min(configured_num_ctx, request_num_ctx)
            if isinstance(configured_num_ctx, int)
            and configured_num_ctx > 0
            and isinstance(request_num_ctx, int)
            and request_num_ctx > 0
            else configured_num_ctx
        )
        options = {"num_predict": request.max_output_tokens}
        if isinstance(num_ctx, int) and num_ctx > 0:
            options["num_ctx"] = num_ctx
        # Pass keep_alive when configured; omit the key entirely when None
        # so Ollama applies its own default TTL without our interference.
        keep_alive_seconds = getattr(self.settings, "ollama_keep_alive_seconds", None)
        keep_alive_option = (
            {"keep_alive": keep_alive_seconds}
            if isinstance(keep_alive_seconds, int) and keep_alive_seconds >= 0
            else {}
        )
        start = time.perf_counter()
        payload = self._post(
            f"{self.settings.base_url}/api/chat",
            {
                "model": request.model,
                "messages": [
                    _ollama_message_dict(message) for message in request.messages
                ],
                "tools": list(request.tools),
                "stream": False,
                "options": options,
                **thinking_option,
                **keep_alive_option,
                **({"format": "json"} if request.response_format == "json" else {}),
            },
            {},
            timeout_seconds=request.timeout_seconds,
        )
        request_duration_ms = (time.perf_counter() - start) * 1000.0
        if isinstance(payload.get("error"), str) and payload["error"]:
            raise ProviderError(
                "provider_inference_failed",
                "Ollama could not generate a response with the configured model.",
                telemetry=RequestTelemetry(
                    provider="ollama",
                    model=request.model,
                    request_duration_ms=request_duration_ms,
                    finish_reason=str(payload.get("done_reason") or "error"),
                    failure_reason="provider_inference_failed",
                    incomplete=True,
                ),
            )
        message = _provider_message(payload.get("message"), openai_format=False)
        if not message.tool_calls and not message.content.strip():
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an empty response.",
                telemetry=RequestTelemetry(
                    provider="ollama",
                    model=request.model,
                    request_duration_ms=request_duration_ms,
                    finish_reason=str(payload.get("done_reason") or "empty"),
                    failure_reason="empty_response",
                    incomplete=True,
                ),
            )
        usage = _usage(
            payload,
            input_key="prompt_eval_count",
            output_key="eval_count",
            cached_key="prompt_eval_cached",
        )
        thinking_tokens = (
            payload.get("thinking_tokens")
            if isinstance(payload.get("thinking_tokens"), int)
            and not isinstance(payload.get("thinking_tokens"), bool)
            else None
        )
        if thinking_tokens is not None and thinking_tokens < 0:
            thinking_tokens = None
        prompt_eval_duration_ms = payload.get("prompt_eval_duration")
        generation_duration_ms = payload.get("eval_duration")
        model_load_duration_ms = payload.get("load_duration")
        prompt_eval_duration_ms = (
            prompt_eval_duration_ms / 1_000_000
            if isinstance(prompt_eval_duration_ms, (int, float))
            and prompt_eval_duration_ms >= 0
            else None
        )
        generation_duration_ms = (
            generation_duration_ms / 1_000_000
            if isinstance(generation_duration_ms, (int, float))
            and generation_duration_ms >= 0
            else None
        )
        model_load_duration_ms = (
            model_load_duration_ms / 1_000_000
            if isinstance(model_load_duration_ms, (int, float))
            and model_load_duration_ms >= 0
            else None
        )
        tokens_per_second = (
            usage.output_tokens / (generation_duration_ms / 1000)
            if usage.output_tokens is not None
            and generation_duration_ms is not None
            and generation_duration_ms > 0
            else None
        )
        return ModelResponse(
            message=message,
            finish_reason=str(payload.get("done_reason") or "stop"),
            usage=usage,
            telemetry=RequestTelemetry(
                provider="ollama",
                model=request.model,
                input_tokens=usage.input_tokens,
                cached_tokens=usage.cached_tokens,
                output_tokens=usage.output_tokens,
                thinking_tokens=thinking_tokens,
                model_load_duration_ms=model_load_duration_ms,
                prompt_eval_duration_ms=prompt_eval_duration_ms,
                generation_duration_ms=generation_duration_ms,
                request_duration_ms=request_duration_ms,
                tokens_per_second=tokens_per_second,
                tool_calls=len(message.tool_calls),
                finish_reason=str(payload.get("done_reason") or "stop"),
                incomplete=str(payload.get("done_reason") or "").lower()
                in {"length", "max_tokens"},
            ),
        )


class OpenAICompatibleProvider(_HttpProvider):
    capabilities = ProviderCapabilities(
        text_generation="supported",
        tool_calling="supported",
        structured_output="supported",
        streaming="unsupported",
        local_inference="unverified",
    )

    def _endpoint(self, resource: str) -> str:
        base_url = self.settings.base_url
        if base_url is None:
            raise ProviderError(
                "provider_configuration_invalid",
                "OPENAI_COMPATIBLE_BASE_URL must be configured.",
            )
        hostname = (urlparse(base_url).hostname or "").lower().rstrip(".")
        if hostname == "openai.com" or hostname.endswith(".openai.com"):
            raise ProviderError(
                "provider_configuration_invalid",
                "The generic compatible adapter cannot target OpenAI's hosted service; configure a local or self-hosted endpoint.",
            )
        base_url = base_url.removesuffix("/chat/completions").rstrip("/")
        if base_url.endswith(f"/{resource}"):
            return base_url
        return f"{base_url}/{resource}"

    def _headers(self) -> dict[str, str]:
        if not self.settings.api_key:
            raise ProviderError(
                "provider_not_configured",
                "Configure OPENAI_COMPATIBLE_API_KEY before using this provider.",
            )
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    def check_health(self) -> ProviderHealth:
        if not self.settings.model:
            raise ProviderError(
                "provider_not_configured",
                "Configure OPENAI_COMPATIBLE_MODEL before using this provider.",
            )
        payload = self._get(self._endpoint("models"), self._headers())
        models = payload.get("data")
        if not isinstance(models, list):
            raise ProviderError(
                "provider_response_invalid",
                "The configured model provider returned an invalid model list.",
            )
        available = any(
            isinstance(item, dict) and item.get("id") == self.settings.model
            for item in models
        )
        return ProviderHealth(
            status="healthy" if available else "unavailable",
            model_identity=(
                ModelIdentity(
                    provider="openai_compatible",
                    model=self.settings.model,
                )
                if available
                else None
            ),
            capabilities=self.capabilities,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        self._validate_request_capabilities(request)
        if (
            not isinstance(request.model, str)
            or not request.model.strip()
            or not self.settings.api_key
        ):
            raise ProviderError(
                "provider_not_configured",
                "A model and API key are required for the OpenAI-compatible provider.",
            )
        endpoint = self._endpoint("chat/completions")
        start = time.perf_counter()
        payload = self._post_for_request(
            request,
            endpoint,
            {
                "model": request.model,
                "messages": [_message_dict(message) for message in request.messages],
                "tools": list(request.tools),
                "tool_choice": "auto",
                "max_tokens": request.max_output_tokens,
                **(
                    {"response_format": {"type": "json_object"}}
                    if request.response_format == "json"
                    else {}
                ),
            },
            {"Authorization": f"Bearer {self.settings.api_key}"},
        )
        request_duration_ms = (time.perf_counter() - start) * 1000.0
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an invalid response.",
                telemetry=RequestTelemetry(
                    provider="openai_compatible",
                    model=request.model,
                    request_duration_ms=request_duration_ms,
                    finish_reason="invalid_response",
                    failure_reason="invalid_response",
                    incomplete=True,
                ),
            )
        choice = choices[0]
        message = _provider_message(choice.get("message"), openai_format=True)
        if not message.tool_calls and not message.content.strip():
            raise ProviderError(
                "provider_response_invalid",
                "The configured model returned an empty response.",
                telemetry=RequestTelemetry(
                    provider="openai_compatible",
                    model=request.model,
                    request_duration_ms=request_duration_ms,
                    finish_reason=str(choice.get("finish_reason") or "empty"),
                    failure_reason="empty_response",
                    incomplete=True,
                ),
            )
        usage = _usage(
            payload.get("usage"),
            input_key="prompt_tokens",
            output_key="completion_tokens",
        )
        if isinstance(payload.get("usage"), dict):
            details = payload["usage"].get("prompt_tokens_details")
            if isinstance(details, dict):
                cached = details.get("cached_tokens")
                if (
                    isinstance(cached, int)
                    and not isinstance(cached, bool)
                    and cached >= 0
                ):
                    usage = UsageMetadata(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cached_tokens=cached,
                        reasoning_tokens=None,
                    )
            completion_details = payload["usage"].get("completion_tokens_details")
            if isinstance(completion_details, dict):
                reasoning_tokens = completion_details.get("reasoning_tokens")
                if (
                    isinstance(reasoning_tokens, int)
                    and not isinstance(reasoning_tokens, bool)
                    and reasoning_tokens >= 0
                ):
                    usage = UsageMetadata(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cached_tokens=usage.cached_tokens,
                        reasoning_tokens=reasoning_tokens,
                    )
        tokens_per_second = None
        if usage.output_tokens is not None and request_duration_ms > 0:
            tokens_per_second = usage.output_tokens / (request_duration_ms / 1000.0)
        return ModelResponse(
            message=message,
            finish_reason=str(choice.get("finish_reason") or "stop"),
            usage=usage,
            telemetry=RequestTelemetry(
                provider="openai_compatible",
                model=request.model,
                input_tokens=usage.input_tokens,
                cached_tokens=usage.cached_tokens,
                output_tokens=usage.output_tokens,
                request_duration_ms=request_duration_ms,
                tokens_per_second=tokens_per_second,
                tool_calls=len(message.tool_calls),
                finish_reason=str(choice.get("finish_reason") or "stop"),
                incomplete=str(choice.get("finish_reason") or "").lower()
                in {"length", "max_tokens"},
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
