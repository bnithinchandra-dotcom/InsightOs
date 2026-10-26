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


@dataclass(frozen=True)
class UsageMetadata:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    message: ProviderMessage
    finish_reason: str
    usage: UsageMetadata = field(default_factory=UsageMetadata)


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
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


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
