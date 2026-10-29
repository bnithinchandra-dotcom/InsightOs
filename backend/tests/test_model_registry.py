"""Unit tests for app.agent.model_registry.

All tests are fully offline – no Ollama calls, no network, no DB.
"""

from __future__ import annotations

import pytest

from app.agent.config import AgentSettings
from app.agent.contracts import ProviderError
from app.agent.model_registry import ModelEntry, ModelRegistry, REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(**kwargs) -> ModelEntry:
    defaults = dict(
        model_id="test:model",
        provider="ollama",
        description="A test model.",
        memory_tier="medium",
        declared_capabilities=frozenset({"text_generation"}),
        eligible_roles=frozenset({"orchestrator"}),
        validation_status="validated",
        is_default=False,
    )
    defaults.update(kwargs)
    return ModelEntry(**defaults)


def _full_settings(**overrides) -> AgentSettings:
    """Return a complete AgentSettings suitable for tests."""
    base = dict(
        provider="ollama",
        base_url="http://127.0.0.1:11434",
        model="qwen3:8b",
        api_key=None,
        timeout_seconds=30,
        max_output_tokens=512,
        max_model_turns=3,
        max_tool_calls=4,
        max_history_messages=10,
    )
    base.update(overrides)
    return AgentSettings(**base)


# ---------------------------------------------------------------------------
# REGISTRY singleton tests
# ---------------------------------------------------------------------------

class TestRegistrySingleton:
    def test_registry_has_exactly_one_default(self):
        defaults = [e for e in REGISTRY.all_entries() if e.is_default]
        assert len(defaults) == 1
        assert defaults[0].model_id == "qwen3:8b"

    def test_default_model_id_returns_qwen3_8b(self):
        assert REGISTRY.default_model_id() == "qwen3:8b"

    def test_all_entries_returns_all_four_models(self):
        ids = {e.model_id for e in REGISTRY.all_entries()}
        assert ids == {
            "qwen3:8b",
            "qwen3:4b-instruct",
            "qwen3:4b",
            "qwen3:0.6b",
        }

    def test_validated_entries_returns_only_qwen3_8b(self):
        validated = list(REGISTRY.validated_entries())
        assert len(validated) == 1
        assert validated[0].model_id == "qwen3:8b"

    def test_qwen3_8b_eligible_for_all_roles(self):
        """Default model must pass require_eligible for every defined role."""
        for role in ("orchestrator", "data_intelligence", "analytics_modelling", "insights_visualization"):
            entry = REGISTRY.require_eligible("qwen3:8b", role)  # must not raise
            assert entry.model_id == "qwen3:8b"

    def test_unregistered_model_raises_provider_error(self):
        with pytest.raises(ProviderError) as exc_info:
            REGISTRY.require("unknown:model")
        assert exc_info.value.code == "provider_model_not_registered"

    def test_ineligible_role_raises_provider_error(self):
        """qwen3:0.6b is only eligible for insights_visualization."""
        with pytest.raises(ProviderError) as exc_info:
            REGISTRY.require_eligible("qwen3:0.6b", "orchestrator")
        assert exc_info.value.code == "provider_model_ineligible"

    def test_missing_capability_via_custom_registry(self):
        """A model eligible for a role but missing a required capability raises capability_missing."""
        # Build a custom registry: model is eligible for orchestrator but declares
        # only text_generation — orchestrator also requires tool_calling and structured_output.
        partial_caps = _make_entry(
            model_id="partial:model",
            declared_capabilities=frozenset({"text_generation"}),
            eligible_roles=frozenset({"orchestrator"}),
            is_default=True,  # registry requires exactly one default
        )
        reg = ModelRegistry(entries=(partial_caps,))
        with pytest.raises(ProviderError) as exc_info:
            reg.require_eligible("partial:model", "orchestrator")
        assert exc_info.value.code == "provider_model_capability_missing"

    def test_instruct_model_missing_capability_for_orchestrator(self):
        """qwen3:4b-instruct raises ineligible for orchestrator (role check precedes capability check)."""
        with pytest.raises(ProviderError) as exc_info:
            REGISTRY.require_eligible("qwen3:4b-instruct", "orchestrator")
        # ineligible is raised first because the role is not in eligible_roles
        assert exc_info.value.code == "provider_model_ineligible"

    def test_candidate_model_accepted_when_role_eligible(self):
        """qwen3:4b-instruct passes for data_intelligence (no tool_calling needed)."""
        entry = REGISTRY.require_eligible("qwen3:4b-instruct", "data_intelligence")
        assert entry.model_id == "qwen3:4b-instruct"
        assert entry.validation_status == "candidate"

    def test_qwen3_4b_eligible_for_analytics_modelling(self):
        """qwen3:4b must pass for analytics_modelling (needed by existing router tests)."""
        entry = REGISTRY.require_eligible("qwen3:4b", "analytics_modelling")
        assert entry.model_id == "qwen3:4b"

    def test_qwen3_0_6b_eligible_for_insights_visualization(self):
        """qwen3:0.6b must pass for insights_visualization (needed by existing router tests)."""
        entry = REGISTRY.require_eligible("qwen3:0.6b", "insights_visualization")
        assert entry.model_id == "qwen3:0.6b"


# ---------------------------------------------------------------------------
# ModelRegistry construction tests
# ---------------------------------------------------------------------------

class TestModelRegistryConstruction:
    def test_duplicate_model_id_raises_value_error(self):
        e1 = _make_entry(model_id="dup:model", is_default=True)
        e2 = _make_entry(model_id="dup:model")
        with pytest.raises(ValueError, match="Duplicate model_id"):
            ModelRegistry(entries=(e1, e2))

    def test_no_default_raises_value_error(self):
        """A registry with no is_default=True entry raises ValueError."""
        e = _make_entry(model_id="no-default:model", is_default=False)
        with pytest.raises(ValueError, match="exactly one default"):
            ModelRegistry(entries=(e,))

    def test_multiple_defaults_raise_value_error(self):
        e1 = _make_entry(model_id="a:model", is_default=True)
        e2 = _make_entry(model_id="b:model", is_default=True)
        with pytest.raises(ValueError, match="exactly one default"):
            ModelRegistry(entries=(e1, e2))


# ---------------------------------------------------------------------------
# ModelEntry validation tests
# ---------------------------------------------------------------------------

class TestModelEntryValidation:
    def test_empty_model_id_raises(self):
        with pytest.raises(ValueError, match="model_id"):
            _make_entry(model_id="")

    def test_empty_eligible_roles_raises(self):
        with pytest.raises(ValueError, match="eligible_roles"):
            _make_entry(eligible_roles=frozenset())

    def test_empty_declared_capabilities_raises(self):
        with pytest.raises(ValueError, match="declared_capabilities"):
            _make_entry(declared_capabilities=frozenset())

    def test_valid_entry_does_not_raise(self):
        entry = _make_entry()
        assert entry.model_id == "test:model"


# ---------------------------------------------------------------------------
# AgentSettings keep_alive integration
# ---------------------------------------------------------------------------

class TestAgentSettingsKeepAlive:
    def test_zero_keep_alive_accepted(self):
        s = _full_settings(ollama_keep_alive_seconds=0)
        assert s.ollama_keep_alive_seconds == 0

    def test_positive_keep_alive_accepted(self):
        s = _full_settings(ollama_keep_alive_seconds=300)
        assert s.ollama_keep_alive_seconds == 300

    def test_none_keep_alive_is_default(self):
        s = _full_settings()
        assert s.ollama_keep_alive_seconds is None
