import unittest

from app.agent.business_intelligence import (
    BI_PROMPT_VERSION,
    BUSINESS_METRIC_CATALOG,
    MODEL_SPECIALIZATION_PROFILES,
    SPECIALIST_PROMPT_PROFILES,
    get_business_metric,
    specialist_prompt,
)
from app.agent.model_registry import REGISTRY


class BusinessIntelligenceProfileTests(unittest.TestCase):
    def test_role_profiles_are_versioned_and_keep_existing_agent_roles(self):
        self.assertEqual(
            set(SPECIALIST_PROMPT_PROFILES),
            {
                "orchestrator",
                "data_intelligence",
                "analytics_modelling",
                "insights_visualization",
            },
        )
        self.assertTrue(
            all(
                profile.version == BI_PROMPT_VERSION
                for profile in SPECIALIST_PROMPT_PROFILES.values()
            )
        )
        self.assertEqual(
            SPECIALIST_PROMPT_PROFILES["analytics_modelling"].title,
            "Data Analysis Associate",
        )
        self.assertEqual(
            SPECIALIST_PROMPT_PROFILES["insights_visualization"].title,
            "Reporting Specialist",
        )

    def test_prompt_variants_preserve_baseline_and_add_model_specific_scope(self):
        baseline = "Use the existing system prompt."
        self.assertEqual(
            specialist_prompt(
                "analytics_modelling",
                "baseline",
                baseline_instruction=baseline,
                model_id="qwen3:4b",
            ),
            baseline,
        )
        optimized = specialist_prompt(
            "analytics_modelling",
            "optimized",
            baseline_instruction=baseline,
            model_id="qwen3:4b",
        )
        self.assertIn("exact requested allowlisted tool", optimized)
        self.assertIn("fixed checklist", optimized)
        self.assertNotIn(baseline, optimized)

    def test_model_specialization_profiles_do_not_change_registry_approval(self):
        self.assertEqual(
            MODEL_SPECIALIZATION_PROFILES["qwen3:8b"].intended_role,
            "senior_business_analyst",
        )
        self.assertEqual(
            MODEL_SPECIALIZATION_PROFILES["qwen3:0.6b"].intended_role,
            "reporting_specialist",
        )
        self.assertEqual(REGISTRY.require("qwen3:8b").validation_status, "validated")
        for model in ("qwen3:4b", "qwen3:4b-instruct", "qwen3:0.6b"):
            self.assertEqual(REGISTRY.require(model).validation_status, "candidate")

    def test_metric_catalog_has_explicit_inputs_provenance_and_engine_references(self):
        self.assertGreaterEqual(len(BUSINESS_METRIC_CATALOG), 20)
        self.assertEqual(
            len({metric.key for metric in BUSINESS_METRIC_CATALOG}),
            len(BUSINESS_METRIC_CATALOG),
        )
        self.assertTrue(
            all(
                metric.required_inputs
                and metric.aggregation
                and metric.unit
                and metric.validation_rules
                and metric.provenance_requirements
                and metric.engine_reference
                for metric in BUSINESS_METRIC_CATALOG
            )
        )
        self.assertEqual(
            get_business_metric("churn_rate").definition_status,
            "requires_business_definition",
        )
        self.assertEqual(
            get_business_metric("duplicate_row_count").engine_reference,
            "app.agent.tools.run_quality_analysis",
        )
        self.assertIsNone(get_business_metric("user_defined_metric"))


if __name__ == "__main__":
    unittest.main()
