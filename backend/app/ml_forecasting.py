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
_COLUMN_NAME = re.compile(r"^[^\x00-\x1f]{1,255}$")


class ForecastError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | None]:
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ForecastError("evaluation_non_finite", "Evaluation values must be finite.")
    errors = predicted - actual
    absolute_errors = np.abs(errors)
    denominator = float(np.sum(np.abs(actual)))
    metrics = {
        "mae": float(np.mean(absolute_errors)),
        "rmse": float(np.sqrt(np.mean(np.square(errors)))),
        "wape": (
            float(np.sum(absolute_errors) / denominator * 100)
            if denominator > 0
            else None
        ),
    }
    if any(
        value is not None and not math.isfinite(value)
        for value in metrics.values()
    ):
        raise ForecastError("evaluation_non_finite", "Evaluation metrics are not finite.")
    return metrics


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
    validation_indices = np.arange(train_count, len(values), dtype=np.int64)
    candidate = LinearRegression()
    candidate.fit(
        _feature_matrix(np.arange(train_count, dtype=np.int64)),
        train_values,
    )
    candidate_validation = candidate.predict(_feature_matrix(validation_indices))
    baseline_last = np.full(validation_count, train_values[-1], dtype=np.float64)
    baseline_candidates = {
        "last_observation": baseline_last,
    }
    if train_count >= 24:
        seasonal_history = train_values[-12:].tolist()
        for _ in range(validation_count):
            seasonal_history.append(seasonal_history[-12])
        seasonal = np.asarray(
            seasonal_history[12:],
            dtype=np.float64,
        )
        baseline_candidates["seasonal_naive_12"] = seasonal
    baseline_name = min(
        baseline_candidates,
        key=lambda name: _metrics(validation_values, baseline_candidates[name])["mae"],
    )

    evaluation = {
        "training_periods": period_labels[:train_count],
        "periods": period_labels[train_count:],
        "sample_count": validation_count,
        "observed": [float(value) for value in validation_values],
        "candidate_predictions": [
            float(value) for value in candidate_validation
        ],
        "baseline_predictions": [
            float(value) for value in baseline_candidates[baseline_name]
        ],
        "candidate": _metrics(validation_values, candidate_validation),
        "baseline_name": baseline_name,
        "baseline": _metrics(validation_values, baseline_candidates[baseline_name]),
    }
    evaluation["beats_baseline"] = (
        evaluation["candidate"]["mae"] < evaluation["baseline"]["mae"]
    )

    final_model = LinearRegression()
    all_indices = np.arange(len(values), dtype=np.int64)
    final_model.fit(_feature_matrix(all_indices), values)
    final_artifact = {
        "artifact_version": 1,
        "algorithm": "sklearn_linear_trend_seasonal",
        "model_id": artifact_id,
        "model_version": 1,
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
            "random_seed": 0,
            "split": "chronological_last_20_percent_minimum_3_months",
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
    forecast["warnings"] = (
        []
        if evaluation["beats_baseline"]
        else [
            "The candidate model did not beat the selected baseline on the held-out period."
        ]
    )
    forecast["limitations"] = [
        "Forecasts use a linear trend with annual calendar seasonality; no prediction intervals are implemented.",
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
        or artifact.get("artifact_version") != 1
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
    return {
        "model_id": artifact.get("model_id"),
        "model_version": artifact.get("model_version"),
        "algorithm": artifact["algorithm"],
        "target": artifact.get("target_column"),
        "frequency": "month",
        "aggregation": "sum",
        "source": artifact["source"],
        "artifact_sha256": artifact_sha256 or _digest_json(artifact),
        "evaluation": artifact["validation"],
        "forecast": [
            {
                "period": period,
                "predicted": float(value),
                "kind": "forecast",
            }
            for period, value in zip(forecast_periods, predicted, strict=True)
        ],
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
