from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant", "tool"]


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


@dataclass(frozen=True)
class ModelRequest:
    model: str
    messages: tuple[ProviderMessage, ...]
    tools: tuple[dict[str, Any], ...]
    max_output_tokens: int


@dataclass(frozen=True)
class UsageMetadata:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    message: ProviderMessage
    finish_reason: str
    usage: UsageMetadata = field(default_factory=UsageMetadata)


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
    status: Literal["completed", "partial", "failed", "provider_unavailable"]
    assistant_text: str
    activities: tuple[ToolActivity, ...]
    structured_results: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    usage: UsageMetadata
    error_code: str | None = None
