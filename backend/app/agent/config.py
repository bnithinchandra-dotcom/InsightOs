from __future__ import annotations

import math
from dataclasses import dataclass
from os import getenv
from urllib.parse import urlparse

from app.agent.contracts import ProviderError


MAX_TOOL_CALLS = 10
MAX_MODEL_TURNS = 8
MAX_HISTORY_MESSAGES = 100
MAX_USER_MESSAGE_CHARS = 8_000
MAX_TOOL_OUTPUT_BYTES = 256 * 1024


@dataclass(frozen=True)
class AgentSettings:
    provider: str
    base_url: str | None
    model: str | None
    api_key: str | None
    timeout_seconds: float
    max_output_tokens: int
    max_model_turns: int
    max_tool_calls: int
    max_history_messages: int
    # Optional Ollama keep_alive value in seconds. None uses Ollama default TTL.
    # Configure via INSIGHTOS_OLLAMA_KEEP_ALIVE. Only relevant for ollama provider.
    ollama_keep_alive_seconds: int | None = None


def _positive_int(name: str, default: int, maximum: int) -> int:
    value = getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError:
        raise ProviderError(
            "provider_configuration_invalid",
            f"{name} must be a whole number between 1 and {maximum}.",
        ) from None
    if parsed < 1 or parsed > maximum:
        raise ProviderError(
            "provider_configuration_invalid",
            f"{name} must be a whole number between 1 and {maximum}.",
        )
    return parsed


def _base_url(name: str, value: str | None) -> str:
    if not value:
        raise ProviderError(
            "provider_configuration_invalid",
            f"{name} must be configured for the selected model provider.",
        )
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderError(
            "provider_configuration_invalid",
            f"{name} must be an absolute HTTP or HTTPS URL.",
        )
    return value.rstrip("/")


def get_agent_settings() -> AgentSettings:
    provider = getenv("INSIGHTOS_LLM_PROVIDER", "none").strip().lower()
    if provider not in {"none", "ollama", "openai_compatible"}:
        raise ProviderError(
            "provider_configuration_invalid",
            "INSIGHTOS_LLM_PROVIDER must be none, ollama, or openai_compatible.",
        )
    try:
        timeout = float(getenv("INSIGHTOS_LLM_TIMEOUT_SECONDS", "30"))
    except ValueError:
        raise ProviderError(
            "provider_configuration_invalid",
            "INSIGHTOS_LLM_TIMEOUT_SECONDS must be a positive finite number.",
        ) from None
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
        raise ProviderError(
            "provider_configuration_invalid",
            "INSIGHTOS_LLM_TIMEOUT_SECONDS must be greater than 0 and at most 300.",
        )

    max_tool_calls = _positive_int(
        "INSIGHTOS_AGENT_MAX_TOOL_CALLS",
        6,
        MAX_TOOL_CALLS,
    )
    max_model_turns = _positive_int(
        "INSIGHTOS_AGENT_MAX_MODEL_TURNS",
        4,
        MAX_MODEL_TURNS,
    )
    max_history_messages = _positive_int(
        "INSIGHTOS_AGENT_MAX_HISTORY_MESSAGES",
        40,
        MAX_HISTORY_MESSAGES,
    )
    max_output_tokens = _positive_int(
        "INSIGHTOS_LLM_MAX_OUTPUT_TOKENS",
        1200,
        16_384,
    )

    if provider == "ollama":
        keep_alive_seconds = None
        _raw_keep_alive = getenv("INSIGHTOS_OLLAMA_KEEP_ALIVE", "").strip()
        if _raw_keep_alive:
            try:
                _ka = int(_raw_keep_alive)
            except ValueError:
                raise ProviderError(
                    "provider_configuration_invalid",
                    "INSIGHTOS_OLLAMA_KEEP_ALIVE must be a whole number of seconds.",
                ) from None
            if _ka < 0:
                raise ProviderError(
                    "provider_configuration_invalid",
                    "INSIGHTOS_OLLAMA_KEEP_ALIVE must be zero or a positive number of seconds.",
                )
            keep_alive_seconds = _ka
        return AgentSettings(
            provider=provider,
            base_url=_base_url(
                "OLLAMA_BASE_URL",
                getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            ),
            model=(getenv("OLLAMA_MODEL", "").strip() or None),
            api_key=None,
            timeout_seconds=timeout,
            max_output_tokens=max_output_tokens,
            max_model_turns=max_model_turns,
            max_tool_calls=max_tool_calls,
            max_history_messages=max_history_messages,
            ollama_keep_alive_seconds=keep_alive_seconds,
        )
    if provider == "openai_compatible":
        return AgentSettings(
            provider=provider,
            base_url=_base_url(
                "OPENAI_COMPATIBLE_BASE_URL",
                getenv("OPENAI_COMPATIBLE_BASE_URL"),
            ),
            model=(getenv("OPENAI_COMPATIBLE_MODEL", "").strip() or None),
            api_key=(getenv("OPENAI_COMPATIBLE_API_KEY", "").strip() or None),
            timeout_seconds=timeout,
            max_output_tokens=max_output_tokens,
            max_model_turns=max_model_turns,
            max_tool_calls=max_tool_calls,
            max_history_messages=max_history_messages,
        )
    return AgentSettings(
        provider="none",
        base_url=None,
        model=None,
        api_key=None,
        timeout_seconds=timeout,
        max_output_tokens=max_output_tokens,
        max_model_turns=max_model_turns,
        max_tool_calls=max_tool_calls,
        max_history_messages=max_history_messages,
    )
