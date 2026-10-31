from __future__ import annotations

import math
from dataclasses import dataclass
from os import getenv
from typing import Literal
from urllib.parse import urlparse

from app.agent.contracts import ProviderError


MAX_TOOL_CALLS = 10
MAX_MODEL_TURNS = 8
MAX_HISTORY_MESSAGES = 100
MAX_USER_MESSAGE_CHARS = 8_000
MAX_TOOL_OUTPUT_BYTES = 256 * 1024

TaskCategory = Literal[
    "lightweight",
    "routing",
    "conversation",
    "analytical",
    "complex_reasoning",
    "presentation",
    "verification",
    "live_test",
]
MAX_OUTPUT_TOKENS = 16_384


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
    num_ctx: int | None = None
    reasoning_enabled: bool = False
    workflow_token_budget: int = 24_576
    workflow_final_reserve_tokens: int = 3_072


@dataclass(frozen=True)
class TaskBudgetProfile:
    name: TaskCategory
    max_output_tokens: int
    context_tokens: int
    max_history_messages: int
    max_model_turns: int
    max_tool_calls: int
    timeout_seconds: float
    reasoning_allowed: bool


_TASK_BUDGETS: dict[TaskCategory, TaskBudgetProfile] = {
    "lightweight": TaskBudgetProfile(
        name="lightweight",
        max_output_tokens=1024,
        context_tokens=32_768,
        max_history_messages=12,
        max_model_turns=3,
        max_tool_calls=3,
        timeout_seconds=30.0,
        reasoning_allowed=False,
    ),
    "routing": TaskBudgetProfile(
        name="routing",
        max_output_tokens=768,
        context_tokens=16_384,
        max_history_messages=8,
        max_model_turns=2,
        max_tool_calls=2,
        timeout_seconds=20.0,
        reasoning_allowed=False,
    ),
    "conversation": TaskBudgetProfile(
        name="conversation",
        max_output_tokens=3072,
        context_tokens=32_768,
        max_history_messages=12,
        max_model_turns=3,
        max_tool_calls=3,
        timeout_seconds=45.0,
        reasoning_allowed=False,
    ),
    "analytical": TaskBudgetProfile(
        name="analytical",
        max_output_tokens=4096,
        context_tokens=32_768,
        max_history_messages=12,
        max_model_turns=4,
        max_tool_calls=5,
        timeout_seconds=45.0,
        reasoning_allowed=False,
    ),
    "complex_reasoning": TaskBudgetProfile(
        name="complex_reasoning",
        max_output_tokens=6144,
        context_tokens=49_152,
        max_history_messages=16,
        max_model_turns=4,
        max_tool_calls=5,
        timeout_seconds=60.0,
        reasoning_allowed=True,
    ),
    "presentation": TaskBudgetProfile(
        name="presentation",
        max_output_tokens=1024,
        context_tokens=16_384,
        max_history_messages=8,
        max_model_turns=2,
        max_tool_calls=2,
        timeout_seconds=20.0,
        reasoning_allowed=False,
    ),
    "verification": TaskBudgetProfile(
        name="verification",
        max_output_tokens=2048,
        context_tokens=24_576,
        max_history_messages=10,
        max_model_turns=2,
        max_tool_calls=3,
        timeout_seconds=30.0,
        reasoning_allowed=False,
    ),
    "live_test": TaskBudgetProfile(
        name="live_test",
        max_output_tokens=4096,
        context_tokens=65_536,
        max_history_messages=20,
        max_model_turns=4,
        max_tool_calls=5,
        timeout_seconds=90.0,
        reasoning_allowed=False,
    ),
}


def resolve_task_budget(
    category: str | TaskCategory | None = None,
    *,
    allow_live_test: bool = False,
) -> TaskBudgetProfile:
    live_test_enabled = (
        getenv("INSIGHTOS_LLM_LIVE_TEST_PROFILE", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if allow_live_test and live_test_enabled:
        return _TASK_BUDGETS["live_test"]
    requested = (category or getenv("INSIGHTOS_LLM_TASK_CATEGORY", "lightweight")).strip().lower()
    profile = _TASK_BUDGETS.get(requested) if requested in _TASK_BUDGETS else None
    if requested == "live_test" and not (live_test_enabled and allow_live_test):
        raise ProviderError(
            "provider_configuration_invalid",
            "The live-test profile requires INSIGHTOS_LLM_LIVE_TEST_PROFILE=true.",
        )
    if profile is None:
        raise ProviderError(
            "provider_configuration_invalid",
            f"Unsupported task category {requested!r}. Expected one of: {', '.join(sorted(_TASK_BUDGETS))}.",
        )
    return profile


def task_budget_for_request(
    requested: int,
    category: str | None,
    *,
    allow_live_test: bool = False,
) -> int:
    if not isinstance(requested, int) or isinstance(requested, bool) or requested < 1:
        raise ProviderError(
            "provider_configuration_invalid",
            "The requested model output budget must be a positive whole number.",
        )
    profile = resolve_task_budget(category, allow_live_test=allow_live_test)
    return min(requested, profile.max_output_tokens, MAX_OUTPUT_TOKENS)


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
        MAX_OUTPUT_TOKENS,
        MAX_OUTPUT_TOKENS,
    )
    workflow_token_budget = _positive_int(
        "INSIGHTOS_AGENT_WORKFLOW_TOKEN_BUDGET",
        24_576,
        100_000,
    )
    workflow_final_reserve_tokens = _positive_int(
        "INSIGHTOS_AGENT_WORKFLOW_FINAL_RESERVE_TOKENS",
        min(3_072, workflow_token_budget),
        workflow_token_budget,
    )
    num_ctx = None
    _raw_num_ctx = getenv("INSIGHTOS_OLLAMA_NUM_CTX", "").strip()
    if _raw_num_ctx:
        try:
            num_ctx = int(_raw_num_ctx)
        except ValueError:
            raise ProviderError(
                "provider_configuration_invalid",
                "INSIGHTOS_OLLAMA_NUM_CTX must be a whole number of tokens.",
            ) from None
        if num_ctx < 1 or num_ctx > 131_072:
            raise ProviderError(
                "provider_configuration_invalid",
                "INSIGHTOS_OLLAMA_NUM_CTX must be between 1 and 131072 tokens.",
            )

    reasoning_enabled = False
    _raw_reasoning = getenv("INSIGHTOS_OLLAMA_THINK", "").strip().lower()
    if _raw_reasoning:
        if _raw_reasoning in {"1", "true", "yes", "on"}:
            reasoning_enabled = True
        elif _raw_reasoning not in {"0", "false", "no", "off"}:
            raise ProviderError(
                "provider_configuration_invalid",
                "INSIGHTOS_OLLAMA_THINK must be a boolean value.",
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
            num_ctx=num_ctx,
            reasoning_enabled=reasoning_enabled,
            workflow_token_budget=workflow_token_budget,
            workflow_final_reserve_tokens=workflow_final_reserve_tokens,
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
            num_ctx=num_ctx,
            reasoning_enabled=reasoning_enabled,
            workflow_token_budget=workflow_token_budget,
            workflow_final_reserve_tokens=workflow_final_reserve_tokens,
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
        num_ctx=num_ctx,
        reasoning_enabled=reasoning_enabled,
        workflow_token_budget=workflow_token_budget,
        workflow_final_reserve_tokens=workflow_final_reserve_tokens,
    )
