from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

from app.agent.contracts import ProviderError


ModelLifecycleState = Literal[
    "inactive",
    "activating",
    "active",
    "releasing",
    "failed",
    "cancelled",
]


class LifecycleProvider(Protocol):
    def load_model(self, model: str) -> None: ...

    def unload_model(self, model: str) -> None: ...

    def loaded_models(self) -> frozenset[str]: ...


@dataclass(frozen=True)
class ModelLifecycleSnapshot:
    state: ModelLifecycleState
    active_model: str | None
    loaded_models: frozenset[str]
    activation_duration_ms: float | None
    release_duration_ms: float | None
    last_error: str | None


class ModelLifecycleManager:
    """Serializes InsightOS model residency changes around active generations."""

    def __init__(
        self,
        provider: LifecycleProvider,
        *,
        transition_timeout_seconds: float = 120.0,
    ) -> None:
        if transition_timeout_seconds <= 0:
            raise ValueError("transition_timeout_seconds must be positive.")
        self._provider = provider
        self._transition_timeout_seconds = transition_timeout_seconds
        self._condition = threading.Condition(threading.RLock())
        self._state: ModelLifecycleState = "inactive"
        self._active_model: str | None = None
        self._leases = 0
        self._activation_duration_ms: float | None = None
        self._release_duration_ms: float | None = None
        self._last_error: str | None = None

    @property
    def active_model(self) -> str | None:
        with self._condition:
            return self._active_model

    def snapshot(self) -> ModelLifecycleSnapshot:
        with self._condition:
            loaded = self._provider.loaded_models()
            return ModelLifecycleSnapshot(
                state=self._state,
                active_model=self._active_model,
                loaded_models=loaded,
                activation_duration_ms=self._activation_duration_ms,
                release_duration_ms=self._release_duration_ms,
                last_error=self._last_error,
            )

    def reconcile(self) -> ModelLifecycleSnapshot:
        with self._condition:
            try:
                loaded = self._provider.loaded_models()
            except BaseException as error:
                self._state = "failed" if isinstance(error, Exception) else "cancelled"
                self._last_error = str(error) if isinstance(error, Exception) else None
                raise
            if self._active_model not in loaded:
                self._active_model = None
                if self._state not in {"failed", "cancelled"}:
                    self._state = "inactive"
            elif self._state not in {"activating", "releasing"}:
                self._state = "active"
            return ModelLifecycleSnapshot(
                state=self._state,
                active_model=self._active_model,
                loaded_models=loaded,
                activation_duration_ms=self._activation_duration_ms,
                release_duration_ms=self._release_duration_ms,
                last_error=self._last_error,
            )

    def activate(self, model: str) -> ModelLifecycleSnapshot:
        if not isinstance(model, str) or not model.strip():
            raise ProviderError(
                "provider_configuration_invalid",
                "A non-empty model identifier is required for activation.",
            )
        with self._condition:
            self._activate_locked(model)
            return self.snapshot()

    @contextmanager
    def generation_lease(
        self,
        model: str,
        *,
        release_after: bool = True,
    ) -> Generator[None, None, None]:
        with self._condition:
            self._activate_locked(model)
            self._leases += 1
        try:
            yield
        except BaseException as error:
            failure_state: ModelLifecycleState = (
                "failed" if isinstance(error, Exception) else "cancelled"
            )
            with self._condition:
                self._state = failure_state
                self._last_error = str(error) if isinstance(error, Exception) else None
                self._leases -= 1
                self._condition.notify_all()
                if release_after:
                    self.release()
                    if self._state != "failed":
                        self._state = failure_state
            raise
        else:
            with self._condition:
                self._leases -= 1
                self._condition.notify_all()
                if release_after:
                    self.release()

    @asynccontextmanager
    async def async_generation_lease(
        self,
        model: str,
        *,
        release_after: bool = True,
    ) -> AsyncGenerator[None, None, None]:
        lease = self.generation_lease(model, release_after=release_after)
        await asyncio.to_thread(lease.__enter__)
        try:
            yield
        except BaseException as error:
            await asyncio.shield(
                asyncio.to_thread(
                    lease.__exit__,
                    type(error),
                    error,
                    error.__traceback__,
                )
            )
            raise
        else:
            await asyncio.shield(
                asyncio.to_thread(lease.__exit__, None, None, None)
            )

    def release(self) -> ModelLifecycleSnapshot:
        with self._condition:
            model = self._active_model
            if model is None:
                self._state = "inactive"
                return self.snapshot()
            deadline = time.monotonic() + self._transition_timeout_seconds
            while self._leases:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderError(
                        "provider_timeout",
                        "Timed out waiting for an active generation before model release.",
                        retryable=True,
                    )
                self._condition.wait(remaining)
            self._state = "releasing"
            started = time.perf_counter()
            try:
                self._provider.unload_model(model)
                try:
                    loaded = self._provider.loaded_models()
                except BaseException as error:
                    self._state = "failed" if isinstance(error, Exception) else "cancelled"
                    self._last_error = str(error) if isinstance(error, Exception) else None
                    raise
                if model in loaded:
                    raise ProviderError(
                        "provider_model_release_unverified",
                        f"Ollama still reports {model!r} as resident after release.",
                    )
            except BaseException as error:
                self._state = (
                    "failed" if isinstance(error, Exception) else "cancelled"
                )
                self._last_error = str(error) if isinstance(error, Exception) else None
                self._release_duration_ms = (time.perf_counter() - started) * 1000
                raise
            self._release_duration_ms = (time.perf_counter() - started) * 1000
            self._active_model = None
            self._state = "inactive"
            self._last_error = None
            return self.snapshot()

    def _activate_locked(self, model: str) -> None:
        deadline = time.monotonic() + self._transition_timeout_seconds
        while self._leases and self._active_model != model:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError(
                    "provider_timeout",
                    "Timed out waiting for an active generation before model activation.",
                    retryable=True,
                )
            self._condition.wait(remaining)

        loaded = self._provider.loaded_models()
        if self._active_model == model and model in loaded:
            self._state = "active"
            return

        previous = self._active_model
        started = time.perf_counter()
        self._state = "activating"
        try:
            if previous is not None and previous != model and previous in loaded:
                self._state = "releasing"
                self._provider.unload_model(previous)
                if previous in self._provider.loaded_models():
                    raise ProviderError(
                        "provider_model_release_unverified",
                        f"Ollama still reports {previous!r} as resident during model switching.",
                    )
                self._active_model = None
            self._state = "activating"
            self._provider.load_model(model)
            if model not in self._provider.loaded_models():
                raise ProviderError(
                    "provider_model_activation_unverified",
                    f"Ollama did not report {model!r} as resident after activation.",
                )
        except BaseException as error:
            self._state = "failed" if isinstance(error, Exception) else "cancelled"
            self._last_error = str(error) if isinstance(error, Exception) else None
            self._activation_duration_ms = (time.perf_counter() - started) * 1000
            try:
                actual = self._provider.loaded_models()
            except Exception:
                self._active_model = None
            else:
                self._active_model = (
                    previous
                    if previous is not None and previous in actual
                    else model
                    if model in actual
                    else None
                )
            raise
        self._active_model = model
        self._state = "active"
        self._last_error = None
        self._activation_duration_ms = (time.perf_counter() - started) * 1000
