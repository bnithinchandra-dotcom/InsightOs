from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant", "tool"]
CapabilityStatus = Literal["supported", "unsupported", "unverified"]
AgentExecutionStatus = Literal[
    "completed",
    "partial",
    "failed",
    "provider_unavailable",
    "blocked",
    "unsupported",
]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderMessage:
    role: Role
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    thinking: str | None = None


@dataclass(frozen=True)
class ModelRequest:
    model: str
    messages: tuple[ProviderMessage, ...]
    tools: tuple[dict[str, Any], ...]
    max_output_tokens: int
    response_format: Literal["json"] | None = None
    task_category: str | None = None
    workflow_id: str | None = None
    timeout_seconds: float | None = None
    reasoning_enabled: bool = False
    context_truncated: bool = False
    is_final: bool = False
    context_token_limit: int | None = None
    allow_live_test: bool = False


@dataclass(frozen=True)
class UsageMetadata:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class RequestTelemetry:
    provider: str | None = None
    model: str | None = None
    agent_role: str | None = None
    workflow_id: str | None = None
    task_category: str | None = None
    context_allocation_tokens: int | None = None
    configured_context_tokens: int | None = None
    output_token_limit: int | None = None
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    model_load_duration_ms: float | None = None
    prompt_eval_duration_ms: float | None = None
    generation_duration_ms: float | None = None
    request_duration_ms: float | None = None
    tokens_per_second: float | None = None
    tool_calls: int | None = None
    retries: int | None = None
    finish_reason: str | None = None
    context_truncation: bool = False
    failure_reason: str | None = None
    incomplete: bool = False
    cumulative_input_tokens: int | None = None
    cumulative_output_tokens: int | None = None
    workflow_budget_tokens: int | None = None
    workflow_budget_used_tokens: int | None = None
    unmetered_output_reservation: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "agent_role": self.agent_role,
            "workflow_id": self.workflow_id,
            "task_category": self.task_category,
            "context_allocation_tokens": self.context_allocation_tokens,
            "configured_context_tokens": self.configured_context_tokens,
            "output_token_limit": self.output_token_limit,
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "model_load_duration_ms": self.model_load_duration_ms,
            "prompt_eval_duration_ms": self.prompt_eval_duration_ms,
            "generation_duration_ms": self.generation_duration_ms,
            "request_duration_ms": self.request_duration_ms,
            "tokens_per_second": self.tokens_per_second,
            "tool_calls": self.tool_calls,
            "retries": self.retries,
            "finish_reason": self.finish_reason,
            "context_truncation": self.context_truncation,
            "failure_reason": self.failure_reason,
            "incomplete": self.incomplete,
            "cumulative_input_tokens": self.cumulative_input_tokens,
            "cumulative_output_tokens": self.cumulative_output_tokens,
            "workflow_budget_tokens": self.workflow_budget_tokens,
            "workflow_budget_used_tokens": self.workflow_budget_used_tokens,
            "unmetered_output_reservation": self.unmetered_output_reservation,
        }


@dataclass(frozen=True)
class ModelResponse:
    message: ProviderMessage
    finish_reason: str
    usage: UsageMetadata = field(default_factory=UsageMetadata)
    telemetry: RequestTelemetry | None = None


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str
    digest: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    text_generation: CapabilityStatus
    tool_calling: CapabilityStatus
    structured_output: CapabilityStatus
    streaming: CapabilityStatus
    local_inference: CapabilityStatus

    def as_dict(self) -> dict[str, CapabilityStatus]:
        return {
            "text_generation": self.text_generation,
            "tool_calling": self.tool_calling,
            "structured_output": self.structured_output,
            "streaming": self.streaming,
            "local_inference": self.local_inference,
        }


@dataclass(frozen=True)
class ProviderHealth:
    status: Literal["healthy", "unavailable"]
    runtime_version: str | None = None
    model_identity: ModelIdentity | None = None
    capabilities: ProviderCapabilities | None = None


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        telemetry: RequestTelemetry | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.telemetry = telemetry


class ToolExecutionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ToolResult:
    status: Literal["succeeded", "failed"]
    summary: str
    data: dict[str, Any] | list[Any] | None = None
    warnings: tuple[str, ...] = ()
    references: tuple[dict[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class ToolActivity:
    tool_name: str
    label: str
    status: Literal["succeeded", "failed"]
    summary: str
    references: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentOutcome:
    status: AgentExecutionStatus
    assistant_text: str
    activities: tuple[ToolActivity, ...]
    structured_results: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    usage: UsageMetadata
    error_code: str | None = None
    telemetry: tuple[RequestTelemetry, ...] = ()
