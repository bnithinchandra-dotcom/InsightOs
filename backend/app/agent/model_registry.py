"""
Model registry for InsightOS — static capability and eligibility metadata.

This module maintains a catalogue of all model identifiers that InsightOS is
permitted to dispatch.  A model must be present in this registry and marked
``validated`` (or ``eligible``) for the relevant ``AgentRole`` before the
``AgentModelRouter`` will send work to it.

Design decisions
----------------
* The registry is intentionally static (no database, no network, no Ollama
  call).  Dynamic availability (whether the model is actually loaded) is a
  separate concern owned by ``OllamaProvider.check_health()``.
* Validation status ("validated" vs "candidate") is distinct from Ollama
  installation status.  A model may be installed in Ollama but still be
  ``candidate`` here until an approved live validation test confirms it meets
  InsightOS structured-output and tool-calling requirements.
* Only ``qwen3:8b`` is ``validated`` today.  All other entries are
  ``candidate`` — they exist in the registry so the router can accept them if
  an operator explicitly sets the role env vars, but they are not active by
  default.

Adding a new model
------------------
1. Add an entry to ``MODEL_REGISTRY`` below.
2. Set ``validation_status`` to ``"candidate"``.
3. Run an approved live validation phase (separate authorization required).
4. Update ``validation_status`` to ``"validated"`` after the phase passes.

Cross-process note
------------------
The ``AgentModelRouter`` semaphore is in-process only.  If the worker process
ever calls a model, a Redis-backed distributed lock must be added before that
point.  The worker currently does not call any model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.agent.contracts import ProviderError


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

MemoryTier = Literal["small", "medium", "large"]
ValidationStatus = Literal["validated", "candidate"]

# Map from AgentRole strings to capability requirement labels.
AgentRole = Literal[
    "orchestrator",
    "data_intelligence",
    "analytics_modelling",
    "insights_visualization",
]

# Required capability labels that an adapter must declare for a role to be
# eligible.  These align with the capability flags on ProviderCapabilities.
_ROLE_REQUIRED_CAPABILITIES: dict[AgentRole, frozenset[str]] = {
    "orchestrator": frozenset({"text_generation", "tool_calling", "structured_output"}),
    "data_intelligence": frozenset({"text_generation", "structured_output"}),
    "analytics_modelling": frozenset({"text_generation", "structured_output"}),
    "insights_visualization": frozenset({"text_generation"}),
}


@dataclass(frozen=True)
class ModelEntry:
    """Static registry entry for one model identifier."""

    # Stable model identifier as recognised by the runtime (e.g. Ollama tag).
    model_id: str

    # Provider runtime that hosts this model.
    provider: Literal["ollama", "openai_compatible"]

    # Human-readable description of model purpose.
    description: str

    # Approximate peak RAM tier under InsightOS's configured token limits.
    # "small"  → < 2 GB  (e.g. sub-1B models)
    # "medium" → 2–5 GB  (e.g. 4B-class models at q4_K_M)
    # "large"  → 5+ GB   (e.g. 8B-class models at q4_K_M)
    memory_tier: MemoryTier

    # Capability flags that this model is known/believed to support.
    # Must be a subset of ProviderCapabilities field names with "supported" status.
    # These are assertions, not runtime-verified values.
    declared_capabilities: frozenset[str]

    # AgentRoles for which this model is permitted to be dispatched.
    eligible_roles: frozenset[AgentRole]

    # Whether this model has passed an approved live InsightOS validation phase.
    # "validated" → confirmed; "candidate" → not yet confirmed.
    validation_status: ValidationStatus

    # Whether this model is eligible as a default fallback when no role
    # override env var is set.  Only one entry should have this True.
    is_default: bool = False

    # Optional documentation notes (not used at runtime).
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.model_id or not isinstance(self.model_id, str):
            raise ValueError("model_id must be a non-empty string.")
        if not self.eligible_roles:
            raise ValueError(f"{self.model_id}: eligible_roles must not be empty.")
        if not self.declared_capabilities:
            raise ValueError(f"{self.model_id}: declared_capabilities must not be empty.")


# ---------------------------------------------------------------------------
# Registry catalogue
# ---------------------------------------------------------------------------
# IMPORTANT: Only ``qwen3:8b`` is marked ``validation_status="validated"``.
# All other models are ``"candidate"`` until an approved live validation phase
# confirms their behaviour meets InsightOS contracts.
# ---------------------------------------------------------------------------

_REGISTRY_ENTRIES: tuple[ModelEntry, ...] = (
    ModelEntry(
        model_id="qwen3:8b",
        provider="ollama",
        description=(
            "Default InsightOS orchestrator model.  8B-parameter Qwen3 with "
            "extended thinking disabled (think:false) for structured output. "
            "Tool calling and JSON output confirmed end-to-end."
        ),
        memory_tier="large",
        declared_capabilities=frozenset({
            "text_generation",
            "tool_calling",
            "structured_output",
        }),
        eligible_roles=frozenset({
            "orchestrator",
            "data_intelligence",
            "analytics_modelling",
            "insights_visualization",
        }),
        validation_status="validated",
        is_default=True,
        notes=(
            "Configured via OLLAMA_MODEL=qwen3:8b.  The OllamaProvider "
            "automatically sets think:false for all qwen3* model names."
        ),
    ),
    ModelEntry(
        model_id="qwen3:4b-instruct",
        provider="ollama",
        description=(
            "4B-parameter instruction-tuned Qwen3 variant.  Candidate for "
            "data_intelligence and analytics_modelling specialist roles. "
            "Lower RAM than 8b; instruct variant suppresses extended thinking."
        ),
        memory_tier="medium",
        declared_capabilities=frozenset({
            "text_generation",
            "structured_output",
        }),
        eligible_roles=frozenset({
            "data_intelligence",
            "analytics_modelling",
            "insights_visualization",
        }),
        validation_status="candidate",
        notes=(
            "Tool calling is NOT declared for this model — it has not been "
            "confirmed to produce valid tool-call JSON for InsightOS schemas. "
            "Do not assign to orchestrator role until tool calling is verified."
        ),
    ),
    ModelEntry(
        model_id="qwen3:4b",
        provider="ollama",
        description=(
            "4B-parameter thinking-capable Qwen3 variant.  Candidate for "
            "specialist roles.  Uses think:false via OllamaProvider for "
            "structured output requests."
        ),
        memory_tier="medium",
        declared_capabilities=frozenset({
            "text_generation",
            "structured_output",
        }),
        eligible_roles=frozenset({
            "data_intelligence",
            "analytics_modelling",
            "insights_visualization",
        }),
        validation_status="candidate",
        notes=(
            "Tool calling unverified.  Thinking mode consumes extra context "
            "and RAM unless suppressed.  OllamaProvider suppresses it for "
            "qwen3* models.  Do not assign to orchestrator role."
        ),
    ),
    ModelEntry(
        model_id="qwen3:0.6b",
        provider="ollama",
        description=(
            "0.6B-parameter Qwen3 model.  Very low RAM footprint.  Candidate "
            "for insights_visualization (presentation-only) role if language "
            "quality proves sufficient.  No numeric claims allowed in that role."
        ),
        memory_tier="small",
        declared_capabilities=frozenset({"text_generation"}),
        eligible_roles=frozenset({"insights_visualization"}),
        validation_status="candidate",
        notes=(
            "Likely insufficient for complex structured output.  Evaluate only "
            "for the presentation role where numeric validation already blocks "
            "arithmetic assertions.  Do NOT use for orchestrator or analytics."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Registry interface
# ---------------------------------------------------------------------------

class ModelRegistry:
    """
    Immutable in-process model registry.

    All routing decisions that need to validate a model identifier should
    call :meth:`require_eligible` before dispatching.
    """

    def __init__(self, entries: tuple[ModelEntry, ...] = _REGISTRY_ENTRIES) -> None:
        self._by_id: dict[str, ModelEntry] = {}
        for entry in entries:
            if entry.model_id in self._by_id:
                raise ValueError(
                    f"Duplicate model_id in registry: {entry.model_id!r}"
                )
            self._by_id[entry.model_id] = entry

        # Exactly one default must be present.
        defaults = [e for e in entries if e.is_default]
        if len(defaults) != 1:
            raise ValueError(
                f"ModelRegistry requires exactly one default entry; found {len(defaults)}."
            )
        self._default = defaults[0]

    # ------------------------------------------------------------------
    # Public query helpers
    # ------------------------------------------------------------------

    def get(self, model_id: str) -> ModelEntry | None:
        """Return the registry entry for *model_id*, or ``None`` if absent."""
        return self._by_id.get(model_id)

    def require(self, model_id: str) -> ModelEntry:
        """
        Return the entry for *model_id* or raise :class:`ProviderError` if
        the model is not in the registry at all.
        """
        entry = self._by_id.get(model_id)
        if entry is None:
            raise ProviderError(
                "provider_model_not_registered",
                (
                    f"Model {model_id!r} is not in the InsightOS model registry. "
                    "Only registered model identifiers may be dispatched."
                ),
            )
        return entry

    def require_eligible(self, model_id: str, role: AgentRole) -> ModelEntry:
        """
        Return the entry for *model_id* after verifying it is eligible for
        *role*, or raise :class:`ProviderError`.

        Checks (in order):
        1. Model is in the registry.
        2. Role is in the entry's ``eligible_roles``.
        3. Model declares all capabilities required by the role.

        Note: this method does NOT check ``validation_status``.  Operators may
        explicitly configure a ``candidate`` model via env vars for testing.
        The router logs a warning when dispatching a candidate model.
        """
        entry = self.require(model_id)
        if role not in entry.eligible_roles:
            raise ProviderError(
                "provider_model_ineligible",
                (
                    f"Model {model_id!r} is not eligible for the {role!r} role. "
                    f"Eligible roles: {sorted(entry.eligible_roles)}."
                ),
            )
        required = _ROLE_REQUIRED_CAPABILITIES.get(role, frozenset())
        missing = required - entry.declared_capabilities
        if missing:
            raise ProviderError(
                "provider_model_capability_missing",
                (
                    f"Model {model_id!r} does not declare the capabilities "
                    f"required for the {role!r} role: {sorted(missing)}."
                ),
            )
        return entry

    def default_model_id(self) -> str:
        """Return the model_id of the registry's default entry."""
        return self._default.model_id

    def all_entries(self) -> tuple[ModelEntry, ...]:
        """Return all registry entries in definition order."""
        return tuple(self._by_id.values())

    def validated_entries(self) -> tuple[ModelEntry, ...]:
        """Return only entries with ``validation_status == "validated"``."""
        return tuple(e for e in self._by_id.values() if e.validation_status == "validated")


# ---------------------------------------------------------------------------
# Module-level singleton — import this in model_router.py
# ---------------------------------------------------------------------------

#: The shared model registry instance used across the application.
#: Constructed once at import time from the static ``_REGISTRY_ENTRIES`` above.
REGISTRY = ModelRegistry()

