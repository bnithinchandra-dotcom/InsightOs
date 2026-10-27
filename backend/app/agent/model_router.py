from __future__ import annotations

import os
import threading
from dataclasses import dataclass, replace
from typing import Literal

from app.agent.config import AgentSettings
from app.agent.contracts import ModelRequest, ModelResponse, ProviderError
from app.agent.providers import ModelProvider


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


class AgentModelRouter:
    _generation_lock = threading.BoundedSemaphore(1)

    def __init__(
        self,
        *,
        settings: AgentSettings,
        provider: ModelProvider,
        model_overrides: dict[AgentRole, str] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._model_overrides = model_overrides or {}

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
        bounded_request = replace(
            request,
            model=model,
            max_output_tokens=min(
                request.max_output_tokens,
                self._settings.max_output_tokens,
            ),
        )
        if len(bounded_request.messages) > self._settings.max_history_messages + 2:
            raise ProviderError(
                "agent_context_too_large",
                "The specialist request exceeded the configured message limit.",
            )
        acquired = self._generation_lock.acquire(
            timeout=self._settings.timeout_seconds,
        )
        if not acquired:
            raise ProviderError(
                "provider_timeout",
                "The model request timed out waiting for an available inference slot.",
                retryable=True,
            )
        try:
            response = self._provider.complete(bounded_request)
        finally:
            self._generation_lock.release()
        return RoutedCompletion(role=role, model=model, response=response)
