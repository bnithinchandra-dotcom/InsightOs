import unittest

from app.dashboard_insights import (
    DashboardAnalyticsError,
    build_chart_spec,
    build_eda_report,
    build_report,
)
from app.provenance import build_provenance, validate_provenance


def sample_result():
    return {
        "dimensions": ["customers.region"],
        "metrics": [
            {
                "name": "revenue",
                "operation": "sum",
                "column": "sales.amount",
            }
        ],
        "input_sources": [
            {
                "alias": "customers",
                "dataset_id": 3,
                "file_id": 11,
                "table_index": 0,
                "source_sha256": "a" * 64,
                "row_count": 4,
            },
            {
                "alias": "sales",
                "dataset_id": 4,
                "file_id": 22,
                "table_index": 0,
                "source_sha256": "b" * 64,
                "row_count": 4,
            },
        ],
        "joined_row_count": 4,
        "group_count": 2,
        "results": [
            {"customers.region": "east", "revenue": 12},
            {"customers.region": "west", "revenue": 25},
        ],
        "result_evidence": [
            {
                "customers.region": "east",
                "group_row_count": 2,
                "metrics": [
                    {
                        "name": "revenue",
                        "value": 12,
                        "valid_count": 2,
                        "missing_count": 0,
                    }
                ],
            },
            {
                "customers.region": "west",
                "group_row_count": 2,
                "metrics": [
                    {
                        "name": "revenue",
                        "value": 25,
                        "valid_count": 1,
                        "missing_count": 1,
                    }
                ],
            },
        ],
        "warnings": [],
    }


def bar_request():
    return {
        "title": "Revenue by region",
        "chart_type": "bar",
        "x_field": "customers.region",
        "y_field": "revenue",
        "series_field": None,
    }


def stored_eda():
    provenance = build_provenance(
        operation_type="eda_analysis",
        operation_version="1",
        configuration_sha256="d" * 64,
        status="completed",
        inputs=[
            {
                "kind": "normalized_parquet",
                "dataset_id": 3,
                "file_id": 11,
                "table_index": 0,
                "sha256": "a" * 64,
            }
        ],
        outputs=[{"sha256": "b" * 64}],
        verification_status="verified",
        checks=[{"name": "analysis", "status": "verified"}],
    )
    return {
        "id": 9,
        "analysis_type": "univariate",
        "status": "completed",
        "source": {
            "dataset_id": 3,
            "file_id": 11,
            "table_index": 0,
            "artifact_sha256": "a" * 64,
        },
        "configuration_sha256": "d" * 64,
        "rows_analyzed": 4,
        "warnings": [],
        "columns": [
            {
                "name": "amount",
                "status": "completed",
                "non_null_count": 3,
                "null_count": 1,
                "statistics": {
                    "mean": 4.0,
                    "median": 4.0,
                    "histogram": {
                        "edges": [1.0, 4.0, 7.0],
                        "counts": [2, 1],
                    },
                },
                "warnings": [],
            }
        ],
        "pair_results": [],
    }, provenance


class DashboardInsightsTests(unittest.TestCase):
    def test_report_findings_are_grounded_and_provenance_is_valid(self):
        result = sample_result()
        report = build_report({"sources": []}, result, [bar_request()])

        self.assertEqual(report["summary"]["findings_count"], 2)
        self.assertEqual(report["findings"][0]["kind"], "measured_fact")
        self.assertEqual(report["findings"][0]["supporting_metric"]["value"], 12)
        self.assertEqual(
            report["findings"][0]["source_references"][0]["file_id"],
            11,
        )
        self.assertIsNone(report["findings"][0]["interpretation"])
        self.assertIsNone(report["findings"][0]["recommendation"])
        self.assertTrue(any("missing or invalid" in item for item in report["warnings"]))
        self.assertTrue(any("fewer than 30" in item for item in report["warnings"]))
        self.assertEqual(validate_provenance(report["provenance"]), report["provenance"])

    def test_fabricated_or_missing_metric_is_rejected_for_charts(self):
        with self.assertRaises(DashboardAnalyticsError) as raised:
            build_chart_spec(
                sample_result(),
                {**bar_request(), "y_field": "invented_metric"},
            )
        self.assertEqual(raised.exception.code, "chart_field_unavailable")

    def test_scatter_requires_numeric_axes_and_pie_requires_positive_values(self):
        result = sample_result()
        with self.assertRaises(DashboardAnalyticsError) as raised:
            build_chart_spec(
                result,
                {
                    **bar_request(),
                    "chart_type": "scatter",
                },
            )
        self.assertEqual(raised.exception.code, "scatter_numeric_fields_required")

        result["results"] = [
            {"customers.region": "east", "revenue": -2},
            {"customers.region": "west", "revenue": 2},
        ]
        with self.assertRaises(DashboardAnalyticsError) as pie:
            build_chart_spec(
                result,
                {**bar_request(), "chart_type": "pie"},
            )
        self.assertEqual(pie.exception.code, "pie_values_invalid")

    def test_chart_points_are_bounded(self):
        result = sample_result()
        result["results"] = [
            {"customers.region": str(index), "revenue": index}
            for index in range(250)
        ]
        chart = build_chart_spec(result, bar_request())

        self.assertEqual(len(chart["data"]["labels"]), 200)
        self.assertTrue(chart["warnings"])

    def test_line_chart_orders_numeric_x_values_before_specification(self):
        result = sample_result()
        result["dimensions"] = ["period"]
        result["metrics"] = [{"name": "revenue", "operation": "sum", "column": "sales.amount"}]
        result["results"] = [
            {"period": 10, "revenue": 100},
            {"period": 2, "revenue": 20},
        ]
        chart = build_chart_spec(
            result,
            {
                "title": "Revenue over time",
                "chart_type": "line",
                "x_field": "period",
                "y_field": "revenue",
            },
        )

        self.assertEqual(chart["data"]["labels"], ["2", "10"])
        self.assertEqual(chart["data"]["x_values"], [2, 10])

    def test_report_response_size_is_bounded(self):
        result = sample_result()
        values = [
            {"customers.region": f"{index}-{'x' * 800}", "revenue": index}
            for index in range(1500)
        ]
        result["results"] = values
        result["result_evidence"] = [
            {
                "customers.region": row["customers.region"],
                "group_row_count": 1,
                "metrics": [
                    {
                        "name": "revenue",
                        "value": row["revenue"],
                        "valid_count": 1,
                        "missing_count": 0,
                    }
                ],
            }
            for row in values
        ]
        result["group_count"] = len(values)

        with self.assertRaises(DashboardAnalyticsError) as raised:
            build_report({"sources": []}, result, [])
        self.assertEqual(raised.exception.code, "report_size_limit")

    def test_stored_eda_metrics_and_histogram_are_reported_with_parent_provenance(self):
        analysis, provenance = stored_eda()

        report = build_eda_report(analysis, provenance, [])

        self.assertTrue(any(item["supporting_metric"]["name"] == "mean" for item in report["findings"]))
        self.assertEqual(report["charts"][0]["aggregation"], "histogram_count")
        self.assertEqual(report["charts"][0]["data"]["series"][0]["values"], [2, 1])
        self.assertEqual(
            report["provenance"]["parent_operation_ids"],
            [provenance["operation_id"]],
        )

    def test_eda_report_rejects_an_unanalyzed_chart_column(self):
        analysis, provenance = stored_eda()
        with self.assertRaises(DashboardAnalyticsError) as raised:
            build_eda_report(analysis, provenance, ["fabricated"])
        self.assertEqual(raised.exception.code, "eda_chart_column_invalid")


if __name__ == "__main__":
    unittest.main()
