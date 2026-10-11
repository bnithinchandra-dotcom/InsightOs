import unittest

from app.quality_catalog import QUALITY_RULE_CATALOG_VERSION
from app.quality_planning import QualityPlanningContext, plan_quality_rules


def complete_context():
    return QualityPlanningContext(
        dataset_id=12,
        file_id=34,
        dataset_status="Ready",
        file_status="Ready",
        detected_format="csv",
        profile_result={
            "tables": [
                {
                    "duplicate_row_count": 0,
                    "duplicate_row_count_exact": True,
                }
            ]
        },
        normalization_result={
            "tables": [
                {
                    "object_key": "12/34/output.parquet",
                    "row_count": 4,
                    "column_count": 3,
                    "schema": [
                        {"name": "free_text", "type": "string"},
                        {"name": "numeric_value", "type": "double"},
                        {"name": "uninterpreted", "type": "int64"},
                    ],
                    "null_value_counts": [0, 0, 0],
                    "empty_value_counts": [0, 0, 0],
                }
            ]
        },
        quality_report={
            "coverage": {
                "checks": [
                    {
                        "check_id": (
                            "quality_rules.rule.generic.numeric_outlier_candidates"
                        ),
                        "status": "Completed",
                    }
                ]
            }
        },
        quality_report_current=True,
    )


class QualityPlanningTests(unittest.TestCase):
    def test_plan_is_deterministic_and_reports_current_quality_context(self):
        first = plan_quality_rules(complete_context())
        second = plan_quality_rules(complete_context())
        self.assertEqual(first, second)
        response = first.model_dump(mode="json")
        self.assertEqual(response["catalog_version"], QUALITY_RULE_CATALOG_VERSION)
        self.assertEqual(response["catalog_version"], "2")
        self.assertEqual(response["context"]["dataset_id"], 12)
        self.assertTrue(response["context"]["current_quality_report_available"])
        outlier = next(
            item
            for item in response["recommendations"]
            if item["rule_id"] == "generic.numeric_outlier_candidates"
        )
        self.assertEqual(outlier["planning_status"], "ready")
        self.assertTrue(outlier["execution_supported"])
        self.assertEqual(outlier["previous_execution_status"], "Completed")

    def test_missing_normalization_context_blocks_applicable_rules(self):
        context = complete_context()
        context = QualityPlanningContext(
            dataset_id=context.dataset_id,
            file_id=context.file_id,
            dataset_status="Uploaded",
            file_status="Ready",
            detected_format="csv",
            profile_result=context.profile_result,
            normalization_result=None,
            quality_report=None,
            quality_report_current=False,
        )
        plan = plan_quality_rules(context, status="implemented")
        for recommendation in plan.recommendations:
            if recommendation.status == "implemented":
                self.assertEqual(recommendation.planning_status, "needs_context")
                self.assertFalse(recommendation.execution_supported)
                self.assertTrue(recommendation.missing_context)

    def test_planned_and_partial_rules_are_never_execution_recommendations(self):
        plan = plan_quality_rules(complete_context())
        non_implemented = [
            item
            for item in plan.recommendations
            if item.status in {"partial", "planned"}
        ]
        self.assertTrue(non_implemented)
        for item in non_implemented:
            self.assertEqual(item.planning_status, item.status)
            self.assertFalse(item.execution_supported)
            self.assertIn("cannot", item.rationale)

    def test_explicit_business_key_requires_a_user_decision(self):
        plan = plan_quality_rules(
            complete_context(),
            status="implemented",
        )
        business_key = next(
            item
            for item in plan.recommendations
            if item.rule_id == "configured.business_key_duplicate_candidates"
        )
        self.assertTrue(business_key.requires_user_decision)
        self.assertIn(
            "user decision: explicit_business_key_definition",
            business_key.missing_context,
        )
        self.assertFalse(business_key.execution_supported)

    def test_catalog_filters_are_applied_without_reordering(self):
        all_rules = plan_quality_rules(complete_context()).recommendations
        planned_ba = plan_quality_rules(
            complete_context(),
            category="BA",
            status="planned",
        ).recommendations
        self.assertTrue(planned_ba)
        self.assertTrue(
            all(item.status == "planned" for item in planned_ba)
        )
        self.assertTrue(
            all(
                item.rule_id.startswith("ba.")
                for item in planned_ba
            )
        )
        expected = [
            item.rule_id
            for item in all_rules
            if item.status == "planned" and item.rule_id.startswith("ba.")
        ]
        self.assertEqual(
            [item.rule_id for item in planned_ba],
            expected,
        )

    def test_quality_report_status_is_ignored_when_report_is_stale(self):
        context = complete_context()
        stale_context = QualityPlanningContext(
            dataset_id=context.dataset_id,
            file_id=context.file_id,
            dataset_status=context.dataset_status,
            file_status=context.file_status,
            detected_format=context.detected_format,
            profile_result=context.profile_result,
            normalization_result=context.normalization_result,
            quality_report=context.quality_report,
            quality_report_current=False,
        )
        plan = plan_quality_rules(stale_context)
        outlier = next(
            item
            for item in plan.recommendations
            if item.rule_id == "generic.numeric_outlier_candidates"
        )
        self.assertIsNone(outlier.previous_execution_status)
        self.assertFalse(plan.context.current_quality_report_available)

    def test_column_names_do_not_create_business_semantics(self):
        context = complete_context()
        tables = [
            dict(table)
            for table in context.normalization_result["tables"]
        ]
        tables[0]["schema"] = [
            {"name": "revenue", "type": "double"},
        ]
        semantic_context = QualityPlanningContext(
            dataset_id=context.dataset_id,
            file_id=context.file_id,
            dataset_status=context.dataset_status,
            file_status=context.file_status,
            detected_format=context.detected_format,
            profile_result=context.profile_result,
            normalization_result={"tables": tables},
            quality_report=None,
            quality_report_current=False,
        )
        plan = plan_quality_rules(semantic_context, category="BA")
        self.assertTrue(all(item.status == "planned" for item in plan.recommendations))
        self.assertTrue(all(not item.execution_supported for item in plan.recommendations))
