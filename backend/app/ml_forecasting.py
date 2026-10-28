from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import numpy as np
from sklearn.linear_model import LinearRegression


MAX_FORECAST_ROWS = 100_000
MAX_MONTHS = 240
MAX_HORIZON = 24
MIN_TRAIN_MONTHS = 12
MIN_VALIDATION_MONTHS = 3
MAX_MODEL_ARTIFACT_BYTES = 16 * 1024
MAX_BACKTEST_FOLDS = 12
MIN_UNCERTAINTY_FOLDS = 8
BASELINE_MAE_RELATIVE_TOLERANCE = 0.01
BASELINE_MAE_ABSOLUTE_TOLERANCE = 1e-9
WAPE_NEAR_ZERO_TOLERANCE = 1e-12
NEAR_CONSTANT_RELATIVE_TOLERANCE = 1e-9
_COLUMN_NAME = re.compile(r"^[^\x00-\x1f]{1,255}$")


class ForecastError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        findings: list[dict[str, str]] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.findings = findings or [
            {"severity": "blocked", "code": code, "message": message}
        ]


def _finite(value: Any) -> bool:
    if not isinstance(value, (int, float, np.number)) or isinstance(
        value, (bool, np.bool_)
    ):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _feature_matrix(indices: np.ndarray) -> np.ndarray:
    angles = 2 * np.pi * (indices % 12) / 12
    return np.column_stack(
        (
            indices.astype(np.float64),
            np.sin(angles),
            np.cos(angles),
        )
    )


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ForecastError("evaluation_non_finite", "Evaluation values must be finite.")
    errors = predicted - actual
    absolute_errors = np.abs(errors)
    denominator = float(np.sum(np.abs(actual)))
    wape_available = denominator > WAPE_NEAR_ZERO_TOLERANCE
    metrics = {
        "mae": float(np.mean(absolute_errors)),
        "rmse": float(np.sqrt(np.mean(np.square(errors)))),
        "wape": (
            float(np.sum(absolute_errors) / denominator * 100)
            if wape_available
            else None
        ),
        "wape_status": "available" if wape_available else "unavailable",
        "wape_reason": (
            None
            if wape_available
            else "The sum of absolute actual values is zero or below 1e-12."
        ),
    }
    if any(
        not math.isfinite(metrics[name])
        for name in ("mae", "rmse")
    ) or (
        metrics["wape"] is not None
        and not math.isfinite(metrics["wape"])
    ):
        raise ForecastError("evaluation_non_finite", "Evaluation metrics are not finite.")
    return metrics


def _baseline_comparison(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    baseline_name: str,
) -> dict[str, Any]:
    difference = float(baseline["mae"]) - float(candidate["mae"])
    tolerance = max(
        BASELINE_MAE_ABSOLUTE_TOLERANCE,
        abs(float(baseline["mae"])) * BASELINE_MAE_RELATIVE_TOLERANCE,
    )
    outcome = (
        "beats_baseline"
        if difference > tolerance
        else "within_tolerance"
        if difference >= -tolerance
        else "worse_than_baseline"
    )
    return {
        "baseline_name": baseline_name,
        "mae_improvement": difference,
        "mae_tolerance": tolerance,
        "outcome": outcome,
        "tolerance_relative": BASELINE_MAE_RELATIVE_TOLERANCE,
    }


def _fit_and_predict(training: np.ndarray, start: int, count: int) -> np.ndarray:
    model = LinearRegression()
    model.fit(
        _feature_matrix(np.arange(len(training), dtype=np.int64)),
        training,
    )
    predictions = model.predict(
        _feature_matrix(np.arange(start, start + count, dtype=np.int64))
    )
    if not np.isfinite(predictions).all():
        raise ForecastError(
            "evaluation_non_finite",
            "The trend model produced non-finite evaluation predictions.",
        )
    return predictions


def _expanding_backtest(
    values: np.ndarray,
    periods: list[str],
    horizon: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fold_count = min(
        MAX_BACKTEST_FOLDS,
        max(0, len(values) - horizon - MIN_TRAIN_MONTHS + 1),
    )
    if fold_count == 0:
        return (
            {
                "method": "expanding_window",
                "fold_count": 0,
                "horizon": horizon,
                "sample_count": 0,
                "folds": [],
                "candidate": None,
                "baselines": {},
            },
            {
                "status": "unavailable",
                "method": None,
                "reason": "Not enough pre-forecast observations for an expanding-window evaluation.",
                "required_folds": MIN_UNCERTAINTY_FOLDS,
                "available_folds": 0,
                "lead_time_bounds": [],
                "limitations": [
                    "Uncertainty bounds require at least eight expanding-window forecast origins for every requested lead time."
                ],
            },
        )

    last_origin = len(values) - horizon
    first_origin = last_origin - fold_count + 1
    fold_metadata = []
    actual_values: list[list[float]] = [[] for _ in range(horizon)]
    candidate_values: list[list[float]] = [[] for _ in range(horizon)]
    last_values: list[list[float]] = [[] for _ in range(horizon)]
    seasonal_values: list[list[float]] = [[] for _ in range(horizon)]
    include_seasonal = first_origin >= 24

    for origin in range(first_origin, last_origin + 1):
        training = values[:origin]
        observed = values[origin : origin + horizon]
        predictions = _fit_and_predict(training, origin, horizon)
        last_baseline = np.full(horizon, training[-1], dtype=np.float64)
        for lead in range(horizon):
            actual_values[lead].append(float(observed[lead]))
            candidate_values[lead].append(float(predictions[lead]))
            last_values[lead].append(float(last_baseline[lead]))
            if include_seasonal:
                seasonal_prediction = float(training[-12 + (lead % 12)])
                seasonal_values[lead].append(seasonal_prediction)
        fold_metadata.append(
            {
                "training_observations": len(training),
                "training_end": periods[origin - 1],
                "evaluation_start": periods[origin],
                "evaluation_end": periods[origin + horizon - 1],
                "sample_count": horizon,
                "candidate_mae": _metrics(observed, predictions)["mae"],
                "last_observation_mae": _metrics(
                    observed,
                    last_baseline,
                )["mae"],
            }
        )

    flattened_actual = np.asarray(
        [value for lead_values in actual_values for value in lead_values],
        dtype=np.float64,
    )
    flattened_candidate = np.asarray(
        [value for lead_values in candidate_values for value in lead_values],
        dtype=np.float64,
    )
    flattened_last = np.asarray(
        [value for lead_values in last_values for value in lead_values],
        dtype=np.float64,
    )
    baseline_metrics = {
        "last_observation": _metrics(flattened_actual, flattened_last),
    }
    if include_seasonal:
        flattened_seasonal = np.asarray(
            [value for lead_values in seasonal_values for value in lead_values],
            dtype=np.float64,
        )
        baseline_metrics["seasonal_naive_12"] = _metrics(
            flattened_actual,
            flattened_seasonal,
        )
    candidate_metrics = _metrics(flattened_actual, flattened_candidate)
    selected_baseline = (
        "seasonal_naive_12" if include_seasonal else "last_observation"
    )
    comparisons = {
        name: _baseline_comparison(candidate_metrics, metrics, name)
        for name, metrics in baseline_metrics.items()
    }

    if fold_count < MIN_UNCERTAINTY_FOLDS:
        uncertainty = {
            "status": "unavailable",
            "method": None,
            "reason": (
                f"Only {fold_count} expanding-window origins are available; "
                f"at least {MIN_UNCERTAINTY_FOLDS} are required."
            ),
            "required_folds": MIN_UNCERTAINTY_FOLDS,
            "available_folds": fold_count,
            "lead_time_bounds": [],
            "limitations": [
                "No uncertainty bounds are inferred from training residuals or an undersized out-of-sample sample."
            ],
        }
    else:
        lead_time_bounds = []
        for lead, (observed, predicted) in enumerate(
            zip(actual_values, candidate_values, strict=True),
            start=1,
        ):
            absolute_errors = sorted(
                abs(actual - prediction)
                for actual, prediction in zip(observed, predicted, strict=True)
            )
            quantile_index = max(
                0,
                math.ceil(0.9 * len(absolute_errors)) - 1,
            )
            lead_time_bounds.append(
                {
                    "lead": lead,
                    "absolute_error_quantile": absolute_errors[quantile_index],
                    "quantile": 0.9,
                    "sample_count": len(absolute_errors),
                }
            )
        uncertainty = {
            "status": "available",
            "method": "expanding_window_empirical_absolute_error_envelope",
            "reason": None,
            "required_folds": MIN_UNCERTAINTY_FOLDS,
            "available_folds": fold_count,
            "lead_time_bounds": lead_time_bounds,
            "limitations": [
                "Bounds are a descriptive 90th-percentile absolute-error envelope from rolling-origin errors, not calibrated prediction or confidence intervals."
            ],
        }
    return (
        {
            "method": "expanding_window",
            "fold_count": fold_count,
            "horizon": horizon,
            "sample_count": fold_count * horizon,
            "folds": fold_metadata,
            "candidate": candidate_metrics,
            "baseline_name": selected_baseline,
            "baselines": baseline_metrics,
            "comparisons": comparisons,
        },
        uncertainty,
    )


def _suitability_findings(
    *,
    values: np.ndarray,
    duplicate_rows: int,
    validation_count: int,
    horizon: int,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if duplicate_rows:
        findings.append(
            {
                "severity": "informational",
                "code": "duplicate_month_rows_aggregated",
                "message": (
                    f"{duplicate_rows} additional row(s) shared a month and were "
                    "combined using the requested sum aggregation."
                ),
            }
        )
    if len(values) < 24:
        findings.append(
            {
                "severity": "warning",
                "code": "less_than_two_annual_cycles",
                "message": (
                    "Fewer than 24 monthly observations are available; the annual "
                    "calendar terms have less than two complete cycles of history."
                ),
            }
        )
    if validation_count < 6:
        findings.append(
            {
                "severity": "warning",
                "code": "small_chronological_holdout",
                "message": (
                    f"The chronological holdout contains {validation_count} "
                    "period(s), so its metrics may be sensitive to individual months."
                ),
            }
        )
    scale = max(1.0, float(np.max(np.abs(values))))
    if float(np.ptp(values)) <= scale * NEAR_CONSTANT_RELATIVE_TOLERANCE:
        findings.append(
            {
                "severity": "warning",
                "code": "constant_or_near_constant_target",
                "message": (
                    "The target range is within a 1e-9 relative tolerance of its "
                    "scale; trend and seasonal effects cannot be meaningfully distinguished."
                ),
            }
        )
    if horizon > len(values) / 2:
        findings.append(
            {
                "severity": "warning",
                "code": "long_horizon_relative_to_history",
                "message": (
                    "The requested horizon exceeds half the available monthly "
                    "history, so forecast uncertainty may grow substantially."
                ),
            }
        )
    return findings


def _digest_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _month_ordinal(value: Any) -> int:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ForecastError(
                "invalid_dates",
                "The date column contains missing or invalid dates.",
            ) from None
    else:
        raise ForecastError(
            "invalid_dates",
            "The date column contains missing or invalid dates.",
        )
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.year * 12 + parsed.month - 1


def _period_label(ordinal: int) -> str:
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def _numeric_target(value: Any) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value,
        (int, float, np.number, Decimal),
    ):
        raise ForecastError(
            "invalid_targets",
            "The target column must contain finite numeric values without missing entries.",
        )
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ForecastError(
            "invalid_targets",
            "The target column must contain finite numeric values without missing entries.",
        ) from None
    if not math.isfinite(converted):
        raise ForecastError(
            "invalid_targets",
            "The target column must contain finite numeric values without missing entries.",
        )
    return converted


def train_monthly_forecast(
    *,
    dates: list[Any],
    targets: list[Any],
    date_column: str,
    target_column: str,
    dataset_id: int,
    file_id: int,
    source_fingerprint: str,
    horizon: int = 3,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    """Train and evaluate a deterministic monthly trend model on bounded data."""
    for name, value in (("date", date_column), ("target", target_column)):
        if not isinstance(value, str) or not _COLUMN_NAME.fullmatch(value):
            raise ForecastError("invalid_column", f"The selected {name} column name is invalid.")
    if date_column == target_column:
        raise ForecastError("invalid_columns", "Date and target columns must be different.")
    if artifact_id is not None and (
        not isinstance(artifact_id, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", artifact_id)
    ):
        raise ForecastError("model_id_invalid", "The model artifact identifier is invalid.")
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, int)
        or not 1 <= horizon <= MAX_HORIZON
    ):
        raise ForecastError(
            "invalid_horizon",
            f"Forecast horizon must be between 1 and {MAX_HORIZON} months.",
        )
    if (
        isinstance(dataset_id, bool)
        or not isinstance(dataset_id, int)
        or dataset_id <= 0
        or isinstance(file_id, bool)
        or not isinstance(file_id, int)
        or file_id <= 0
        or not isinstance(source_fingerprint, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", source_fingerprint)
    ):
        raise ForecastError(
            "source_identity_invalid",
            "The authorized source file version could not be verified.",
        )
    if (
        not isinstance(dates, list)
        or not isinstance(targets, list)
        or len(dates) != len(targets)
        or not dates
        or len(dates) > MAX_FORECAST_ROWS
    ):
        raise ForecastError(
            "training_rows_invalid",
            f"Training data must contain 1 to {MAX_FORECAST_ROWS} matching date and target values.",
        )

    monthly_totals: dict[int, float] = {}
    for raw_date, raw_target in zip(dates, targets, strict=True):
        month = _month_ordinal(raw_date)
        value = _numeric_target(raw_target)
        monthly_totals[month] = monthly_totals.get(month, 0.0) + value
    duplicate_rows = len(dates) - len(monthly_totals)
    ordered_months = sorted(monthly_totals)
    values = np.asarray([monthly_totals[month] for month in ordered_months], dtype=np.float64)
    period_labels = [_period_label(month) for month in ordered_months]
    if not len(values) or not np.isfinite(values).all():
        raise ForecastError("invalid_aggregation", "Monthly target aggregation is not finite.")
    if len(values) > MAX_MONTHS:
        raise ForecastError(
            "history_limit",
            f"Forecasting supports at most {MAX_MONTHS} monthly observations.",
        )
    if len(values) < MIN_TRAIN_MONTHS + MIN_VALIDATION_MONTHS:
        raise ForecastError(
            "insufficient_history",
            f"At least {MIN_TRAIN_MONTHS + MIN_VALIDATION_MONTHS} consecutive months are required.",
            findings=[
                {
                    "severity": "blocked",
                    "code": "insufficient_history",
                    "message": (
                        f"Only {len(values)} distinct monthly period(s) were found; "
                        f"at least {MIN_TRAIN_MONTHS + MIN_VALIDATION_MONTHS} are "
                        "required for a training window and chronological holdout."
                    ),
                }
            ],
        )
    month_ordinals = np.asarray(ordered_months, dtype=np.int64)
    if len(month_ordinals) > 1 and np.any(np.diff(month_ordinals) != 1):
        raise ForecastError(
            "missing_periods",
            "The monthly series contains gaps; missing periods are not imputed.",
        )

    validation_count = max(
        MIN_VALIDATION_MONTHS,
        int(math.ceil(len(values) * 0.2)),
    )
    train_count = len(values) - validation_count
    if train_count < MIN_TRAIN_MONTHS:
        raise ForecastError(
            "insufficient_history",
            "There is not enough pre-validation history for a chronological evaluation.",
        )
    train_values = values[:train_count]
    validation_values = values[train_count:]
    candidate_validation = _fit_and_predict(
        train_values,
        train_count,
        validation_count,
    )
    baseline_last = np.full(validation_count, train_values[-1], dtype=np.float64)
    baseline_candidates = {
        "last_observation": baseline_last,
    }
    if train_count >= 24:
        baseline_candidates["seasonal_naive_12"] = np.asarray(
            [
                train_values[-12 + (lead % 12)]
                for lead in range(validation_count)
            ],
            dtype=np.float64,
        )
    baseline_name = (
        "seasonal_naive_12"
        if "seasonal_naive_12" in baseline_candidates
        else "last_observation"
    )
    candidate_metrics = _metrics(validation_values, candidate_validation)
    baselines = {
        name: _metrics(validation_values, predictions)
        for name, predictions in baseline_candidates.items()
    }
    comparison = _baseline_comparison(
        candidate_metrics,
        baselines[baseline_name],
        baseline_name,
    )
    backtesting, uncertainty = _expanding_backtest(
        values,
        period_labels,
        horizon,
    )
    suitability_findings = _suitability_findings(
        values=values,
        duplicate_rows=duplicate_rows,
        validation_count=validation_count,
        horizon=horizon,
    )
    if backtesting["fold_count"] < MIN_UNCERTAINTY_FOLDS:
        suitability_findings.append(
            {
                "severity": "warning",
                "code": "insufficient_rolling_origins_for_uncertainty",
                "message": uncertainty["reason"],
            }
        )
    evaluation = {
        "method": "chronological_holdout",
        "training_periods": period_labels[:train_count],
        "periods": period_labels[train_count:],
        "period_start": period_labels[train_count],
        "period_end": period_labels[-1],
        "sample_count": validation_count,
        "observed": [float(value) for value in validation_values],
        "candidate_predictions": [
            float(value) for value in candidate_validation
        ],
        "baseline_predictions": [
            float(value) for value in baseline_candidates[baseline_name]
        ],
        "candidate": candidate_metrics,
        "baseline_name": baseline_name,
        "baseline": baselines[baseline_name],
        "baselines": baselines,
        "comparison": comparison,
        "mae_tolerance_relative": BASELINE_MAE_RELATIVE_TOLERANCE,
        "backtesting": backtesting,
        "uncertainty": uncertainty,
        "suitability": {
            "status": (
                "warning"
                if any(
                    finding["severity"] == "warning"
                    for finding in suitability_findings
                )
                else "suitable"
            ),
            "findings": suitability_findings,
        },
    }
    evaluation["beats_baseline"] = comparison["outcome"] == "beats_baseline"

    final_model = LinearRegression()
    all_indices = np.arange(len(values), dtype=np.int64)
    final_model.fit(_feature_matrix(all_indices), values)
    final_artifact = {
        "artifact_version": 2,
        "algorithm": "sklearn_linear_trend_seasonal",
        "model_id": artifact_id,
        "model_version": 2,
        "result_schema_version": 2,
        "problem_type": "monthly_forecasting",
        "date_column": date_column,
        "target_column": target_column,
        "frequency": "month",
        "aggregation": "sum",
        "feature_names": ["month_index", "month_sin", "month_cos"],
        "coefficients": [float(value) for value in final_model.coef_],
        "intercept": float(final_model.intercept_),
        "training_observations": len(values),
        "training_range": [period_labels[0], period_labels[-1]],
        "source": {
            "dataset_id": dataset_id,
            "file_id": file_id,
            "fingerprint": source_fingerprint,
        },
        "validation": evaluation,
        "parameters": {
            "evaluation_split": "chronological_last_20_percent_minimum_3_months",
            "backtest": "expanding_window_up_to_12_origins",
            "random_seed": 0,
            "baseline_mae_relative_tolerance": BASELINE_MAE_RELATIVE_TOLERANCE,
            "estimator": "sklearn.linear_model.LinearRegression",
        },
        "library_versions": {
            "scikit_learn": __import__("sklearn").__version__,
            "numpy": np.__version__,
        },
    }
    artifact_bytes = json.dumps(
        final_artifact,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(artifact_bytes) > MAX_MODEL_ARTIFACT_BYTES:
        raise ForecastError("model_artifact_too_large", "The model artifact exceeds its size limit.")
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    forecast = predict_monthly_forecast(
        artifact=final_artifact,
        history=values.tolist(),
        history_periods=period_labels,
        horizon=horizon,
        artifact_sha256=artifact_sha256,
        dataset_id=dataset_id,
        file_id=file_id,
        source_fingerprint=source_fingerprint,
    )
    forecast["historical"] = [
        {"period": period, "observed": float(value), "kind": "observed"}
        for period, value in zip(period_labels, values, strict=True)
    ]
    warning_findings = [
        finding
        for finding in suitability_findings
        if finding["severity"] == "warning"
    ]
    forecast_warnings = [
        finding["message"] for finding in warning_findings
    ]
    if comparison["outcome"] == "worse_than_baseline":
        forecast_warnings.append(
            f"The candidate model's holdout MAE did not beat {baseline_name} by more than the documented 1% tolerance."
        )
    elif comparison["outcome"] == "within_tolerance":
        forecast_warnings.append(
            f"The candidate model and {baseline_name} are within the documented 1% MAE comparison tolerance."
        )
    forecast["result_schema_version"] = 2
    forecast["evaluation_method"] = evaluation["method"]
    forecast["suitability"] = evaluation["suitability"]
    forecast["uncertainty"] = uncertainty
    forecast["warnings"] = forecast_warnings
    forecast["limitations"] = [
        "Forecasts use a linear trend with annual calendar terms; this does not establish statistical significance or stationarity.",
        *uncertainty["limitations"],
        "Monthly values are sums of the selected target; no missing periods are imputed.",
    ]
    return {
        "artifact": final_artifact,
        "artifact_bytes": artifact_bytes,
        "artifact_sha256": artifact_sha256,
        "forecast": forecast,
    }


def predict_monthly_forecast(
    *,
    artifact: dict[str, Any],
    history: list[float],
    history_periods: list[str],
    horizon: int,
    dataset_id: int,
    file_id: int,
    source_fingerprint: str,
    artifact_sha256: str | None = None,
) -> dict[str, Any]:
    required_features = ["month_index", "month_sin", "month_cos"]
    training_range = artifact.get("training_range") if isinstance(artifact, dict) else None
    source = artifact.get("source") if isinstance(artifact, dict) else None
    if (
        not isinstance(artifact, dict)
        or artifact.get("artifact_version") not in {1, 2}
        or artifact.get("algorithm") != "sklearn_linear_trend_seasonal"
        or artifact.get("frequency") != "month"
        or artifact.get("aggregation") != "sum"
        or artifact.get("feature_names") != required_features
        or not isinstance(source, dict)
        or isinstance(source.get("dataset_id"), bool)
        or not isinstance(source.get("dataset_id"), int)
        or source["dataset_id"] <= 0
        or isinstance(source.get("file_id"), bool)
        or not isinstance(source.get("file_id"), int)
        or source["file_id"] <= 0
        or not isinstance(source.get("fingerprint"), str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", source["fingerprint"])
        or not isinstance(artifact.get("validation"), dict)
        or not isinstance(training_range, list)
        or len(training_range) != 2
        or any(
            not isinstance(period, str)
            or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period)
            for period in training_range
        )
        or isinstance(artifact.get("training_observations"), bool)
        or not isinstance(artifact.get("training_observations"), int)
        or artifact["training_observations"] < MIN_TRAIN_MONTHS + MIN_VALIDATION_MONTHS
    ):
        raise ForecastError("model_artifact_invalid", "The saved model artifact is incompatible.")
    coefficients = artifact.get("coefficients")
    intercept = artifact.get("intercept")
    if (
        not isinstance(coefficients, list)
        or len(coefficients) != 3
        or not all(_finite(value) for value in coefficients)
        or not _finite(intercept)
        or not isinstance(history, list)
        or not history
        or len(history) > MAX_MONTHS
        or not all(_finite(value) for value in history)
        or not isinstance(history_periods, list)
        or len(history_periods) != len(history)
        or any(
            not isinstance(period, str)
            or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period)
            for period in history_periods
        )
        or len(history) != artifact["training_observations"]
        or isinstance(dataset_id, bool)
        or not isinstance(dataset_id, int)
        or isinstance(file_id, bool)
        or not isinstance(file_id, int)
        or not isinstance(source_fingerprint, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", source_fingerprint)
        or isinstance(horizon, bool)
        or not isinstance(horizon, int)
        or not 1 <= horizon <= MAX_HORIZON
    ):
        raise ForecastError("prediction_inputs_invalid", "Prediction inputs failed validation.")
    if (
        history_periods[0] != training_range[0]
        or history_periods[-1] != training_range[-1]
        or source.get("dataset_id") != dataset_id
        or source.get("file_id") != file_id
        or source.get("fingerprint") != source_fingerprint
        or any(
            _month_ordinal(f"{period}-01") - _month_ordinal(f"{previous}-01") != 1
            for previous, period in zip(history_periods, history_periods[1:])
        )
    ):
        raise ForecastError(
            "model_source_mismatch",
            "The prediction source version does not match the model version.",
        )
    start = len(history)
    indices = np.arange(start, start + horizon, dtype=np.int64)
    features = _feature_matrix(indices)
    predicted = features @ np.asarray(coefficients, dtype=np.float64) + float(intercept)
    if not np.isfinite(predicted).all():
        raise ForecastError("prediction_non_finite", "The model produced a non-finite forecast.")
    last_month = _month_ordinal(history_periods[-1] + "-01")
    forecast_periods = [
        _period_label(last_month + offset)
        for offset in range(1, horizon + 1)
    ]
    forecast_points = []
    uncertainty = artifact.get("validation", {}).get("uncertainty")
    bounds_by_lead = {}
    if artifact.get("artifact_version") == 2:
        if not isinstance(uncertainty, dict):
            raise ForecastError(
                "model_artifact_invalid",
                "The version 2 model artifact has no uncertainty evaluation.",
            )
        if uncertainty.get("status") == "available":
            lead_time_bounds = uncertainty.get("lead_time_bounds")
            if not isinstance(lead_time_bounds, list):
                raise ForecastError(
                    "model_artifact_invalid",
                    "The model artifact uncertainty bounds are malformed.",
                )
            for item in lead_time_bounds:
                if (
                    not isinstance(item, dict)
                    or isinstance(item.get("lead"), bool)
                    or not isinstance(item.get("lead"), int)
                    or not 1 <= item["lead"] <= MAX_HORIZON
                    or not _finite(item.get("absolute_error_quantile"))
                ):
                    raise ForecastError(
                        "model_artifact_invalid",
                        "The model artifact uncertainty bounds are malformed.",
                    )
                bounds_by_lead[item["lead"]] = float(
                    item["absolute_error_quantile"]
                )
            if set(bounds_by_lead) != set(
                range(1, max(bounds_by_lead, default=0) + 1)
            ):
                raise ForecastError(
                    "model_artifact_invalid",
                    "The model artifact uncertainty bounds do not match the horizon.",
                )
        elif uncertainty.get("status") != "unavailable":
            raise ForecastError(
                "model_artifact_invalid",
                "The model artifact uncertainty status is invalid.",
            )
    for lead, (period, value) in enumerate(
        zip(forecast_periods, predicted, strict=True),
        start=1,
    ):
        point = {
            "period": period,
            "predicted": float(value),
            "kind": "forecast",
        }
        if artifact.get("artifact_version") == 2:
            bound = bounds_by_lead.get(lead)
            lower_bound = float(value) - bound if bound is not None else None
            upper_bound = float(value) + bound if bound is not None else None
            if (
                lower_bound is not None
                and upper_bound is not None
                and (
                    not math.isfinite(lower_bound)
                    or not math.isfinite(upper_bound)
                    or lower_bound > upper_bound
                )
            ):
                raise ForecastError(
                    "uncertainty_bounds_invalid",
                    "Empirical forecast error bounds are not finite and ordered.",
                )
            point["lower_bound"] = lower_bound
            point["upper_bound"] = upper_bound
        forecast_points.append(point)
    return {
        "result_schema_version": artifact.get("result_schema_version", 1),
        "model_id": artifact.get("model_id"),
        "model_version": artifact.get("model_version"),
        "algorithm": artifact["algorithm"],
        "target": artifact.get("target_column"),
        "frequency": "month",
        "aggregation": "sum",
        "source": artifact["source"],
        "artifact_sha256": artifact_sha256 or _digest_json(artifact),
        "evaluation": artifact["validation"],
        "forecast": forecast_points,
    }


def load_model_artifact(encoded: bytes, expected_sha256: str) -> dict[str, Any]:
    if (
        not isinstance(encoded, bytes)
        or len(encoded) > MAX_MODEL_ARTIFACT_BYTES
        or not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256)
        or hashlib.sha256(encoded).hexdigest() != expected_sha256
    ):
        raise ForecastError("model_artifact_integrity_failed", "The saved model artifact failed integrity verification.")
    try:
        artifact = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ForecastError("model_artifact_invalid", "The saved model artifact is malformed.") from None
    if not isinstance(artifact, dict):
        raise ForecastError("model_artifact_invalid", "The saved model artifact is malformed.")
    return artifact
