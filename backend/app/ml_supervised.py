from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Literal

import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
    r2_score,
)
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits


MAX_SUPERVISED_ROWS = 20_000
MAX_SUPERVISED_FEATURES = 30
MAX_SUPERVISED_CLASSES = 20
MAX_CATEGORIES_PER_FEATURE = 32
MIN_SUPERVISED_ROWS = 30
MAX_RESULT_BYTES = 128 * 1024
RANDOM_SEED = 42
TEST_FRACTION = 0.2
VALIDATION_FRACTION_OF_TRAIN = 0.2
_COLUMN_NAME = re.compile(r"^[^\x00-\x1f]{1,255}$")
_SUSPICIOUS_NAME = re.compile(
    r"(^|[_\W])(future|after|post|outcome|result|target|label|next|lead|actual)([_\W]|$)",
    re.IGNORECASE,
)


class SupervisedEvaluationError(ValueError):
    def __init__(self, code: str, message: str, *, reasons: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.reasons = reasons or []


def _column_kind(data_type: str) -> str:
    lowered = data_type.lower()
    if lowered.startswith(("int", "uint", "float", "double", "decimal")):
        return "numeric"
    if lowered.startswith(("string", "large_string", "dictionary", "bool", "binary", "utf8")):
        return "categorical"
    if lowered.startswith(("date", "timestamp", "time")):
        return "datetime"
    return "unsupported"


def validate_supervised_request(
    *,
    task_type: str,
    target_column: str,
    feature_columns: list[str],
    schema: list[dict[str, Any]],
    row_count: int,
    profile_columns: list[dict[str, Any]] | None = None,
    time_column: str | None = None,
    group_column: str | None = None,
    confirmed_suspicious_features: list[str] | None = None,
) -> dict[str, Any]:
    if task_type not in {"regression", "classification"}:
        raise SupervisedEvaluationError(
            "task_type_invalid", "Choose either regression or classification."
        )
    if not isinstance(target_column, str) or not _COLUMN_NAME.fullmatch(target_column):
        raise SupervisedEvaluationError("target_invalid", "Select one valid target column.")
    if (
        not isinstance(feature_columns, list)
        or not feature_columns
        or len(feature_columns) > MAX_SUPERVISED_FEATURES
        or any(not isinstance(name, str) or not _COLUMN_NAME.fullmatch(name) for name in feature_columns)
    ):
        raise SupervisedEvaluationError(
            "features_invalid",
            f"Select 1 to {MAX_SUPERVISED_FEATURES} valid feature columns.",
        )
    if len(feature_columns) != len(set(feature_columns)):
        raise SupervisedEvaluationError("features_duplicate", "Feature columns must be unique.")
    if target_column in feature_columns:
        raise SupervisedEvaluationError(
            "target_in_features", "The target column cannot also be a feature."
        )
    if row_count < MIN_SUPERVISED_ROWS or row_count > MAX_SUPERVISED_ROWS:
        raise SupervisedEvaluationError(
            "row_count_unsupported",
            f"Supervised evaluation requires {MIN_SUPERVISED_ROWS} to {MAX_SUPERVISED_ROWS} rows.",
        )
    by_name = {
        item["name"]: item
        for item in schema
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("type"), str)
    }
    requested = [target_column, *feature_columns]
    if time_column is not None:
        requested.append(time_column)
    if group_column is not None:
        requested.append(group_column)
    if len(requested) != len(set(requested)):
        raise SupervisedEvaluationError(
            "column_roles_overlap", "Target, features, time and group columns must be distinct."
        )
    unknown = [name for name in requested if name not in by_name]
    if unknown:
        raise SupervisedEvaluationError(
            "column_unknown", "One or more selected columns are not in the normalized table.",
            reasons=unknown,
        )
    target_kind = _column_kind(by_name[target_column]["type"])
    expected_target_kind = "numeric" if task_type == "regression" else "categorical"
    if target_kind != expected_target_kind:
        raise SupervisedEvaluationError(
            "target_type_unsupported",
            f"{task_type.capitalize()} requires a {expected_target_kind} target column.",
        )
    for name in feature_columns:
        if _column_kind(by_name[name]["type"]) not in {"numeric", "categorical"}:
            raise SupervisedEvaluationError(
                "feature_type_unsupported",
                f"Feature {name!r} must be numeric, boolean, or categorical.",
            )
    if time_column is not None and _column_kind(by_name[time_column]["type"]) != "datetime":
        raise SupervisedEvaluationError(
            "time_column_invalid", "Chronological evaluation requires a datetime time column."
        )
    if time_column and group_column:
        raise SupervisedEvaluationError(
            "split_configuration_unsupported",
            "Choose chronological or group-aware evaluation; combining both is not supported.",
        )

    profile_by_name = {
        item["name"]: item
        for item in (profile_columns or [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    confirmed = set(confirmed_suspicious_features or [])
    for name in feature_columns:
        profile = profile_by_name.get(name, {})
        distinct = (
            profile.get("distinct_value_count")
            if profile.get("distinct_count_exact") is True
            else None
        )
        if distinct == 1:
            raise SupervisedEvaluationError(
                "constant_feature",
                f"Feature {name!r} is constant and cannot support evaluation.",
            )
        if (
            isinstance(distinct, int)
            and not isinstance(distinct, bool)
            and distinct >= max(20, int(row_count * 0.98))
            and name not in confirmed
        ):
            raise SupervisedEvaluationError(
                "identifier_confirmation_required",
                f"Feature {name!r} appears identifier-like; remove it or explicitly confirm its use.",
                reasons=[name],
            )
        if _SUSPICIOUS_NAME.search(name) and name not in confirmed:
            raise SupervisedEvaluationError(
                "feature_confirmation_required",
                f"Feature {name!r} has a name associated with outcomes or future values; explicitly confirm it or remove it.",
                reasons=[name],
            )
    unconfirmed = confirmed.difference(feature_columns)
    if unconfirmed:
        raise SupervisedEvaluationError(
            "feature_confirmation_invalid",
            "Confirmations must refer to selected feature columns.",
            reasons=sorted(unconfirmed),
        )
    return {
        "target_kind": target_kind,
        "feature_kinds": {
            name: _column_kind(by_name[name]["type"]) for name in feature_columns
        },
        "schema": by_name,
    }


def _clean_category(value: Any) -> str | float:
    if value is None:
        return np.nan
    if isinstance(value, float) and not math.isfinite(value):
        return np.nan
    return str(value)


def _make_matrix(
    table,
    feature_columns: list[str],
    feature_kinds: dict[str, str],
) -> tuple[np.ndarray, list[int], list[int]]:
    values: list[list[Any]] = []
    numeric_indices: list[int] = []
    categorical_indices: list[int] = []
    for index, name in enumerate(feature_columns):
        if feature_kinds[name] == "numeric":
            numeric_indices.append(index)
        else:
            categorical_indices.append(index)
    arrays = [table[name].to_pylist() for name in feature_columns]
    for row_index in range(table.num_rows):
        row = []
        for feature_index, name in enumerate(feature_columns):
            value = arrays[feature_index][row_index]
            if feature_kinds[name] == "numeric":
                if value is None:
                    row.append(np.nan)
                else:
                    converted = float(value)
                    row.append(converted if math.isfinite(converted) else np.nan)
            else:
                row.append(_clean_category(value))
        values.append(row)
    return np.asarray(values, dtype=object), numeric_indices, categorical_indices


def _pipeline(
    estimator,
    numeric_indices: list[int],
    categorical_indices: list[int],
) -> Pipeline:
    transformers = []
    if numeric_indices:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    [
                        (
                            "imputer",
                            SimpleImputer(
                                strategy="median",
                                keep_empty_features=True,
                            ),
                        ),
                        ("scaler", StandardScaler()),
                    ]
                ),
                numeric_indices,
            )
        )
    if categorical_indices:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        (
                            "imputer",
                            SimpleImputer(
                                strategy="most_frequent",
                                keep_empty_features=True,
                            ),
                        ),
                        (
                            "encoder",
                            OneHotEncoder(
                                handle_unknown="ignore",
                                max_categories=MAX_CATEGORIES_PER_FEATURE,
                            ),
                        ),
                    ]
                ),
                categorical_indices,
            )
        )
    return Pipeline(
        [
            (
                "preprocessor",
                ColumnTransformer(transformers, remainder="drop"),
            ),
            ("model", estimator),
        ]
    )


def _finite_metric(value: float) -> float | None:
    result = float(value)
    return result if math.isfinite(result) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _row_token(value: Any) -> tuple[str, str]:
    if value is None or (
        isinstance(value, (float, np.floating)) and not math.isfinite(float(value))
    ):
        return ("missing", "")
    return (type(value).__name__, str(value))


def _classification_metrics(y_true, prediction, labels, probabilities=None) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "accuracy": _finite_metric(accuracy_score(y_true, prediction)),
        "balanced_accuracy": _finite_metric(balanced_accuracy_score(y_true, prediction)),
        "precision_weighted": _finite_metric(
            precision_score(y_true, prediction, average="weighted", zero_division=0)
        ),
        "recall_weighted": _finite_metric(
            recall_score(y_true, prediction, average="weighted", zero_division=0)
        ),
        "f1_weighted": _finite_metric(
            f1_score(y_true, prediction, average="weighted", zero_division=0)
        ),
        "confusion_matrix": confusion_matrix(
            y_true, prediction, labels=labels
        ).astype(int).tolist(),
        "labels": [str(label) for label in labels],
        "roc_auc": None,
        "roc_auc_reason": None,
    }
    if len(labels) == 2 and probabilities is not None and len(set(y_true)) == 2:
        try:
            metrics["roc_auc"] = _finite_metric(
                roc_auc_score(y_true, probabilities[:, 1])
            )
        except ValueError:
            metrics["roc_auc_reason"] = "ROC-AUC could not be calculated for the test split."
    else:
        metrics["roc_auc_reason"] = (
            "ROC-AUC is reported only for binary classification with both classes in the test split."
        )
    return metrics


def evaluate_supervised(
    *,
    table,
    task_type: Literal["regression", "classification"],
    target_column: str,
    feature_columns: list[str],
    dataset_id: int,
    file_id: int,
    table_index: int,
    source_fingerprint: str,
    artifact_sha256: str,
    feature_kinds: dict[str, str],
    time_column: str | None = None,
    group_column: str | None = None,
    confirmed_suspicious_features: list[str] | None = None,
) -> dict[str, Any]:
    if table.num_rows > MAX_SUPERVISED_ROWS:
        raise SupervisedEvaluationError(
            "row_count_unsupported",
            f"Supervised evaluation is limited to {MAX_SUPERVISED_ROWS} rows.",
        )
    matrix, numeric_indices, categorical_indices = _make_matrix(
        table, feature_columns, feature_kinds
    )
    raw_target = table[target_column].to_pylist()
    if any(value is None for value in raw_target):
        raise SupervisedEvaluationError(
            "target_missing",
            "Target values must be present for every evaluated row; target imputation is not supported.",
        )
    if task_type == "regression":
        try:
            y = np.asarray([float(value) for value in raw_target], dtype=np.float64)
        except (TypeError, ValueError, OverflowError):
            raise SupervisedEvaluationError(
                "target_invalid", "Regression target contains non-numeric values."
            ) from None
        if not np.isfinite(y).all():
            raise SupervisedEvaluationError(
                "target_nonfinite", "Regression target contains non-finite values."
            )
        target_constant = bool(np.ptp(y) == 0)
        labels = None
    else:
        y = np.asarray([str(value) for value in raw_target], dtype=object)
        labels, counts = np.unique(y, return_counts=True)
        if len(labels) < 2:
            raise SupervisedEvaluationError(
                "target_one_class", "Classification requires at least two target classes."
            )
        if len(labels) > MAX_SUPERVISED_CLASSES:
            raise SupervisedEvaluationError(
                "target_cardinality_unsupported",
                f"Classification supports at most {MAX_SUPERVISED_CLASSES} target classes.",
            )
        if counts.min() < 4:
            raise SupervisedEvaluationError(
                "rare_target_class",
                "Each class needs at least four rows for the training, validation and test splits.",
                reasons=[f"Smallest class has {int(counts.min())} row(s)."],
            )
    confirmed = set(confirmed_suspicious_features or [])
    feature_warnings: list[str] = []
    for name in feature_columns:
        raw_feature = table[name].to_pylist()
        observed_feature_values = []
        for value in raw_feature:
            if value is None:
                continue
            if feature_kinds[name] == "numeric":
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(numeric_value):
                    observed_feature_values.append(numeric_value)
            else:
                observed_feature_values.append(str(value))
        if not observed_feature_values:
            raise SupervisedEvaluationError(
                "feature_all_missing",
                f"Feature {name!r} contains no usable observations.",
            )
        if len(set(map(str, observed_feature_values))) == 1:
            raise SupervisedEvaluationError(
                "constant_feature",
                f"Feature {name!r} is constant in the normalized source.",
            )
        if task_type == "regression":
            feature_values = []
            for value in raw_feature:
                try:
                    converted = float(value) if value is not None else np.nan
                except (TypeError, ValueError, OverflowError):
                    converted = np.nan
                feature_values.append(converted if math.isfinite(converted) else np.nan)
            comparable = np.isfinite(feature_values)
            target_values = np.asarray(y, dtype=np.float64)
            exact = bool(
                np.all(comparable)
                and len(feature_values) == len(target_values)
                and np.array_equal(np.asarray(feature_values), target_values)
            )
            near = False
            if int(comparable.sum()) >= 10:
                target_finite = target_values[comparable]
                feature_finite = np.asarray(feature_values)[comparable]
                if np.std(target_finite) > 0 and np.std(feature_finite) > 0:
                    correlation = float(np.corrcoef(target_finite, feature_finite)[0, 1])
                    near = math.isfinite(correlation) and abs(correlation) >= 0.995
        else:
            feature_values = [None if value is None else str(value) for value in raw_feature]
            exact = all(
                value is not None and value == target
                for value, target in zip(feature_values, y, strict=True)
            )
            valid_pairs = [
                (value, target)
                for value, target in zip(feature_values, y, strict=True)
                if value is not None
            ]
            near = (
                bool(valid_pairs)
                and sum(value == target for value, target in valid_pairs) / len(valid_pairs) >= 0.995
            )
        if exact:
            raise SupervisedEvaluationError(
                "target_copy_feature",
                f"Feature {name!r} exactly duplicates the selected target and is rejected as leakage.",
                reasons=[name],
            )
        if near and name not in confirmed:
            raise SupervisedEvaluationError(
                "target_derived_feature_confirmation_required",
                f"Feature {name!r} closely matches the target; explicitly confirm its use or remove it.",
                reasons=[name],
            )
        if near:
            feature_warnings.append(
                f"Feature {name!r} closely matches the target and was retained only after explicit user confirmation."
            )
        missing_fraction = sum(
            value is None
            or (
                feature_kinds[name] == "numeric"
                and isinstance(value, (int, float, np.number))
                and not math.isfinite(float(value))
            )
            for value in raw_feature
        ) / len(raw_feature)
        if missing_fraction >= 0.5:
            feature_warnings.append(
                f"Feature {name!r} has at least 50% missing values; training-only imputation may reduce its usefulness."
            )
        feature_counts = Counter(map(str, observed_feature_values))
        if (
            len(observed_feature_values) >= 10
            and len(feature_counts) > 1
            and max(feature_counts.values()) / len(observed_feature_values) >= 0.98
        ):
            feature_warnings.append(f"Feature {name!r} is near-constant.")
        if (
            feature_kinds[name] == "categorical"
            and len(feature_counts) > MAX_CATEGORIES_PER_FEATURE
        ):
            feature_warnings.append(
                f"Feature {name!r} has more than {MAX_CATEGORIES_PER_FEATURE} observed values; infrequent categories are grouped by the bounded encoder."
            )
        if name in confirmed:
            feature_warnings.append(
                f"Use of feature {name!r} was explicitly confirmed by the user."
            )

    if time_column:
        raw_times = table[time_column].to_pylist()
        if any(value is None for value in raw_times):
            raise SupervisedEvaluationError(
                "time_values_missing",
                "Chronological evaluation requires a non-missing time value for every row.",
            )
        try:
            order = np.asarray(
                sorted(range(len(raw_times)), key=lambda index: raw_times[index]),
                dtype=np.int64,
            )
        except TypeError:
            raise SupervisedEvaluationError(
                "time_values_invalid", "Time values cannot be consistently ordered."
            ) from None
        matrix = matrix[order]
        y = y[order]
        ordered_times = [raw_times[int(index)] for index in order]
        if group_column:
            raise SupervisedEvaluationError(
                "split_configuration_unsupported",
                "Chronological evaluation cannot currently be combined with group-aware splitting.",
            )
        test_count = max(2, int(math.ceil(len(y) * TEST_FRACTION)))
        test_boundary = ordered_times[len(y) - test_count]
        train_indices = np.asarray(
            [index for index, value in enumerate(ordered_times) if value < test_boundary],
            dtype=np.int64,
        )
        test_indices = np.asarray(
            [index for index, value in enumerate(ordered_times) if value >= test_boundary],
            dtype=np.int64,
        )
        if len(train_indices) < 10:
            raise SupervisedEvaluationError(
                "split_insufficient", "The chronological split leaves too few training rows."
            )
        split_strategy = {
            "type": "chronological_holdout",
            "reason": "The caller explicitly selected a datetime column; earlier rows train and later rows test.",
            "time_column": time_column,
            "test_fraction": TEST_FRACTION,
            "training_start": _json_safe(ordered_times[int(train_indices[0])]),
            "training_end": _json_safe(ordered_times[int(train_indices[-1])]),
            "test_start": _json_safe(ordered_times[int(test_indices[0])]),
            "test_end": _json_safe(ordered_times[int(test_indices[-1])]),
        }
    elif group_column:
        raw_groups = table[group_column].to_pylist()
        if any(
            value is None
            or (
                isinstance(value, (float, np.floating))
                and not math.isfinite(float(value))
            )
            for value in raw_groups
        ):
            raise SupervisedEvaluationError(
                "group_values_missing", "Group-aware evaluation requires a group for every row."
            )
        groups = np.asarray([str(value) for value in raw_groups], dtype=object)
        if len(set(groups.tolist())) < 5:
            raise SupervisedEvaluationError(
                "groups_insufficient", "Group-aware evaluation requires at least five distinct groups."
            )
        splitter = GroupShuffleSplit(
            n_splits=1,
            test_size=TEST_FRACTION,
            random_state=RANDOM_SEED,
        )
        train_indices, test_indices = next(splitter.split(matrix, y, groups))
        split_strategy = {
            "type": "group_holdout",
            "reason": "Groups are held out as whole entities to prevent cross-split group overlap.",
            "group_column": group_column,
            "test_fraction": TEST_FRACTION,
        }
    else:
        stratify = y if task_type == "classification" else None
        try:
            train_indices, test_indices = train_test_split(
                np.arange(len(y)),
                test_size=TEST_FRACTION,
                random_state=RANDOM_SEED,
                stratify=stratify,
            )
        except ValueError as error:
            raise SupervisedEvaluationError(
                "split_insufficient", f"A valid held-out split cannot be produced: {error}"
            ) from None
        split_strategy = {
            "type": "stratified_random_holdout" if stratify is not None else "random_holdout",
            "reason": (
                "A deterministic stratified split preserves class proportions."
                if stratify is not None
                else "A deterministic held-out split is used; provide a time or group column when observations are dependent."
            ),
            "test_fraction": TEST_FRACTION,
        }

    if group_column:
        all_groups = np.asarray([str(value) for value in table[group_column].to_pylist()])
        train_groups = all_groups[train_indices]
        validation_split = GroupShuffleSplit(
            n_splits=1,
            test_size=VALIDATION_FRACTION_OF_TRAIN,
            random_state=RANDOM_SEED,
        )
        fit_positions, validation_positions = next(
            validation_split.split(
                matrix[train_indices],
                y[train_indices],
                train_groups,
            )
        )
        fit_indices = train_indices[fit_positions]
        validation_indices = train_indices[validation_positions]
        split_strategy["validation"] = "group_holdout_within_training_partition"
    else:
        if time_column:
            validation_count = max(2, int(math.ceil(len(train_indices) * VALIDATION_FRACTION_OF_TRAIN)))
            validation_boundary = ordered_times[
                int(train_indices[len(train_indices) - validation_count])
            ]
            fit_indices = np.asarray(
                [
                    index for index in train_indices
                    if ordered_times[int(index)] < validation_boundary
                ],
                dtype=np.int64,
            )
            validation_indices = np.asarray(
                [
                    index for index in train_indices
                    if ordered_times[int(index)] >= validation_boundary
                ],
                dtype=np.int64,
            )
            split_strategy["validation"] = "chronological_holdout_within_training_partition"
        else:
            try:
                inner_stratify = y[train_indices] if task_type == "classification" else None
                fit_indices, validation_indices = train_test_split(
                    train_indices,
                    test_size=VALIDATION_FRACTION_OF_TRAIN,
                    random_state=RANDOM_SEED,
                    stratify=inner_stratify,
                )
                split_strategy["validation"] = (
                    "stratified_training_only_holdout"
                    if inner_stratify is not None
                    else "training_only_holdout"
                )
            except ValueError:
                raise SupervisedEvaluationError(
                    "validation_split_insufficient",
                    "A separate training-only validation split cannot be produced without weakening the evaluation.",
                ) from None
    if len(fit_indices) < 10 or len(validation_indices) < 2 or len(test_indices) < 2:
        raise SupervisedEvaluationError(
            "split_insufficient", "The requested split leaves too few evaluation observations."
        )
    if group_column:
        fit_group_values = set(all_groups[fit_indices].tolist())
        validation_group_values = set(all_groups[validation_indices].tolist())
        test_group_values = set(all_groups[test_indices].tolist())
        if (
            fit_group_values & validation_group_values
            or fit_group_values & test_group_values
            or validation_group_values & test_group_values
        ):
            raise SupervisedEvaluationError(
                "group_overlap", "Group-aware split produced overlapping groups."
            )

    observation_keys = set()
    duplicate_observation_count = 0
    for row_index, row in enumerate(matrix):
        key = tuple(_row_token(value) for value in row) + (_row_token(y[row_index]),)
        if key in observation_keys:
            duplicate_observation_count += 1
        else:
            observation_keys.add(key)

    if task_type == "regression":
        candidates = {
            "ridge": Ridge(alpha=1.0),
            "random_forest": RandomForestRegressor(
                n_estimators=50,
                max_depth=8,
                min_samples_leaf=5,
                max_features=1.0,
                random_state=RANDOM_SEED,
                n_jobs=1,
            ),
        }
        baseline = DummyRegressor(strategy="mean")
    else:
        candidates = {
            "regularized_logistic": LogisticRegression(
                C=1.0,
                max_iter=500,
                random_state=RANDOM_SEED,
            ),
            "random_forest": RandomForestClassifier(
                n_estimators=50,
                max_depth=8,
                min_samples_leaf=5,
                max_features=1.0,
                random_state=RANDOM_SEED,
                n_jobs=1,
                class_weight="balanced",
            ),
        }
        baseline = DummyClassifier(strategy="most_frequent")

    validation_scores: dict[str, float] = {}
    candidate_errors: dict[str, str] = {}

    def primary_score(actual, predicted) -> float:
        if task_type == "regression":
            return float(mean_absolute_error(actual, predicted))
        return float(accuracy_score(actual, predicted))

    with threadpool_limits(limits=1):
        baseline_pipeline = _pipeline(
            baseline, numeric_indices, categorical_indices
        )
        baseline_pipeline.fit(matrix[fit_indices], y[fit_indices])
        baseline_validation = baseline_pipeline.predict(matrix[validation_indices])
        baseline_score = primary_score(y[validation_indices], baseline_validation)
        for name, estimator in candidates.items():
            model = _pipeline(estimator, numeric_indices, categorical_indices)
            try:
                model.fit(matrix[fit_indices], y[fit_indices])
                prediction = model.predict(matrix[validation_indices])
                score = primary_score(y[validation_indices], prediction)
                if math.isfinite(score):
                    validation_scores[name] = score
                else:
                    candidate_errors[name] = "Validation metric was non-finite."
            except (ValueError, FloatingPointError, MemoryError) as error:
                candidate_errors[name] = f"Candidate could not be evaluated ({type(error).__name__})."
        if not validation_scores:
            raise SupervisedEvaluationError(
                "candidate_models_failed", "No supervised candidate completed training-only validation."
            )
        selected_name = (
            min(validation_scores, key=validation_scores.get)
            if task_type == "regression"
            else max(validation_scores, key=validation_scores.get)
        )
        selected_validation_score = validation_scores[selected_name]
        candidate_beats_baseline = (
            selected_validation_score < baseline_score
            if task_type == "regression"
            else selected_validation_score > baseline_score
        )
        selected_estimator = candidates[selected_name]
        final_pipeline = _pipeline(selected_estimator, numeric_indices, categorical_indices)
        final_pipeline.fit(matrix[train_indices], y[train_indices])
        baseline_final = _pipeline(
            DummyRegressor(strategy="mean")
            if task_type == "regression"
            else DummyClassifier(strategy="most_frequent"),
            numeric_indices,
            categorical_indices,
        )
        baseline_final.fit(matrix[train_indices], y[train_indices])
        final_prediction = final_pipeline.predict(matrix[test_indices])
        baseline_prediction = baseline_final.predict(matrix[test_indices])
        probabilities = (
            final_pipeline.predict_proba(matrix[test_indices])
            if task_type == "classification"
            and hasattr(final_pipeline, "predict_proba")
            else None
        )
        if probabilities is not None and probabilities.shape[1] != len(labels):
            probabilities = None
        if task_type == "regression":
            model_metrics = {
                "mae": _finite_metric(mean_absolute_error(y[test_indices], final_prediction)),
                "rmse": _finite_metric(
                    math.sqrt(mean_squared_error(y[test_indices], final_prediction))
                ),
                "r2": None,
                "r2_reason": None,
            }
            if len(test_indices) > 1 and np.var(y[test_indices]) > 0:
                model_metrics["r2"] = _finite_metric(
                    r2_score(y[test_indices], final_prediction)
                )
                if model_metrics["r2"] is None:
                    model_metrics["r2_reason"] = "R² exceeded finite numeric limits."
            else:
                model_metrics["r2_reason"] = "R² is undefined for a constant or one-row test target."
            baseline_metrics = {
                "mae": _finite_metric(mean_absolute_error(y[test_indices], baseline_prediction)),
                "rmse": _finite_metric(
                    math.sqrt(mean_squared_error(y[test_indices], baseline_prediction))
                ),
                "r2": None,
            }
            if len(test_indices) > 1 and np.var(y[test_indices]) > 0:
                baseline_metrics["r2"] = _finite_metric(
                    r2_score(y[test_indices], baseline_prediction)
                )
            unavailable = [
                f"{name} exceeded finite numeric limits."
                for name in ("mae", "rmse")
                if model_metrics[name] is None or baseline_metrics[name] is None
            ]
            if unavailable:
                model_metrics["metric_limitations"] = unavailable
                baseline_metrics["metric_limitations"] = unavailable
            test_beats_baseline = (
                model_metrics["mae"] is not None
                and baseline_metrics["mae"] is not None
                and model_metrics["mae"] < baseline_metrics["mae"]
            )
        else:
            model_metrics = _classification_metrics(
                y[test_indices], final_prediction, labels, probabilities
            )
            baseline_metrics = _classification_metrics(
                y[test_indices], baseline_prediction, labels
            )
            test_beats_baseline = model_metrics["accuracy"] > baseline_metrics["accuracy"]

    warnings = [
        "The held-out test set was used once for final reporting; model selection used only a separate training-only validation split.",
        "Predictive association does not establish causality or business impact.",
    ]
    if task_type == "regression" and target_constant:
        warnings.append(
            "The regression target is constant; R² is not meaningful and model comparisons may be uninformative."
        )
    warnings.extend(feature_warnings)
    if candidate_errors:
        warnings.append("One or more candidate models could not be evaluated.")
    if duplicate_observation_count:
        warnings.append(
            f"{duplicate_observation_count} duplicate selected feature/target row(s) were found; split-level leakage may remain for duplicate observations."
        )
    if not candidate_beats_baseline:
        warnings.append("No candidate beat the baseline on the training-only validation metric.")
    if not test_beats_baseline:
        warnings.append("The selected model did not beat the baseline on the held-out test metric.")
    if task_type == "classification":
        _, class_counts = np.unique(y[train_indices], return_counts=True)
        if class_counts.min() / len(train_indices) < 0.05:
            warnings.append("The training target is severely imbalanced; weighted metrics may obscure minority-class performance.")
        candidate_model_identifiers = {
            "regularized_logistic": "sklearn.linear_model.LogisticRegression(C=1.0,max_iter=500)",
            "random_forest": (
                "sklearn.ensemble.RandomForestClassifier("
                "n_estimators=50,max_depth=8,min_samples_leaf=5,n_jobs=1)"
            ),
            "dummy_baseline": "sklearn.dummy.DummyClassifier(strategy='most_frequent')",
        }
    else:
        candidate_model_identifiers = {
            "ridge": "sklearn.linear_model.Ridge(alpha=1.0)",
            "random_forest": (
                "sklearn.ensemble.RandomForestRegressor("
                "n_estimators=50,max_depth=8,min_samples_leaf=5,n_jobs=1)"
            ),
            "dummy_baseline": "sklearn.dummy.DummyRegressor(strategy='mean')",
        }
    result = {
        "result_schema_version": 1,
        "task_type": task_type,
        "status": "completed",
        "dataset_id": dataset_id,
        "file_id": file_id,
        "table_index": table_index,
        "source_fingerprint": source_fingerprint,
        "normalized_artifact_sha256": artifact_sha256,
        "target_column": target_column,
        "feature_columns": feature_columns,
        "confirmed_suspicious_features": sorted(confirmed),
        "evaluation": {
            "split_strategy": split_strategy,
            "random_seed": RANDOM_SEED,
            "fit_sample_count": int(len(fit_indices)),
            "validation_sample_count": int(len(validation_indices)),
            "training_sample_count": int(len(train_indices)),
            "test_sample_count": int(len(test_indices)),
            "baseline_validation_score": _finite_metric(baseline_score),
            "test_result": {
                "model": model_metrics,
                "baseline": baseline_metrics,
                "baseline_name": "dummy",
                "model_beats_baseline": bool(test_beats_baseline),
                "selection_metric": "mae" if task_type == "regression" else "accuracy",
                "training_validation_scores": validation_scores,
                "selected_candidate": selected_name,
                "selected_candidate_beats_baseline_on_validation": bool(candidate_beats_baseline),
            },
        },
        "candidate_models": sorted(candidates),
        "candidate_model_identifiers": candidate_model_identifiers,
        "candidate_errors": candidate_errors,
        "warnings": warnings,
        "limitations": [
            "Heuristic name and identifier checks cannot detect every source of target leakage.",
            "No fitted executable model is persisted; this result records evaluation evidence only.",
            "Metrics describe this specific split and should not be treated as a guarantee of future performance.",
        ],
        "library_versions": {"scikit_learn": sklearn.__version__, "numpy": np.__version__},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_safe,
    ).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise SupervisedEvaluationError(
            "result_size_limit", "The evaluation result exceeds its serialized size limit."
        )
    result["result_sha256"] = hashlib.sha256(encoded).hexdigest()
    return result
