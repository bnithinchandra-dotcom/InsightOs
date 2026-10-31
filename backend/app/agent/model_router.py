from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Literal

from app.agent.config import (
    MAX_HISTORY_MESSAGES,
    MAX_OUTPUT_TOKENS,
    MAX_TOOL_CALLS,
    AgentSettings,
    resolve_task_budget,
    task_budget_for_request,
)
from app.agent.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    RequestTelemetry,
    UsageMetadata,
)
from app.agent.model_registry import REGISTRY
from app.agent.providers import ModelProvider

logger = logging.getLogger(__name__)


AgentRole = Literal[
    "orchestrator",
    "data_intelligence",
    "analytics_modelling",
    "insights_visualization",
]

_ROLE_MODEL_ENV = {
    "orchestrator": "INSIGHTOS_LLM_MODEL_ORCHESTRATOR",
    "data_intelligence": "INSIGHTOS_LLM_MODEL_DATA_INTELLIGENCE",
    "analytics_modelling": "INSIGHTOS_LLM_MODEL_ANALYTICS",
    "insights_visualization": "INSIGHTOS_LLM_MODEL_PRESENTATION",
}


@dataclass(frozen=True)
class RoutedCompletion:
    role: AgentRole
    model: str
    response: ModelResponse


class WorkflowTokenBudget:
    def __init__(self, total_tokens: int, final_reserve_tokens: int) -> None:
        self.total_tokens = min(max(1, total_tokens), 100_000)
        self.final_reserve_tokens = min(
            max(0, final_reserve_tokens),
            self.total_tokens,
        )
        self.input_tokens = 0
        self.output_tokens = 0
        self.unmetered_output_reservation = 0
        self._lock = threading.Lock()

    @property
    def consumed_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.unmetered_output_reservation
        )

    def reserve_output(self, requested_tokens: int, *, is_final: bool) -> int:
        with self._lock:
            remaining = self.total_tokens - self.consumed_tokens
            if not is_final:
                remaining -= self.final_reserve_tokens
            if remaining < 1:
                raise ProviderError(
                    "workflow_token_budget_exhausted",
                    "The workflow reached its model-token budget before this generation.",
                )
            return min(requested_tokens, remaining, MAX_OUTPUT_TOKENS)

    def record(self, reserved_tokens: int, usage: UsageMetadata) -> tuple[int, int]:
        with self._lock:
            self.input_tokens += usage.input_tokens or 0
            if usage.output_tokens is None:
                self.unmetered_output_reservation += reserved_tokens
            else:
                self.output_tokens += usage.output_tokens
            return self.input_tokens, self.output_tokens

    def snapshot(self) -> tuple[int, int, int, int]:
        with self._lock:
            return (
                self.input_tokens,
                self.output_tokens,
                self.total_tokens,
                self.consumed_tokens,
            )


class AgentModelRouter:
    _generation_lock = threading.BoundedSemaphore(1)

    def __init__(
        self,
        *,
        settings: AgentSettings,
        provider: ModelProvider,
        model_overrides: dict[AgentRole, str] | None = None,
        workflow_budget: WorkflowTokenBudget | None = None,
        telemetry_callback: Callable[[RequestTelemetry], None] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._model_overrides = model_overrides or {}
        self._workflow_budget = workflow_budget or WorkflowTokenBudget(
            settings.workflow_token_budget,
            settings.workflow_final_reserve_tokens,
        )
        self._telemetry_callback = telemetry_callback

    def model_for(self, role: AgentRole) -> str:
        override = self._model_overrides.get(role)
        if override is None:
            override = os.getenv(_ROLE_MODEL_ENV[role], "").strip() or None
        model = override or self._settings.model
        if not isinstance(model, str) or not model.strip():
            raise ProviderError(
                "provider_not_configured",
                f"No local model is configured for the {role} role.",
            )
        if len(model) > 160:
            raise ProviderError(
                "provider_configuration_invalid",
                f"The configured model identifier for {role} is too long.",
            )
        # Registry gate: only applies to the Ollama provider.
        # OpenAI-compatible providers manage their own model namespaces.
        if self._settings.provider == "ollama":
            entry = REGISTRY.require_eligible(model, role)
            if entry.validation_status == "candidate":
                logger.warning(
                    "Dispatching candidate (unvalidated) model %r for role %r. "
                    "Run an approved live validation before enabling in production.",
                    model,
                    role,
                )
        return model

    @property
    def max_output_tokens(self) -> int:
        return self._settings.max_output_tokens

    def complete(
        self,
        role: AgentRole,
        request: ModelRequest,
    ) -> RoutedCompletion:
        model = self.model_for(role)
        category = request.task_category or "lightweight"
        profile = resolve_task_budget(
            category,
            allow_live_test=request.allow_live_test,
        )
        requested_tokens = task_budget_for_request(
            min(request.max_output_tokens, self._settings.max_output_tokens),
            category,
            allow_live_test=request.allow_live_test,
        )
        output_limit = self._workflow_budget.reserve_output(
            requested_tokens,
            is_final=request.is_final,
        )
        bounded_request = replace(
            request,
            model=model,
            timeout_seconds=min(
                self._settings.timeout_seconds,
                profile.timeout_seconds,
            ),
            max_output_tokens=min(output_limit, profile.max_output_tokens, MAX_OUTPUT_TOKENS),
            reasoning_enabled=(
                request.reasoning_enabled
                and self._settings.reasoning_enabled
                and profile.reasoning_allowed
                and request.response_format is None
            ),
            context_token_limit=profile.context_tokens,
        )
        message_limit = min(
            self._settings.max_history_messages,
            profile.max_history_messages,
            MAX_HISTORY_MESSAGES,
        ) + 2
        if role == "orchestrator":
            message_limit += min(
                self._settings.max_tool_calls,
                profile.max_tool_calls,
                MAX_TOOL_CALLS,
            ) * 2 + 1
        if len(bounded_request.messages) > message_limit:
            raise ProviderError(
                "agent_context_too_large",
                "The specialist request exceeded the configured message limit.",
            )
        acquired = self._generation_lock.acquire(
            timeout=bounded_request.timeout_seconds,
        )
        if not acquired:
            failure = RequestTelemetry(
                provider=self._settings.provider,
                model=model,
                agent_role=role,
                workflow_id=request.workflow_id,
                task_category=category,
                retries=0,
                failure_reason="provider_timeout",
                incomplete=True,
                context_truncation=request.context_truncated,
            )
            self._emit(failure)
            raise ProviderError(
                "provider_timeout",
                "The model request timed out waiting for an available inference slot.",
                retryable=True,
                telemetry=failure,
            )
        started = time.perf_counter()
        try:
            response = self._provider.complete(bounded_request)
        except ProviderError as error:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self._workflow_budget.record(output_limit, UsageMetadata())
            error_telemetry = error.telemetry or RequestTelemetry()
            (
                cumulative_input,
                cumulative_output,
                total_budget,
                budget_used,
            ) = (
                self._workflow_budget.snapshot()
            )
            failure = RequestTelemetry(
                **{
                    **error_telemetry.as_dict(),
                    "provider": self._settings.provider,
                    "model": model,
                    "agent_role": role,
                    "workflow_id": request.workflow_id,
                    "task_category": category,
                    "context_allocation_tokens": profile.context_tokens,
                    "configured_context_tokens": (
                        min(self._settings.num_ctx, profile.context_tokens)
                        if self._settings.num_ctx is not None
                        else None
                    ),
                    "output_token_limit": output_limit,
                    "request_duration_ms": (
                        error_telemetry.request_duration_ms
                        if error_telemetry.request_duration_ms is not None
                        else elapsed_ms
                    ),
                    "retries": 0,
                    "failure_reason": error.code,
                    "incomplete": True,
                    "context_truncation": request.context_truncated,
                    "cumulative_input_tokens": cumulative_input,
                    "cumulative_output_tokens": cumulative_output,
                    "workflow_budget_tokens": total_budget,
                    "workflow_budget_used_tokens": budget_used,
                    "unmetered_output_reservation": self._workflow_budget.unmetered_output_reservation,
                }
            )
            self._emit(failure)
            raise ProviderError(
                error.code,
                error.message,
                retryable=error.retryable,
                telemetry=failure,
            ) from None
        except Exception:
            self._workflow_budget.record(output_limit, UsageMetadata())
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            failure = RequestTelemetry(
                provider=self._settings.provider,
                model=model,
                agent_role=role,
                workflow_id=request.workflow_id,
                task_category=category,
                context_allocation_tokens=profile.context_tokens,
                configured_context_tokens=(
                    min(self._settings.num_ctx, profile.context_tokens)
                    if self._settings.num_ctx is not None
                    else None
                ),
                output_token_limit=bounded_request.max_output_tokens,
                request_duration_ms=elapsed_ms,
                retries=0,
                failure_reason="provider_unavailable",
                incomplete=True,
                context_truncation=request.context_truncated,
                cumulative_input_tokens=self._workflow_budget.snapshot()[0],
                cumulative_output_tokens=self._workflow_budget.snapshot()[1],
                workflow_budget_tokens=self._settings.workflow_token_budget,
                workflow_budget_used_tokens=self._workflow_budget.consumed_tokens,
                unmetered_output_reservation=(
                    self._workflow_budget.unmetered_output_reservation
                ),
            )
            self._emit(failure)
            raise
        finally:
            self._generation_lock.release()
        self._workflow_budget.record(output_limit, response.usage)
        (
            cumulative_input,
            cumulative_output,
            total_budget,
            budget_used,
        ) = (
            self._workflow_budget.snapshot()
        )
        provider_telemetry = response.telemetry
        request_telemetry = replace(
            provider_telemetry or RequestTelemetry(),
            provider=self._settings.provider,
            model=model,
            agent_role=role,
            workflow_id=request.workflow_id,
            task_category=category,
            context_allocation_tokens=profile.context_tokens,
            configured_context_tokens=(
                min(self._settings.num_ctx, profile.context_tokens)
                if self._settings.num_ctx is not None
                else None
            ),
            output_token_limit=bounded_request.max_output_tokens,
            input_tokens=response.usage.input_tokens,
            cached_tokens=response.usage.cached_tokens,
            output_tokens=response.usage.output_tokens,
            thinking_tokens=response.usage.reasoning_tokens,
            request_duration_ms=(
                provider_telemetry.request_duration_ms
                if provider_telemetry and provider_telemetry.request_duration_ms is not None
                else (time.perf_counter() - started) * 1000.0
            ),
            tool_calls=len(response.message.tool_calls),
            retries=0,
            finish_reason=response.finish_reason,
            context_truncation=request.context_truncated,
            incomplete=response.finish_reason.lower() in {"length", "max_tokens"},
            cumulative_input_tokens=cumulative_input,
            cumulative_output_tokens=cumulative_output,
            workflow_budget_tokens=total_budget,
            workflow_budget_used_tokens=budget_used,
            unmetered_output_reservation=(
                self._workflow_budget.unmetered_output_reservation
            ),
        )
        self._emit(request_telemetry)
        response = replace(response, telemetry=request_telemetry)
        return RoutedCompletion(role=role, model=model, response=response)

    def _emit(self, telemetry: RequestTelemetry) -> None:
        logger.info(
            "Model generation telemetry",
            extra={"model_request_telemetry": telemetry.as_dict()},
        )
        if self._telemetry_callback is not None:
            self._telemetry_callback(telemetry)
