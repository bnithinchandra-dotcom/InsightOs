from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from app.provenance import build_provenance, canonical_sha256, validate_provenance


MAX_CHARTS = 8
MAX_CHART_POINTS = 200
MAX_CHART_SERIES = 8
MAX_FINDINGS = 500
MAX_REPORT_WARNINGS = 100
MAX_REPORT_BYTES = 1024 * 1024


class DashboardAnalyticsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise DashboardAnalyticsError(
        "invalid_result_value",
        "Analytics returned a value that cannot be represented safely.",
    )


def build_chart_spec(
    result: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    chart_type = request["chart_type"]
    x_field = request["x_field"]
    y_field = request["y_field"]
    series_field = request.get("series_field")
    rows = result.get("results")
    dimensions = result.get("dimensions")
    metrics = result.get("metrics")
    if not isinstance(rows, list) or not rows:
        raise DashboardAnalyticsError(
            "chart_data_empty",
            "Charts require at least one grouped analytics result.",
        )
    available_fields = set(dimensions or [])
    metric_names = {
        item.get("name")
        for item in metrics or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    available_fields.update(metric_names)
    if x_field not in available_fields or y_field not in metric_names:
        raise DashboardAnalyticsError(
            "chart_field_unavailable",
            "Chart fields must reference an analytics dimension and metric in the result.",
        )
    if series_field is not None and series_field not in set(dimensions or []):
        raise DashboardAnalyticsError(
            "chart_series_unavailable",
            "The chart series field must reference an analytics dimension.",
        )
    if any(not isinstance(row, dict) or x_field not in row or y_field not in row for row in rows):
        raise DashboardAnalyticsError(
            "chart_result_invalid",
            "Analytics rows do not contain all requested chart fields.",
        )
    if chart_type in {"bar", "line", "pie"}:
        if any(row[x_field] is None for row in rows):
            raise DashboardAnalyticsError(
                "chart_null_category",
                "Categorical chart axes cannot contain null values.",
            )
        if any(not _finite_number(row[y_field]) for row in rows):
            raise DashboardAnalyticsError(
                "chart_numeric_metric_required",
                "The selected chart metric must contain numeric values.",
            )
        if chart_type == "pie" and series_field is not None:
            raise DashboardAnalyticsError(
                "chart_series_unsupported",
                "Pie charts do not support a separate series field.",
            )
        if chart_type == "pie":
            pie_total = sum(row[y_field] for row in rows)
            if (
                any(row[y_field] < 0 for row in rows)
                or not math.isfinite(pie_total)
                or pie_total <= 0
            ):
                raise DashboardAnalyticsError(
                    "pie_values_invalid",
                    "Pie charts require non-negative values with a finite positive total.",
                )
        if chart_type == "line" and not all(_finite_number(row[x_field]) for row in rows):
            try:
                for row in rows:
                    datetime.fromisoformat(row[x_field].replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError):
                raise DashboardAnalyticsError(
                    "line_ordering_required",
                    "Line charts require numeric or ISO-formatted date/time x values.",
                ) from None
    elif chart_type == "scatter":
        if any(not _finite_number(row[x_field]) or not _finite_number(row[y_field]) for row in rows):
            raise DashboardAnalyticsError(
                "scatter_numeric_fields_required",
                "Scatter charts require numeric x and y fields.",
            )
        if series_field is not None and len({str(row.get(series_field)) for row in rows}) > MAX_CHART_SERIES:
            raise DashboardAnalyticsError(
                "chart_series_limit",
                f"Charts support at most {MAX_CHART_SERIES} series.",
            )
    else:
        raise DashboardAnalyticsError("unsupported_chart_type", "This chart type is not supported.")

    ordered_rows = rows
    if chart_type == "line":
        if all(_finite_number(row[x_field]) for row in rows):
            ordered_rows = sorted(rows, key=lambda row: row[x_field])
        else:
            def date_order(row: dict[str, Any]) -> datetime:
                parsed = datetime.fromisoformat(
                    row[x_field].replace("Z", "+00:00")
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed

            ordered_rows = sorted(
                rows,
                key=date_order,
            )
    selected_rows = ordered_rows[:MAX_CHART_POINTS]
    warnings = []
    if len(rows) > MAX_CHART_POINTS:
        warnings.append(
            f"Chart is limited to the first {MAX_CHART_POINTS} deterministic result groups."
        )
    if chart_type == "scatter":
        points = [
            {
                "x": _json_value(row[x_field]),
                "y": _json_value(row[y_field]),
                "series": str(row.get(series_field)) if series_field else None,
            }
            for row in selected_rows
        ]
        series = sorted({point["series"] or "All" for point in points})
        if len(series) > MAX_CHART_SERIES:
            raise DashboardAnalyticsError(
                "chart_series_limit",
                f"Charts support at most {MAX_CHART_SERIES} series.",
            )
        data = {"points": points, "series": series}
    else:
        labels = [str(row[x_field]) for row in selected_rows]
        if series_field is None:
            series = [
                {
                    "name": y_field,
                    "values": [_json_value(row[y_field]) for row in selected_rows],
                }
            ]
        else:
            series_names = sorted({str(row[series_field]) for row in selected_rows})
            if len(series_names) > MAX_CHART_SERIES:
                raise DashboardAnalyticsError(
                    "chart_series_limit",
                    f"Charts support at most {MAX_CHART_SERIES} series.",
                )
            series = [
                {
                    "name": name,
                    "values": [
                        _json_value(row[y_field])
                        if str(row.get(series_field)) == name
                        else None
                        for row in selected_rows
                    ],
                }
                for name in series_names
            ]
        data = {"labels": labels, "series": series}
        if chart_type == "line":
            data["x_values"] = [_json_value(row[x_field]) for row in selected_rows]

    return {
        "title": request["title"],
        "chart_type": chart_type,
        "x_field": x_field,
        "y_field": y_field,
        "series_field": series_field,
        "aggregation": next(
            (metric.get("operation") for metric in metrics or [] if metric.get("name") == y_field),
            None,
        ),
        "labels": {"x": x_field, "y": y_field},
        "data": data,
        "warnings": warnings,
    }


def build_report(
    analysis_request: dict[str, Any],
    result: dict[str, Any],
    chart_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(chart_requests) > MAX_CHARTS:
        raise DashboardAnalyticsError(
            "chart_count_limit",
            f"Reports support at most {MAX_CHARTS} charts.",
        )
    evidence_rows = result.get("result_evidence")
    if not isinstance(evidence_rows, list):
        raise DashboardAnalyticsError(
            "analytics_evidence_missing",
            "Analytics did not return evidence sufficient to ground findings.",
        )
    input_sources = result.get("input_sources")
    metric_definitions = {
        metric["name"]: metric
        for metric in result.get("metrics", [])
        if isinstance(metric, dict) and isinstance(metric.get("name"), str)
    }
    findings = []
    warnings = list(result.get("warnings", []))
    findings_count = 0
    for group in evidence_rows:
        dimensions = {
            name: group.get(name)
            for name in result.get("dimensions", [])
        }
        for metric_evidence in group.get("metrics", []):
            metric_name = metric_evidence.get("name")
            metric = metric_definitions.get(metric_name)
            value = metric_evidence.get("value")
            if metric is None or value is None:
                continue
            if not _json_value(value) == value:
                raise DashboardAnalyticsError(
                    "analytics_evidence_invalid",
                    "A finding contains an invalid measured value.",
                )
            metric_reference = {
                "name": metric_name,
                "operation": metric["operation"],
                "column": metric.get("column"),
                "value": value,
                "valid_count": metric_evidence.get("valid_count"),
                "missing_count": metric_evidence.get("missing_count"),
                "group_row_count": group.get("group_row_count"),
            }
            findings_count += 1
            if len(findings) >= MAX_FINDINGS:
                continue
            evidence_hash = canonical_sha256(
                {
                    "dimensions": dimensions,
                    "execution_fingerprint": result.get("execution_fingerprint"),
                    "metric": metric_reference,
                    "sources": input_sources,
                }
            )
            findings.append(
                {
                    "id": evidence_hash,
                    "kind": "measured_fact",
                    "statement": (
                        f"{metric_name} is {value}"
                        + (
                            " for " + ", ".join(f"{key}={item}" for key, item in dimensions.items())
                            if dimensions
                            else ""
                        )
                        + "."
                    ),
                    "supporting_metric": metric_reference,
                    "source_references": input_sources,
                    "columns": [metric["column"]] if metric.get("column") else [],
                    "evidence_sha256": evidence_hash,
                    "execution_reference": result.get("execution_fingerprint"),
                    "interpretation": None,
                    "recommendation": None,
                }
            )
            if metric_reference["missing_count"]:
                warnings.append(
                    f"{metric_name} excludes {metric_reference['missing_count']} missing or invalid values "
                    f"in group {dimensions or 'all rows'}."
                )
            if metric_reference["group_row_count"] < 30:
                warnings.append(
                    f"{metric_name} has fewer than 30 joined rows in group {dimensions or 'all rows'}."
                )

    charts = [build_chart_spec(result, item) for item in chart_requests]
    if findings_count > len(findings):
        warnings.append(
            f"Findings are limited to the first {MAX_FINDINGS} deterministic measurements."
        )
    if not findings_count:
        warnings.append("No measured findings were available for the selected result.")
    summary = {
        "joined_row_count": result.get("joined_row_count"),
        "group_count": result.get("group_count"),
        "findings_count": findings_count,
        "findings_returned": len(findings),
    }
    config_hash = canonical_sha256(analysis_request)
    output_hash = canonical_sha256(
        {"summary": summary, "findings": findings, "charts": charts}
    )
    provenance = build_provenance(
        operation_type="multi_dataset_analytics_report",
        operation_version="1",
        configuration_sha256=config_hash,
        status="completed",
        inputs=[
            {
                "dataset_id": source["dataset_id"],
                "file_id": source["file_id"],
                "table_index": source["table_index"],
                "sha256": source["source_sha256"],
            }
            for source in input_sources
        ],
        outputs=[{"sha256": output_hash, "kind": "structured_report"}],
        verification_status="COMPUTED",
        checks=[
            {"name": "analytics_result_evidence", "status": "COMPUTED"},
            {"name": "chart_fields_validated", "status": "COMPUTED"},
        ],
        limitations=warnings,
    )
    validate_provenance(provenance)
    report = {
        "summary": summary,
        "findings": findings,
        "charts": charts,
        "analytics": result,
        "provenance": provenance,
        "warnings": list(dict.fromkeys(warnings))[:MAX_REPORT_WARNINGS],
        "refresh_policy": "on_demand",
    }
    try:
        encoded = json.dumps(
            report,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise DashboardAnalyticsError(
            "report_invalid",
            "The report could not be represented safely.",
        ) from None
    if len(encoded) > MAX_REPORT_BYTES:
        raise DashboardAnalyticsError(
            "report_size_limit",
            "The report exceeds the 1 MiB response limit.",
        )
    return report


def build_eda_report(
    analysis: dict[str, Any],
    stored_provenance: dict[str, Any],
    chart_columns: list[str],
) -> dict[str, Any]:
    try:
        validate_provenance(stored_provenance)
    except (TypeError, ValueError):
        raise DashboardAnalyticsError(
            "eda_provenance_invalid",
            "The stored EDA provenance could not be verified.",
        ) from None
    verification = stored_provenance.get("verification", {})
    source_evidence = analysis.get("source")
    if (
        stored_provenance.get("status") not in {"completed", "partial"}
        or verification.get("status") != "verified"
        or not isinstance(source_evidence, dict)
        or analysis.get("configuration_sha256")
        != stored_provenance.get("configuration_sha256")
        or not any(
            item.get("kind") == "normalized_parquet"
            and item.get("dataset_id") == source_evidence.get("dataset_id")
            and item.get("file_id") == source_evidence.get("file_id")
            and item.get("table_index") == source_evidence.get("table_index")
            and item.get("sha256") == source_evidence.get("artifact_sha256")
            for item in stored_provenance.get("inputs", [])
        )
    ):
        raise DashboardAnalyticsError(
            "eda_provenance_mismatch",
            "The stored EDA result does not match its verified source provenance.",
        )
    if analysis.get("status") not in {"completed", "partially_completed"}:
        raise DashboardAnalyticsError(
            "eda_result_unavailable",
            "Only completed or partially completed EDA results can be reported.",
        )
    source = analysis.get("source")
    columns = analysis.get("columns")
    if (
        not isinstance(source, dict)
        or not isinstance(columns, list)
        or not isinstance(source.get("dataset_id"), int)
        or not isinstance(source.get("file_id"), int)
        or not isinstance(source.get("artifact_sha256"), str)
        or len(source["artifact_sha256"]) != 64
    ):
        raise DashboardAnalyticsError(
            "eda_result_invalid",
            "The stored EDA result is missing source or column evidence.",
        )
    by_name = {
        item.get("name"): item
        for item in columns
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if len(chart_columns) != len(set(chart_columns)) or any(
        name not in by_name for name in chart_columns
    ):
        raise DashboardAnalyticsError(
            "eda_chart_column_invalid",
            "EDA chart columns must uniquely reference analyzed columns.",
        )

    source_reference = {
        "dataset_id": source["dataset_id"],
        "file_id": source["file_id"],
        "table_index": source.get("table_index"),
        "source_sha256": source["artifact_sha256"],
        "analysis_id": analysis.get("id"),
    }
    warnings = list(analysis.get("warnings", []))
    findings = []

    def add_finding(column_name: str, metric_name: str, value: Any, count: int | None) -> None:
        if value is None or len(findings) >= MAX_FINDINGS:
            return
        safe_value = _json_value(value)
        metric = {
            "name": metric_name,
            "column": column_name,
            "value": safe_value,
            "valid_count": count,
            "total_rows": analysis.get("rows_analyzed"),
        }
        evidence_hash = canonical_sha256(
            {
                "source": source_reference,
                "metric": metric,
                "execution_reference": stored_provenance["operation_id"],
            }
        )
        findings.append(
            {
                "id": evidence_hash,
                "kind": "measured_fact",
                "statement": f"{column_name} {metric_name} is {safe_value}.",
                "supporting_metric": metric,
                "source_references": [source_reference],
                "columns": [column_name],
                "evidence_sha256": evidence_hash,
                "execution_reference": stored_provenance["operation_id"],
                "interpretation": None,
                "recommendation": None,
            }
        )

    for name, column in by_name.items():
        if column.get("status") != "completed":
            continue
        stats = column.get("statistics")
        if not isinstance(stats, dict):
            continue
        valid_count = column.get("non_null_count")
        if not isinstance(valid_count, int) or isinstance(valid_count, bool):
            valid_count = stats.get("finite_count", stats.get("count"))
        for metric_name in (
            "count",
            "finite_count",
            "mean",
            "median",
            "minimum",
            "maximum",
            "range",
            "sample_variance_ddof_1",
            "sample_standard_deviation_ddof_1",
            "distinct_value_count",
            "coverage_seconds",
            "coverage_days",
        ):
            value = stats.get(metric_name)
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                add_finding(name, metric_name, value, valid_count)
        percentiles = stats.get("percentiles_linear")
        if isinstance(percentiles, dict):
            for percentile, value in sorted(percentiles.items()):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    add_finding(name, f"percentile_{percentile}", value, valid_count)
        frequencies = stats.get("frequencies")
        if isinstance(frequencies, list):
            for frequency in frequencies:
                if not isinstance(frequency, dict):
                    continue
                category = frequency.get("value")
                count = frequency.get("count")
                if isinstance(category, (str, int, float, bool)) and isinstance(count, int):
                    add_finding(
                        name,
                        f"count for category {category}",
                        count,
                        valid_count,
                    )
        warnings.extend(column.get("warnings", []))
        null_count = column.get("null_count")
        if isinstance(null_count, int) and null_count:
            warnings.append(f"{name} contains {null_count} null values.")
        if isinstance(valid_count, int) and valid_count < 30:
            warnings.append(f"{name} has fewer than 30 valid observations.")

    for pair in analysis.get("pair_results", []):
        if not isinstance(pair, dict) or not isinstance(pair.get("statistics"), dict):
            continue
        pair_columns = [pair.get("left_column"), pair.get("right_column")]
        if not all(isinstance(name, str) for name in pair_columns):
            continue
        for metric_name in ("pearson_correlation", "spearman_rank_correlation"):
            value = pair["statistics"].get(metric_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                add_finding(
                    " / ".join(pair_columns),
                    metric_name,
                    value,
                    pair.get("valid_observations"),
                )
        warnings.extend(pair.get("warnings", []))

    if len(findings) >= MAX_FINDINGS:
        warnings.append(f"Findings are limited to the first {MAX_FINDINGS} measurements.")
    if not findings:
        warnings.append("No measured findings were available in the stored EDA result.")

    selected_chart_columns = chart_columns or [
        name
        for name, item in by_name.items()
        if isinstance(item.get("statistics"), dict)
        and (
            isinstance(item["statistics"].get("histogram"), dict)
            or isinstance(item["statistics"].get("frequencies"), list)
        )
    ][:MAX_CHARTS]
    if len(selected_chart_columns) > MAX_CHARTS:
        raise DashboardAnalyticsError(
            "chart_count_limit",
            f"EDA reports support at most {MAX_CHARTS} charts.",
        )
    charts = []
    for name in selected_chart_columns:
        stats = by_name[name].get("statistics", {})
        histogram = stats.get("histogram")
        frequencies = stats.get("frequencies")
        if isinstance(histogram, dict):
            edges = histogram.get("edges")
            counts = histogram.get("counts")
            if (
                not isinstance(edges, list)
                or not isinstance(counts, list)
                or len(edges) != len(counts) + 1
                or any(not _finite_number(value) for value in edges)
                or any(not isinstance(value, int) or value < 0 for value in counts)
            ):
                continue
            labels = [f"[{edges[index]}, {edges[index + 1]}]" for index in range(len(counts))]
            values = counts
            aggregation = "histogram_count"
        elif isinstance(frequencies, list):
            selected = [
                item for item in frequencies
                if isinstance(item, dict)
                and isinstance(item.get("count"), int)
                and item["count"] >= 0
            ][:MAX_CHART_POINTS]
            labels = [str(item.get("value")) for item in selected]
            values = [item["count"] for item in selected]
            aggregation = "category_frequency"
        else:
            raise DashboardAnalyticsError(
                "eda_chart_unavailable",
                f"No supported deterministic chart data is available for {name}.",
            )
        charts.append(
            {
                "title": f"Distribution of {name}",
                "chart_type": "bar",
                "x_field": name,
                "y_field": "count",
                "series_field": None,
                "aggregation": aggregation,
                "labels": {"x": name, "y": "count"},
                "data": {
                    "labels": labels[:MAX_CHART_POINTS],
                    "series": [{"name": "count", "values": values[:MAX_CHART_POINTS]}],
                },
                "warnings": [],
            }
        )

    summary = {
        "analysis_id": analysis.get("id"),
        "analysis_type": analysis.get("analysis_type"),
        "rows_analyzed": analysis.get("rows_analyzed"),
        "findings_count": len(findings),
    }
    output_hash = canonical_sha256(
        {"findings": findings, "charts": charts, "summary": summary}
    )
    provenance = build_provenance(
        operation_type="eda_evidence_report",
        operation_version="1",
        configuration_sha256=canonical_sha256(
            {"analysis_id": analysis.get("id"), "chart_columns": selected_chart_columns}
        ),
        status=(
            "completed"
            if analysis.get("status") == "completed"
            else "partial"
        ),
        inputs=[source_reference],
        outputs=[{"sha256": output_hash, "kind": "structured_eda_report"}],
        verification_status="COMPUTED",
        checks=[
            {"name": "stored_eda_provenance", "status": "verified"},
            {"name": "eda_metrics_and_histograms", "status": "COMPUTED"},
        ],
        limitations=list(dict.fromkeys(warnings))[:MAX_REPORT_WARNINGS],
        parent_operation_ids=[stored_provenance["operation_id"]],
    )
    validate_provenance(provenance)
    report = {
        "summary": summary,
        "findings": findings,
        "charts": charts,
        "analysis": analysis,
        "provenance": provenance,
        "warnings": list(dict.fromkeys(warnings))[:MAX_REPORT_WARNINGS],
        "refresh_policy": "stored_analysis",
    }
    try:
        size = len(
            json.dumps(
                report,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError):
        raise DashboardAnalyticsError(
            "report_invalid",
            "The EDA report could not be represented safely.",
        ) from None
    if size > MAX_REPORT_BYTES:
        raise DashboardAnalyticsError(
            "report_size_limit",
            "The EDA report exceeds the 1 MiB response limit.",
        )
    return report
