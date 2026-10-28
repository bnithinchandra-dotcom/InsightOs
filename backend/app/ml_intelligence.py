from __future__ import annotations

import math
from typing import Any

import numpy as np


MIN_ANOMALY_OBSERVATIONS = 8
MAX_ANOMALY_SCAN_ROWS = 250_000
MAX_ANOMALY_EVIDENCE_ROWS = 100
DEFAULT_ROBUST_Z_THRESHOLD = 3.5

_NUMERIC_TYPES = (
    "int",
    "uint",
    "float",
    "double",
    "decimal",
)
_DATETIME_TYPES = ("date", "timestamp", "time")


def _column_kind(data_type: str) -> str:
    lowered = data_type.lower()
    if any(lowered.startswith(prefix) for prefix in _NUMERIC_TYPES):
        return "numeric"
    if any(lowered.startswith(prefix) for prefix in _DATETIME_TYPES):
        return "datetime"
    if lowered in {"bool", "boolean"}:
        return "boolean"
    if any(
        marker in lowered
        for marker in ("string", "large_string", "dictionary", "binary", "utf8")
    ):
        return "categorical"
    return "other"


def assess_ml_task_suitability(
    *,
    row_count: int,
    schema: list[dict[str, Any]],
    profile_columns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assess candidate tasks using verified metadata, without choosing targets."""
    profiles = {
        item["name"]: item
        for item in (profile_columns or [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    typed_columns = [
        {
            "name": item["name"],
            "data_type": item["type"],
            "kind": _column_kind(item["type"]),
        }
        for item in schema
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("type"), str)
    ]
    for column in typed_columns:
        profile = profiles.get(column["name"], {})
        missing_count = profile.get("missing_value_count")
        distinct_count = (
            profile.get("distinct_value_count")
            if profile.get("distinct_count_exact") is True
            else None
        )
        numeric_statistics = profile.get("numeric_statistics")
        category_summary = profile.get("categorical_summary")
        if (
            isinstance(missing_count, int)
            and not isinstance(missing_count, bool)
            and 0 <= missing_count <= row_count
            and row_count
        ):
            column["missing_fraction"] = missing_count / row_count
        constant = (
            isinstance(distinct_count, int)
            and not isinstance(distinct_count, bool)
            and distinct_count == 1
        ) or (
            isinstance(numeric_statistics, dict)
            and numeric_statistics.get("minimum") is not None
            and numeric_statistics.get("minimum") == numeric_statistics.get("maximum")
        )
        if constant:
            column["constant"] = True
        non_missing_count = (
            row_count - missing_count
            if isinstance(missing_count, int)
            and not isinstance(missing_count, bool)
            and 0 <= missing_count <= row_count
            else None
        )
        column_is_identifier = (
            isinstance(distinct_count, int)
            and not isinstance(distinct_count, bool)
            and non_missing_count is not None
            and non_missing_count >= 20
            and distinct_count / non_missing_count >= 0.98
        )
        if column_is_identifier:
            column["likely_identifier"] = True
        if isinstance(category_summary, list) and non_missing_count:
            largest_category = max(
                (item.get("count", 0) for item in category_summary if isinstance(item, dict)),
                default=0,
            )
            if isinstance(largest_category, int) and largest_category / non_missing_count >= 0.98:
                column["near_constant"] = True
        if isinstance(distinct_count, int) and not isinstance(distinct_count, bool):
            column["distinct_value_count"] = distinct_count
            column["distinct_count_exact"] = True
    numeric = [
        item["name"] for item in typed_columns
        if item["kind"] == "numeric"
        and not item.get("likely_identifier")
        and not item.get("constant")
    ]
    dates = [item["name"] for item in typed_columns if item["kind"] == "datetime"]
    categoricals = [
        item["name"] for item in typed_columns
        if item["kind"] in {"categorical", "boolean"}
        and not item.get("likely_identifier")
        and not item.get("constant")
    ]
    feature_columns = [
        item["name"] for item in typed_columns
        if not item.get("likely_identifier") and not item.get("constant")
    ]
    enough_for_forecast = row_count >= 15
    forecast_status = (
        "needs_user_input"
        if enough_for_forecast and dates and numeric
        else "insufficient_data"
        if dates and numeric
        else "unsuitable"
    )
    forecast_missing = []
    if not dates:
        forecast_missing.append("datetime_column")
    if not numeric:
        forecast_missing.append("numeric_target")
    if dates and numeric and not enough_for_forecast:
        forecast_missing.append("at_least_15_rows")

    def supervised_assessment(candidate_targets: list[str]) -> tuple[str, list[str]]:
        if not candidate_targets:
            return "unsuitable", ["target_column_of_compatible_type"]
        if len(feature_columns) < 2:
            return "unsuitable", ["at_least_one_eligible_feature", "explicit_target"]
        if row_count < 20:
            return (
                "insufficient_data",
                ["at_least_20_rows", "explicit_target", "feature_selection"],
            )
        return "needs_user_input", ["explicit_target", "feature_selection"]

    regression_status, regression_missing = supervised_assessment(numeric)
    classification_status, classification_missing = supervised_assessment(categoricals)
    anomaly_status = (
        "needs_user_input"
        if MIN_ANOMALY_OBSERVATIONS <= row_count <= MAX_ANOMALY_SCAN_ROWS and numeric
        else "insufficient_data"
        if numeric and row_count < MIN_ANOMALY_OBSERVATIONS
        else "unsuitable"
    )
    anomaly_missing = (
        ["numeric_column_selection"]
        if anomaly_status == "needs_user_input"
        else [
            f"at_least_{MIN_ANOMALY_OBSERVATIONS}_rows",
            "numeric_column_selection",
        ]
        if anomaly_status == "insufficient_data" and numeric
        else [f"at_most_{MAX_ANOMALY_SCAN_ROWS}_rows"]
        if numeric and row_count > MAX_ANOMALY_SCAN_ROWS
        else ["numeric_column"]
    )
    enough_for_descriptive = row_count > 0 and bool(typed_columns)

    return {
        "contract_version": 1,
        "row_count": row_count,
        "column_count": len(typed_columns),
        "columns": typed_columns,
        "tasks": [
            {
                "task_type": "time_series_forecasting",
                "status": forecast_status,
                "candidate_time_columns": dates,
                "candidate_target_columns": numeric,
                "missing_prerequisites": forecast_missing,
                "reason": (
                    "Forecasting requires an explicit time column and numeric target; "
                    "the existing monthly engine still validates continuity and target values."
                    if dates and numeric and enough_for_forecast
                    else "No datetime column is available."
                    if not dates
                    else "No numeric target is available."
                    if not numeric
                    else "The current forecasting workflow needs at least 15 rows."
                ),
            },
            {
                "task_type": "supervised_regression",
                "status": regression_status,
                "candidate_target_columns": numeric,
                "missing_prerequisites": regression_missing,
                "reason": (
                    "A numeric target is not inferred; select the outcome and eligible features explicitly."
                    if regression_status == "needs_user_input"
                    else "A numeric target, at least one eligible feature, and sufficient rows are required."
                ),
            },
            {
                "task_type": "supervised_classification",
                "status": classification_status,
                "candidate_target_columns": categoricals,
                "missing_prerequisites": classification_missing,
                "reason": (
                    "A categorical target is not inferred; select the outcome and eligible features explicitly."
                    if classification_status == "needs_user_input"
                    else "A categorical target, at least one eligible feature, and sufficient rows are required."
                ),
            },
            {
                "task_type": "anomaly_detection",
                "status": anomaly_status,
                "candidate_columns": numeric,
                "missing_prerequisites": anomaly_missing,
                "reason": (
                    "Select a numeric column for bounded robust univariate anomaly screening."
                    if anomaly_status == "needs_user_input"
                    else "Anomaly screening requires a numeric column and at least eight rows."
                    if anomaly_status == "insufficient_data" and numeric
                    else f"Anomaly screening is limited to {MAX_ANOMALY_SCAN_ROWS} rows."
                    if numeric and row_count > MAX_ANOMALY_SCAN_ROWS
                    else "No numeric column is available."
                ),
            },
            {
                "task_type": "descriptive_analysis",
                "status": "supported" if enough_for_descriptive else "insufficient_data",
                "missing_prerequisites": [] if enough_for_descriptive else ["non_empty_dataset"],
                "reason": (
                    "Descriptive EDA is available for the verified normalized source."
                    if enough_for_descriptive
                    else "A non-empty normalized table is required."
                ),
            },
        ],
        "limitations": [
            "Metadata suitability is not a model evaluation or a guarantee of data quality.",
            "Target and feature roles are never inferred from correlation.",
            "General supervised evaluation and grouped/time-aware anomaly detection are not implemented.",
            "High-cardinality columns are flagged as likely identifiers, not automatically used as features.",
        ],
    }


def detect_univariate_anomalies(
    values: list[float],
    *,
    row_indices: list[int] | None = None,
    threshold: float = DEFAULT_ROBUST_Z_THRESHOLD,
    max_evidence_rows: int = MAX_ANOMALY_EVIDENCE_ROWS,
) -> dict[str, Any]:
    """Screen finite values using a robust median/MAD score and bounded evidence."""
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(float(threshold))
        or not 2.0 <= float(threshold) <= 8.0
    ):
        raise ValueError("threshold must be finite and between 2.0 and 8.0.")
    if (
        isinstance(max_evidence_rows, bool)
        or not isinstance(max_evidence_rows, int)
        or not 1 <= max_evidence_rows <= MAX_ANOMALY_EVIDENCE_ROWS
    ):
        raise ValueError(
            f"max_evidence_rows must be between 1 and {MAX_ANOMALY_EVIDENCE_ROWS}."
        )
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in values
    ):
        raise ValueError("values must contain only finite numbers.")
    if row_indices is None:
        row_indices = list(range(1, len(values) + 1))
    if (
        len(row_indices) != len(values)
        or any(
            isinstance(index, bool) or not isinstance(index, int) or index < 1
            for index in row_indices
        )
    ):
        raise ValueError("row_indices must contain one positive integer per value.")

    if len(values) < MIN_ANOMALY_OBSERVATIONS:
        return {
            "status": "insufficient_data",
            "method": "median_absolute_deviation_modified_z_score",
            "threshold": float(threshold),
            "valid_observations": len(values),
            "row_count": len(row_indices),
            "anomaly_count": None,
            "evidence_rows": [],
            "warnings": [
                f"At least {MIN_ANOMALY_OBSERVATIONS} finite observations are required."
            ],
        }

    observations = np.asarray(values, dtype=np.float64)
    magnitude = max(float(np.max(np.abs(observations))), 1.0)
    scaled_observations = observations / magnitude
    median = float(np.median(scaled_observations))
    absolute_deviations = np.abs(scaled_observations - median)
    mad = float(np.median(absolute_deviations))
    q1, q3 = np.quantile(scaled_observations, [0.25, 0.75])
    iqr_scale = float(q3 - q1) / 1.349
    warnings = [
        "Anomaly flags indicate unusual values under this univariate rule; they are not proven errors or fraud."
    ]

    if mad > 0:
        scale = 1.4826 * mad
        scale_method = "median_absolute_deviation"
        scores = absolute_deviations / scale
    elif iqr_scale > 0:
        scale = iqr_scale
        scale_method = "interquartile_range_fallback"
        scores = absolute_deviations / scale
        warnings.append("Median absolute deviation was zero; the interquartile-range fallback was used.")
    else:
        scale = 0.0
        scale_method = "zero_dispersion_deviation"
        scores = np.where(absolute_deviations == 0, 0.0, float(threshold) + 1.0)
        if np.any(absolute_deviations > 0):
            warnings.append(
                "Both robust dispersion estimates were zero; deviations from the median were flagged by a degenerate-scale rule."
            )

    scores = np.nan_to_num(
        scores,
        nan=0.0,
        posinf=float(np.finfo(np.float64).max),
        neginf=0.0,
    )
    robust_scale = scale * magnitude
    if not math.isfinite(robust_scale):
        robust_scale = None
        warnings.append(
            "The robust scale is outside the finite numeric range and is unavailable."
        )
    if np.any(scores == np.finfo(np.float64).max):
        warnings.append("One or more robust scores exceeded the finite numeric range.")
    flagged_indices = np.flatnonzero(scores > float(threshold))
    ordered_indices = sorted(
        flagged_indices.tolist(),
        key=lambda index: (-float(scores[index]), index),
    )
    evidence = [
        {
            "row_index": row_indices[index],
            "value": float(observations[index]),
            "robust_score": float(scores[index]),
        }
        for index in ordered_indices[:max_evidence_rows]
    ]
    if len(ordered_indices) > len(evidence):
        warnings.append(
            f"Evidence is limited to the {max_evidence_rows} highest-scoring observations."
        )
    return {
        "status": "completed",
        "method": "median_absolute_deviation_modified_z_score",
        "scale_method": scale_method,
        "center_median": median * magnitude,
        "robust_scale": robust_scale,
        "threshold": float(threshold),
        "valid_observations": len(values),
        "anomaly_count": len(ordered_indices),
        "evidence_rows": evidence,
        "evidence_truncated": len(ordered_indices) > len(evidence),
        "warnings": warnings,
        "limitations": [
            "Univariate screening does not account for time, groups, or feature interactions.",
            "The chosen robust-score threshold is a screening convention, not a calibrated false-positive rate.",
        ],
    }
