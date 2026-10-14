import hashlib
import json
import math
import re
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError

from app.normalization import (
    DEFAULT_MAX_OUTPUT_BYTES,
    NormalizationError,
    validate_normalization_result,
)
from app.execution import (
    ExecutionBudget,
    ExecutionBudgetExceeded,
    estimate_decoded_python_string_bytes,
)
from app.profiling import _MAX_DISPLAY_VALUE_LENGTH
from app.provenance import build_provenance, schema_fingerprint, validate_provenance
from app.quality_analysis import (
    QUALITY_ANALYSIS_CONFIGURATION,
    QualityAnalysisError,
    normalized_output_provenance,
    normalized_summary_totals,
    quality_analysis_key,
    validate_quality_report,
)


EDA_RESULT_SCHEMA_VERSION = 1
EDA_ALGORITHM_VERSION = "univariate-1"
MAX_EDA_COLUMNS = 32
MAX_EDA_PAIRS = 64
MAX_EDA_MULTIVARIATE_FEATURES = 16
MIN_HISTOGRAM_BINS = 2
MAX_HISTOGRAM_BINS = 100
MAX_EDA_PERCENTILES = 16
MAX_EDA_CATEGORIES = 100
MAX_EDA_RESULT_BYTES = 1024 * 1024
DEFAULT_EDA_RESULT_BYTES = 256 * 1024
MAX_EDA_NUMERIC_VALUES = 250_000
MAX_EDA_TRACKED_CATEGORIES = 50_000
MAX_EDA_GROUPED_VALUES = 250_000
MAX_EDA_CONTINGENCY_CELLS = 50_000
MAX_EDA_PAIRWISE_VALUES = 4_000_000
MAX_EDA_DECODED_VALUE_BYTES = 128 * 1024 * 1024
MAX_EDA_ROWS = 1_000_000
MAX_EDA_SCAN_BYTES = DEFAULT_MAX_OUTPUT_BYTES
EDA_BATCH_SIZE = 4096
_COLUMN_NAME_LIMIT = 256
_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_SUPPORTED_ANALYSIS_TYPES = {"univariate", "bivariate", "multivariate"}


def _algorithm_version_for_analysis(analysis_type: str) -> str:
    return {
        "univariate": "univariate-1",
        "bivariate": "bivariate-1",
        "multivariate": "multivariate-1",
    }.get(analysis_type, EDA_ALGORITHM_VERSION)


class EDAError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class EDAConfiguration:
    schema_version: int = 1
    analysis_type: str = "univariate"
    table_index: int = 0
    selected_columns: tuple[str, ...] = ()
    column_pairs: tuple[tuple[str, str], ...] = ()
    histogram_bins: int = 10
    percentiles: tuple[float, ...] = (25.0, 50.0, 75.0)
    max_categories: int = 10
    max_result_bytes: int = DEFAULT_EDA_RESULT_BYTES
    max_pairs: int = 32
    max_features: int = 16

    def __post_init__(self) -> None:
        _validate_configuration(self)

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "analysis_type": self.analysis_type,
            "table_index": self.table_index,
            "selected_columns": list(self.selected_columns),
            "column_pairs": [[left, right] for left, right in self.column_pairs],
            "histogram_bins": self.histogram_bins,
            "percentiles": list(self.percentiles),
            "max_categories": self.max_categories,
            "max_result_bytes": self.max_result_bytes,
            "max_pairs": self.max_pairs,
            "max_features": self.max_features,
        }


@dataclass(frozen=True)
class EDAFailure:
    code: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EDAColumnResult:
    name: str
    data_type: str
    status: Literal["completed", "unsupported"]
    total_rows: int
    non_null_count: int | None
    null_count: int | None
    null_percentage: float | None
    statistics: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    related_quality_findings: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "status": self.status,
            "total_rows": self.total_rows,
            "non_null_count": self.non_null_count,
            "null_count": self.null_count,
            "null_percentage": self.null_percentage,
            "statistics": self.statistics,
            "warnings": list(self.warnings),
            "related_quality_findings": list(self.related_quality_findings),
        }


@dataclass(frozen=True)
class EDAColumnPairResult:
    left_column: str
    right_column: str
    pair_type: str
    valid_observations: int
    statistics: dict[str, Any]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "left_column": self.left_column,
            "right_column": self.right_column,
            "pair_type": self.pair_type,
            "valid_observations": self.valid_observations,
            "statistics": self.statistics,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class EDAResult:
    schema_version: int
    analysis_type: str
    status: Literal["completed", "partially_completed", "failed"]
    dataset_id: int
    file_id: int
    table_index: int
    table_name: str | None
    artifact_sha256: str | None
    normalization_version: str | None
    algorithm_version: str
    configuration: dict[str, Any]
    configuration_sha256: str
    executed_at: str
    requested_columns: tuple[str, ...]
    analyzed_columns: tuple[str, ...]
    rows_analyzed: int
    exact: bool
    sampling: None
    columns: tuple[EDAColumnResult, ...]
    warnings: tuple[str, ...] = ()
    failure: EDAFailure | None = None
    pair_results: tuple[EDAColumnPairResult, ...] = ()
    multivariate_summary: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "analysis_type": self.analysis_type,
            "status": self.status,
            "source": {
                "dataset_id": self.dataset_id,
                "file_id": self.file_id,
                "table_index": self.table_index,
                "table_name": self.table_name,
                "artifact_sha256": self.artifact_sha256,
                "normalization_version": self.normalization_version,
            },
            "algorithm_version": self.algorithm_version,
            "configuration": self.configuration,
            "configuration_sha256": self.configuration_sha256,
            "executed_at": self.executed_at,
            "requested_columns": list(self.requested_columns),
            "analyzed_columns": list(self.analyzed_columns),
            "rows_analyzed": self.rows_analyzed,
            "exact": self.exact,
            "sampling": self.sampling,
            "columns": [column.as_dict() for column in self.columns],
            "warnings": list(self.warnings),
            "failure": self.failure.as_dict() if self.failure else None,
            "pair_results": [pair.as_dict() for pair in self.pair_results],
            "multivariate_summary": self.multivariate_summary,
            "provenance": self.provenance,
        }


@dataclass
class _ColumnAccumulator:
    field: pa.Field
    values: list[float] = field(default_factory=list)
    categories: dict[str, list[Any]] = field(default_factory=dict)
    null_count: int = 0
    non_finite_count: int = 0
    empty_count: int = 0
    minimum: Any = None
    maximum: Any = None
    categories_truncated: bool = False


def _validate_configuration(configuration: EDAConfiguration) -> None:
    if (
        isinstance(configuration.schema_version, bool)
        or configuration.schema_version != EDA_RESULT_SCHEMA_VERSION
    ):
        raise EDAError("unsupported_schema_version", "EDA configuration version is unsupported.")
    if (
        not isinstance(configuration.analysis_type, str)
        or configuration.analysis_type not in _SUPPORTED_ANALYSIS_TYPES
    ):
        raise EDAError(
            "unsupported_analysis_type",
            "EDA analysis type is unsupported. Supported values are 'univariate', 'bivariate', and 'multivariate'.",
        )
    if (
        isinstance(configuration.table_index, bool)
        or not isinstance(configuration.table_index, int)
        or configuration.table_index < 0
    ):
        raise EDAError("invalid_table_index", "EDA table_index must be a non-negative integer.")
    columns = configuration.selected_columns
    if (
        not isinstance(columns, tuple)
        or not columns
        or len(columns) > MAX_EDA_COLUMNS
        or any(
            not isinstance(name, str)
            or not name.strip()
            or len(name) > _COLUMN_NAME_LIMIT
            for name in columns
        )
    ):
        raise EDAError(
            "invalid_selected_columns",
            f"EDA must select 1 to {MAX_EDA_COLUMNS} bounded column names.",
        )
    if len(columns) != len(set(columns)):
        raise EDAError("duplicate_columns", "EDA selected columns must be unique.")
    if configuration.analysis_type == "bivariate" and len(columns) < 2:
        raise EDAError("invalid_selected_columns", "Bivariate EDA requires at least two selected columns.")
    if configuration.analysis_type == "multivariate" and len(columns) < 2:
        raise EDAError("invalid_selected_columns", "Multivariate EDA requires at least two selected columns.")
    if configuration.column_pairs:
        if configuration.analysis_type != "bivariate":
            raise EDAError(
                "invalid_column_pairs",
                "column_pairs may only be supplied for bivariate analysis.",
            )
        if not isinstance(configuration.column_pairs, tuple):
            raise EDAError("invalid_column_pairs", "column_pairs must be a list of [left, right] pairs.")
        if len(configuration.column_pairs) > MAX_EDA_PAIRS:
            raise EDAError("invalid_column_pairs", f"column_pairs may include at most {MAX_EDA_PAIRS} pairs.")
        for pair in configuration.column_pairs:
            if not isinstance(pair, tuple) or len(pair) != 2 or not all(isinstance(name, str) and name.strip() for name in pair):
                raise EDAError("invalid_column_pairs", "Each column pair must contain two non-empty column names.")
        if len({name for pair in configuration.column_pairs for name in pair}) > MAX_EDA_COLUMNS:
            raise EDAError(
                "invalid_column_pairs",
                f"column_pairs may reference at most {MAX_EDA_COLUMNS} distinct columns.",
            )
        if len(set(tuple(pair) for pair in configuration.column_pairs)) != len(configuration.column_pairs):
            raise EDAError("duplicate_columns", "EDA selected column pairs must be unique.")
    if (
        isinstance(configuration.histogram_bins, bool)
        or not isinstance(configuration.histogram_bins, int)
        or not MIN_HISTOGRAM_BINS
        <= configuration.histogram_bins
        <= MAX_HISTOGRAM_BINS
    ):
        raise EDAError(
            "invalid_histogram_bins",
            f"Histogram bins must be between {MIN_HISTOGRAM_BINS} and {MAX_HISTOGRAM_BINS}.",
        )
    percentiles = configuration.percentiles
    if (
        not isinstance(percentiles, tuple)
        or len(percentiles) > MAX_EDA_PERCENTILES
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 100
            for value in percentiles
        )
        or tuple(sorted(set(percentiles))) != percentiles
    ):
        raise EDAError(
            "invalid_percentiles",
            f"Percentiles must be unique, sorted numbers from 0 to 100, with at most {MAX_EDA_PERCENTILES} entries.",
        )
    if (
        isinstance(configuration.max_categories, bool)
        or not isinstance(configuration.max_categories, int)
        or not 1 <= configuration.max_categories <= MAX_EDA_CATEGORIES
    ):
        raise EDAError(
            "invalid_category_limit",
            f"max_categories must be between 1 and {MAX_EDA_CATEGORIES}.",
        )
    if (
        isinstance(configuration.max_result_bytes, bool)
        or not isinstance(configuration.max_result_bytes, int)
        or not 1024
        <= configuration.max_result_bytes
        <= MAX_EDA_RESULT_BYTES
    ):
        raise EDAError(
            "invalid_result_limit",
            f"max_result_bytes must be between 1024 and {MAX_EDA_RESULT_BYTES}.",
        )
    if (
        isinstance(configuration.max_pairs, bool)
        or not isinstance(configuration.max_pairs, int)
        or not 1 <= configuration.max_pairs <= MAX_EDA_PAIRS
    ):
        raise EDAError("invalid_max_pairs", f"max_pairs must be between 1 and {MAX_EDA_PAIRS}.")
    if configuration.column_pairs and len(configuration.column_pairs) > configuration.max_pairs:
        raise EDAError(
            "invalid_column_pairs",
            "column_pairs may include at most max_pairs pairs.",
        )
    if (
        isinstance(configuration.max_features, bool)
        or not isinstance(configuration.max_features, int)
        or not 1 <= configuration.max_features <= MAX_EDA_MULTIVARIATE_FEATURES
    ):
        raise EDAError(
            "invalid_max_features",
            f"max_features must be between 1 and {MAX_EDA_MULTIVARIATE_FEATURES}.",
        )


def eda_configuration_from_dict(value: dict) -> EDAConfiguration:
    if not isinstance(value, dict):
        raise EDAError("invalid_configuration", "EDA configuration must be an object.")
    allowed = {
        "schema_version",
        "analysis_type",
        "table_index",
        "selected_columns",
        "column_pairs",
        "histogram_bins",
        "percentiles",
        "max_categories",
        "max_result_bytes",
        "max_pairs",
        "max_features",
    }
    if set(value) - allowed:
        raise EDAError("invalid_configuration", "EDA configuration has unsupported fields.")
    defaults = EDAConfiguration.__dataclass_fields__
    supplied = dict(value)
    if "selected_columns" in supplied:
        columns = supplied["selected_columns"]
        if not isinstance(columns, (list, tuple)):
            raise EDAError("invalid_selected_columns", "selected_columns must be a list.")
        supplied["selected_columns"] = tuple(columns)
    if "column_pairs" in supplied:
        pairs = supplied["column_pairs"]
        if not isinstance(pairs, (list, tuple)):
            raise EDAError("invalid_column_pairs", "column_pairs must be a list of [left, right] pairs.")
        if any(
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or not all(isinstance(name, str) for name in pair)
            for pair in pairs
        ):
            raise EDAError(
                "invalid_column_pairs",
                "Each column pair must contain two non-empty column names.",
            )
        supplied["column_pairs"] = tuple(tuple(pair) for pair in pairs)
    if "percentiles" in supplied:
        percentiles = supplied["percentiles"]
        if not isinstance(percentiles, (list, tuple)):
            raise EDAError("invalid_percentiles", "percentiles must be a list.")
        supplied["percentiles"] = tuple(percentiles)
    try:
        return EDAConfiguration(**{
            name: supplied.get(name, definition.default)
            for name, definition in defaults.items()
        })
    except TypeError as error:
        raise EDAError("invalid_configuration", "EDA configuration is malformed.") from error


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise EDAError("invalid_result", "EDA result is not safely serializable.") from error


def _json_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        if math.isclose(number, round(number), rel_tol=0.0, abs_tol=1e-12):
            return int(round(number))
        return number
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if math.isclose(value, round(value), rel_tol=0.0, abs_tol=1e-12):
            return int(round(value))
        return value
    return None


def _to_finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _pearson_corr(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) != len(y_values) or len(x_values) < 2:
        return None
    centered_x = _scaled_centered_values(x_values)
    centered_y = _scaled_centered_values(y_values)
    if centered_x is None or centered_y is None:
        return None
    numerator = math.fsum(x * y for x, y in zip(centered_x, centered_y))
    denominator_x = math.sqrt(math.fsum(x * x for x in centered_x))
    denominator_y = math.sqrt(math.fsum(y * y for y in centered_y))
    if denominator_x == 0.0 or denominator_y == 0.0:
        return None
    return max(-1.0, min(1.0, numerator / (denominator_x * denominator_y)))


def _average_ranks(values: list[float]) -> list[float]:
    ranked = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    position = 0
    while position < len(ranked):
        end = position
        while end + 1 < len(ranked) and ranked[end + 1][1] == ranked[position][1]:
            end += 1
        average_rank = (position + 1 + end + 1) / 2.0
        for index in range(position, end + 1):
            result[ranked[index][0]] = average_rank
        position = end + 1
    return result


def _spearman_corr(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) != len(y_values) or len(x_values) < 2:
        return None
    ranks_x = _average_ranks(x_values)
    ranks_y = _average_ranks(y_values)
    return _pearson_corr(ranks_x, ranks_y)


def _covariance(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) != len(y_values) or len(x_values) < 2:
        return None
    scale_x = max(abs(value) for value in x_values)
    scale_y = max(abs(value) for value in y_values)
    if scale_x == 0.0 or scale_y == 0.0:
        return 0.0
    centered_x = _scaled_centered_values(x_values, scale_x)
    centered_y = _scaled_centered_values(y_values, scale_y)
    if centered_x is None or centered_y is None:
        return None
    normalized_covariance = (
        math.fsum(x * y for x, y in zip(centered_x, centered_y))
        / (len(x_values) - 1)
    )
    return _scaled_product(normalized_covariance, scale_x, scale_y)


def _scaled_centered_values(
    values: list[float],
    scale: float | None = None,
) -> list[float] | None:
    if not values:
        return None
    scale = max(abs(value) for value in values) if scale is None else scale
    if scale == 0.0:
        return [0.0] * len(values)
    origin = values[0]
    deltas = []
    for value in values:
        difference = value - origin
        if math.isfinite(difference):
            deltas.append(difference / scale)
        else:
            deltas.append(value / scale - origin / scale)
    mean_delta = math.fsum(deltas) / len(deltas)
    return [delta - mean_delta for delta in deltas]


def _scaled_product(value: float, *factors: float) -> float | None:
    if value == 0.0:
        return 0.0
    mantissa, exponent = math.frexp(value)
    for factor in factors:
        if factor == 0.0:
            return 0.0
        factor_mantissa, factor_exponent = math.frexp(factor)
        mantissa *= factor_mantissa
        exponent += factor_exponent
        mantissa, adjustment = math.frexp(mantissa)
        exponent += adjustment
    try:
        result = math.ldexp(mantissa, exponent)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def _sample_variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    scale = max(abs(value) for value in values)
    if scale == 0.0:
        return 0.0
    centered = _scaled_centered_values(values, scale)
    if centered is None:
        return None
    normalized_variance = (
        math.fsum(value * value for value in centered) / (len(values) - 1)
    )
    return _scaled_product(normalized_variance, scale, scale)


def _safe_mean(values: list[float]) -> float:
    scale = max((abs(value) for value in values), default=0.0)
    if scale == 0.0:
        return 0.0
    normalized_mean = math.fsum(value / scale for value in values) / len(values)
    return max(-1.0, min(1.0, normalized_mean)) * scale


def _safe_category_key(value: Any) -> str:
    if value is None:
        return "__null__"
    if isinstance(value, str) and len(value) > _MAX_DISPLAY_VALUE_LENGTH:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return json.dumps(
            ["str-sha256", digest],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return json.dumps([type(value).__name__, value], ensure_ascii=False, separators=(",", ":"))


def _category_identity_and_display(value: Any) -> tuple[str, Any]:
    key = _safe_category_key(value)
    if isinstance(value, str) and len(value) > _MAX_DISPLAY_VALUE_LENGTH:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        prefix_length = _MAX_DISPLAY_VALUE_LENGTH - len("...#") - len(digest)
        display_value = value[:prefix_length] + "...#" + digest
        return key, ["str", display_value]
    return key, json.loads(key)


def _display_category(value: str | bool) -> str | bool:
    if isinstance(value, str):
        return value[:_MAX_DISPLAY_VALUE_LENGTH]
    return value


def _category_key(value: str | bool) -> str:
    return json.dumps(
        [type(value).__name__, value],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _quantile(values: list[float], percentile: float) -> float:
    position = (len(values) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def _numeric_statistics(
    values: list[float],
    configuration: EDAConfiguration,
) -> tuple[dict[str, Any], list[str]]:
    if not values:
        return {
            "count": 0,
            "finite_count": 0,
            "mean": None,
            "median": None,
            "minimum": None,
            "maximum": None,
            "range": None,
            "sample_variance_ddof_1": None,
            "sample_standard_deviation_ddof_1": None,
            "percentiles_linear": {},
            "histogram": None,
            "box_plot": None,
            "precision": "IEEE-754 binary64",
        }, ["No finite numeric values are available for statistics."]

    ordered = sorted(values)
    count = len(ordered)
    mean = _safe_mean(ordered)
    minimum, maximum = ordered[0], ordered[-1]
    data_range = maximum - minimum
    variance = _sample_variance(ordered)
    variance_overflow = count > 1 and variance is None
    percentiles = {
        str(value): _json_number(_quantile(ordered, value))
        for value in configuration.percentiles
    }
    quartiles = {
        str(value): _quantile(ordered, value)
        for value in (25.0, 50.0, 75.0)
    }
    q1, q3 = quartiles["25.0"], quartiles["75.0"]
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr
    inside_whiskers = [value for value in ordered if lower_fence <= value <= upper_fence]
    histogram_counts = [0] * configuration.histogram_bins
    edges = []
    if data_range == 0:
        edges = [minimum] * (configuration.histogram_bins + 1)
        histogram_counts[0] = count
    elif math.isfinite(data_range):
        edges = [
            minimum + data_range * (index / configuration.histogram_bins)
            for index in range(configuration.histogram_bins + 1)
        ]
        edges[-1] = maximum
        for value in ordered:
            bin_index = min(
                configuration.histogram_bins - 1,
                int(((value - minimum) / data_range) * configuration.histogram_bins),
            )
            histogram_counts[bin_index] += 1

    warnings = []
    if variance_overflow:
        warnings.append("Sample variance exceeds binary64 limits and is unavailable.")
    if not math.isfinite(data_range) or not all(math.isfinite(edge) for edge in edges):
        warnings.append("Histogram boundaries are unavailable because the numeric range exceeds binary64 limits.")
        histogram = None
    else:
        histogram = {
            "method": "equal_width",
            "closed": "left_inclusive_right_exclusive_last_bin_inclusive",
            "edges": [_json_number(edge) for edge in edges],
            "counts": histogram_counts,
        }
    box_plot = {
        "q1_linear": _json_number(q1),
        "median_linear": _json_number(quartiles["50.0"]),
        "q3_linear": _json_number(q3),
        "iqr_linear": _json_number(iqr),
        "whisker_method": "observed_extremes_within_1.5_iqr_fences",
        "lower_whisker": _json_number(inside_whiskers[0]) if inside_whiskers else None,
        "upper_whisker": _json_number(inside_whiskers[-1]) if inside_whiskers else None,
    }
    return {
        "count": count,
        "finite_count": count,
        "mean": _json_number(mean),
        "median": _json_number(quartiles["50.0"]),
        "minimum": _json_number(minimum),
        "maximum": _json_number(maximum),
        "range": _json_number(data_range),
        "sample_variance_ddof_1": _json_number(variance),
        "sample_standard_deviation_ddof_1": (
            _json_number(math.sqrt(variance)) if variance is not None else None
        ),
        "percentiles_linear": percentiles,
        "histogram": histogram,
        "box_plot": box_plot,
        "precision": "IEEE-754 binary64",
    }, warnings


def _numeric_pair_summary(left_values: list[float], right_values: list[float], left_name: str, right_name: str) -> dict[str, Any]:
    paired = [
        (left, right)
        for left, right in zip(left_values, right_values)
        if left is not None and right is not None and math.isfinite(left) and math.isfinite(right)
    ]
    valid_observations = len(paired)
    if valid_observations < 2:
        return {
            "valid_observations": valid_observations,
            "pair_type": "numeric_numeric",
            "pearson_correlation": None,
            "spearman_rank_correlation": None,
            "covariance": None,
            "method": "pairwise_valid_rows_only",
            "missing_value_policy": "Rows with nulls or non-finite values are excluded.",
            "undefined_reason": "Insufficient valid observations for a correlation result.",
            "warnings": ["The pair contains fewer than two valid observations after excluding nulls and non-finite values."],
        }
    x_values = [left for left, _ in paired]
    y_values = [right for _, right in paired]
    pearson = _pearson_corr(x_values, y_values)
    spearman = _spearman_corr(x_values, y_values)
    cov = _covariance(x_values, y_values)
    warnings = []
    if all(value == x_values[0] for value in x_values):
        warnings.append(f"{left_name} is constant; correlation is undefined.")
    if all(value == y_values[0] for value in y_values):
        warnings.append(f"{right_name} is constant; correlation is undefined.")
    if pearson is None or spearman is None:
        warnings.append("At least one variable has zero variance; the correlation is undefined.")
    if cov is None:
        warnings.append("Sample covariance exceeds binary64 limits and is unavailable.")
    return {
        "valid_observations": valid_observations,
        "pair_type": "numeric_numeric",
        "pearson_correlation": _json_number(pearson),
        "spearman_rank_correlation": _json_number(spearman),
        "covariance": _json_number(cov),
        "method": "pairwise_valid_rows_only",
        "missing_value_policy": "Rows with nulls or non-finite values are excluded before statistics are calculated.",
        "undefined_reason": None if pearson is not None else "Zero variance or an insufficient range of valid values.",
        "warnings": warnings,
    }


def _numeric_categorical_summary_from_groups(
    grouped: dict[str, list[float]],
    displays: dict[str, Any],
    numeric_name: str,
    category_name: str,
    max_categories: int,
) -> dict[str, Any]:
    ordered_keys = sorted(grouped)
    observed = []
    variance_unavailable = False
    for key in ordered_keys[:max_categories]:
        values_sorted = sorted(grouped[key])
        count = len(values_sorted)
        if count == 0:
            continue
        mean = _safe_mean(values_sorted)
        median = (
            values_sorted[count // 2]
            if count % 2
            else values_sorted[count // 2 - 1] / 2
            + values_sorted[count // 2] / 2
        )
        variance = _sample_variance(values_sorted) if count > 1 else 0.0
        variance_unavailable = variance_unavailable or (
            count > 1 and variance is None
        )
        standard_deviation = (
            math.sqrt(variance)
            if variance is not None and variance >= 0.0
            else None
        )
        observed.append(
            {
                "category": displays[key],
                "count": count,
                "mean": _json_number(mean),
                "median": _json_number(median),
                "minimum": _json_number(min(values_sorted)),
                "maximum": _json_number(max(values_sorted)),
                "sample_variance_ddof_1": _json_number(variance),
                "sample_standard_deviation_ddof_1": _json_number(standard_deviation),
            }
        )
    category_result = {
        "pair_type": "numeric_categorical",
        "left_column": numeric_name,
        "right_column": category_name,
        "method": "groupwise_numeric_summary_by_category",
        "missing_value_policy": "Rows with null or non-finite numeric values and missing category values are excluded.",
        "group_statistics": observed,
        "warnings": (
            []
            if len(ordered_keys) <= max_categories
            else [
                f"{category_name} has more than {max_categories} categories; additional categories were omitted."
            ]
        )
        + (
            ["At least one group sample variance exceeds binary64 limits and is unavailable."]
            if variance_unavailable
            else []
        ),
    }
    return category_result


def _contingency_summary_from_counts(
    left_categories: dict[str, Any],
    right_categories: dict[str, Any],
    cell_counts: dict[tuple[str, str], int],
    observed_total: int,
    left_name: str,
    right_name: str,
    max_categories: int,
) -> dict[str, Any]:
    left_labels = sorted(left_categories)[:max_categories]
    right_labels = sorted(right_categories)[:max_categories]
    selected_left = set(left_labels)
    selected_right = set(right_labels)
    selected_cells = {
        cell: count
        for cell, count in cell_counts.items()
        if cell[0] in selected_left and cell[1] in selected_right
    }
    row_totals = {}
    column_totals = {}
    for (left_key, right_key), count in selected_cells.items():
        row_totals[left_key] = row_totals.get(left_key, 0) + count
        column_totals[right_key] = column_totals.get(right_key, 0) + count

    left_labels = [key for key in left_labels if row_totals.get(key, 0) > 0]
    right_labels = [key for key in right_labels if column_totals.get(key, 0) > 0]
    selected_left = set(left_labels)
    selected_right = set(right_labels)
    row_totals = {key: row_totals[key] for key in left_labels}
    column_totals = {key: column_totals[key] for key in right_labels}
    represented_total = sum(row_totals.values())

    data = [
        {
            "left_category": left_categories[left_key],
            "counts": {
                right_key: cell_counts.get((left_key, right_key), 0)
                for right_key in right_labels
            },
        }
        for left_key in left_labels
    ]
    chi_square = 0.0
    if represented_total:
        for left_key in left_labels:
            for right_key in right_labels:
                observed = cell_counts.get((left_key, right_key), 0)
                expected = row_totals[left_key] * column_totals[right_key] / represented_total
                if expected > 0:
                    chi_square += ((observed - expected) ** 2) / expected

    cramers_v = None
    if represented_total and len(left_labels) > 1 and len(right_labels) > 1:
        denominator = represented_total * (min(len(left_labels), len(right_labels)) - 1)
        cramers_v = min(1.0, math.sqrt(chi_square / denominator)) if denominator else None
    warnings = []
    if len(left_categories) > max_categories or len(right_categories) > max_categories:
        warnings.append(
            f"Category cardinality exceeded the bounded contingency-table limit for {left_name} and {right_name}."
        )
    if not represented_total:
        warnings.append("No valid paired category observations remain after excluding missing values.")
    if represented_total and (len(left_labels) < 2 or len(right_labels) < 2):
        warnings.append("Cramer's V is undefined because fewer than two represented categories remain on an axis.")
    return {
        "pair_type": "categorical_categorical",
        "left_column": left_name,
        "right_column": right_name,
        "contingency_table": data,
        "total_observations": represented_total,
        "excluded_observations": observed_total - represented_total,
        "row_totals": row_totals,
        "column_totals": column_totals,
        "cramers_v": _json_number(cramers_v),
        "warnings": warnings,
    }


def _pair_selection_for_bivariate(configuration: EDAConfiguration) -> list[tuple[str, str]]:
    if configuration.column_pairs:
        pairs = list(configuration.column_pairs)
    else:
        columns = list(configuration.selected_columns)
        pairs = []
        for position, left in enumerate(columns):
            for right in columns[position + 1 :]:
                pairs.append((left, right))
                if len(pairs) >= configuration.max_pairs:
                    break
            if len(pairs) >= configuration.max_pairs:
                break
    return pairs


def _decoded_python_string_bytes(array: pa.Array) -> int:
    return estimate_decoded_python_string_bytes(array)


def _materialize_values(array: pa.Array, budget: ExecutionBudget) -> list[Any]:
    array_type = array.type
    if pa.types.is_dictionary(array_type):
        array_type = array_type.value_type
    if pa.types.is_string(array_type) or pa.types.is_large_string(array_type):
        estimated_bytes = _decoded_python_string_bytes(array)
        try:
            budget.consume("decoded_value_bytes", estimated_bytes)
        except ExecutionBudgetExceeded as error:
            raise EDAError(
                "decoded_value_size_limit",
                f"Decoded categorical values exceed the estimated Python bytes limit ({MAX_EDA_DECODED_VALUE_BYTES} bytes).",
            ) from error
    return _arrow_to_pylist(array)


def _arrow_to_pylist(array: pa.Array) -> list[Any]:
    return array.to_pylist()


def _relationship_analysis(
    parquet: pq.ParquetFile,
    schema: pa.Schema,
    configuration: EDAConfiguration,
    *,
    dataset_id: int,
    file_id: int,
    artifact_sha256: str | None,
    normalization_version: str | None,
    table_name: str | None,
) -> EDAResult:
    row_count = parquet.metadata.num_rows
    budget = ExecutionBudget(
        {
            "numeric_values": MAX_EDA_NUMERIC_VALUES,
            "categories": MAX_EDA_TRACKED_CATEGORIES,
            "grouped_values": MAX_EDA_GROUPED_VALUES,
            "contingency_cells": MAX_EDA_CONTINGENCY_CELLS,
            "pairwise_values": MAX_EDA_PAIRWISE_VALUES,
            "decoded_value_bytes": MAX_EDA_DECODED_VALUE_BYTES,
            "serialized_bytes": configuration.max_result_bytes,
        }
    )
    numeric_values: dict[str, dict[int, float]] = {}
    categories: dict[str, dict[str, Any]] = {}
    pair_definitions = []
    read_columns = []

    if configuration.analysis_type == "bivariate":
        eligible_pairs = (
            len(configuration.column_pairs)
            if configuration.column_pairs
            else len(configuration.selected_columns)
            * (len(configuration.selected_columns) - 1)
            // 2
        )
        pairs = (
            list(configuration.column_pairs)
            if configuration.column_pairs
            else _pair_selection_for_bivariate(configuration)
        )
        for left_name, right_name in pairs:
            left_kind = _arrow_kind(schema.field(left_name).type)
            right_kind = _arrow_kind(schema.field(right_name).type)
            if left_kind == right_kind == "numeric":
                pair_type = "numeric_numeric"
            elif (
                left_kind == "numeric"
                and right_kind in {"categorical", "boolean"}
            ) or (
                right_kind == "numeric"
                and left_kind in {"categorical", "boolean"}
            ):
                pair_type = "numeric_categorical"
            elif left_kind in {"categorical", "boolean"} and right_kind in {
                "categorical",
                "boolean",
            }:
                pair_type = "categorical_categorical"
            else:
                pair_type = "unsupported"
            pair_definitions.append((left_name, right_name, pair_type))
            if pair_type == "numeric_numeric":
                read_columns.extend((left_name, right_name))
            elif pair_type == "numeric_categorical":
                read_columns.extend((left_name, right_name))
            elif pair_type == "categorical_categorical":
                read_columns.extend((left_name, right_name))
    elif configuration.analysis_type == "multivariate":
        numeric_columns = [
            name
            for name in configuration.selected_columns
            if _arrow_kind(schema.field(name).type) == "numeric"
        ]
        features = numeric_columns[: configuration.max_features]
        features_truncated = len(features) < len(numeric_columns)
        if len(features) < 2:
            multivariate_warnings = []
            if features_truncated:
                multivariate_warnings.append(
                    f"Feature selection was limited to max_features={configuration.max_features}; "
                    f"{len(numeric_columns) - len(features)} eligible numeric features were omitted."
                )
            return _make_relationship_result(
                configuration,
                dataset_id,
                file_id,
                artifact_sha256,
                normalization_version,
                table_name,
                row_count,
                tuple(features),
                "partially_completed",
                {
                    "selected_features": list(features),
                    "numeric_features": list(features),
                    "feature_count": len(features),
                    "eligible_features": len(numeric_columns),
                    "features_truncated": features_truncated,
                    "correlation_matrix": {},
                    "pairwise_valid_counts": {},
                    "warnings": multivariate_warnings,
                },
                warnings=tuple(
                    ["Multivariate analysis requires at least two numeric columns."]
                    + multivariate_warnings
                ),
            )
        read_columns.extend(features)
    else:
        raise EDAError("unsupported_analysis_type", "EDA analysis type is unsupported for this table.")

    read_columns = list(dict.fromkeys(read_columns))
    for name in read_columns:
        kind = _arrow_kind(schema.field(name).type)
        if kind == "numeric":
            numeric_values[name] = {}
        elif kind in {"categorical", "boolean"}:
            categories[name] = {}

    grouped_values: dict[tuple[str, str], dict[str, list[float]]] = {}
    grouped_displays: dict[tuple[str, str], dict[str, Any]] = {}
    grouped_valid: dict[tuple[str, str], int] = {}
    contingency_cells: dict[tuple[str, str], dict[tuple[str, str], int]] = {}
    contingency_valid: dict[tuple[str, str], int] = {}
    for left_name, right_name, pair_type in pair_definitions:
        pair = (left_name, right_name)
        if pair_type == "numeric_categorical":
            grouped_values[pair] = {}
            grouped_displays[pair] = {}
            grouped_valid[pair] = 0
        elif pair_type == "categorical_categorical":
            contingency_cells[pair] = {}
            contingency_valid[pair] = 0

    try:
        if read_columns:
            _validate_scan_size(parquet, read_columns)
            scanned_rows = 0
            for batch in parquet.iter_batches(
                batch_size=EDA_BATCH_SIZE,
                columns=read_columns,
                use_threads=False,
            ):
                batch_start = scanned_rows
                scanned_rows += batch.num_rows
                if scanned_rows > MAX_EDA_ROWS:
                    raise EDAError(
                        "row_limit",
                        f"Exact EDA is limited to {MAX_EDA_ROWS} rows per table.",
                    )
                batch_category_ids: dict[str, list[str | None]] = {}
                for index, name in enumerate(read_columns):
                    kind = _arrow_kind(schema.field(name).type)
                    values = _materialize_values(batch.column(index), budget)
                    if kind == "numeric":
                        stored = numeric_values[name]
                        for offset, value in enumerate(values):
                            number = _to_finite_float(value)
                            if number is None:
                                continue
                            _consume_relationship_budget(budget, "numeric_values")
                            stored[batch_start + offset] = number
                    elif kind in {"categorical", "boolean"}:
                        known_categories = categories[name]
                        category_ids = []
                        for value in values:
                            if value is None:
                                category_ids.append(None)
                                continue
                            identity, display = _category_identity_and_display(value)
                            if identity not in known_categories:
                                _consume_relationship_budget(budget, "categories")
                                known_categories[identity] = display
                            category_ids.append(identity)
                        batch_category_ids[name] = category_ids
                    del values

                for left_name, right_name, pair_type in pair_definitions:
                    pair = (left_name, right_name)
                    if pair_type == "numeric_categorical":
                        numeric_name, category_name = (
                            (left_name, right_name)
                            if _arrow_kind(schema.field(left_name).type) == "numeric"
                            else (right_name, left_name)
                        )
                        for offset, category_id in enumerate(
                            batch_category_ids[category_name]
                        ):
                            _consume_relationship_budget(budget, "pairwise_values")
                            number = numeric_values[numeric_name].get(batch_start + offset)
                            if number is None or category_id is None:
                                continue
                            _consume_relationship_budget(budget, "grouped_values")
                            grouped_valid[pair] += 1
                            grouped_values[pair].setdefault(category_id, []).append(number)
                            grouped_displays[pair][category_id] = categories[category_name][category_id]
                    elif pair_type == "categorical_categorical":
                        left_ids = batch_category_ids[left_name]
                        right_ids = batch_category_ids[right_name]
                        for left_id, right_id in zip(left_ids, right_ids):
                            _consume_relationship_budget(budget, "pairwise_values")
                            if left_id is None or right_id is None:
                                continue
                            cell = (left_id, right_id)
                            if cell not in contingency_cells[pair]:
                                _consume_relationship_budget(budget, "contingency_cells")
                                contingency_cells[pair][cell] = 0
                            contingency_cells[pair][cell] += 1
                            contingency_valid[pair] += 1
            if scanned_rows != row_count:
                raise EDAError(
                    "artifact_metadata_mismatch",
                    "Parquet scan row count did not match normalization metadata.",
                )

        if configuration.analysis_type == "bivariate":
            pair_results = []
            for left_name, right_name, pair_type in pair_definitions:
                pair = (left_name, right_name)
                if pair_type == "numeric_numeric":
                    paired_left, paired_right = _intersect_numeric_values(
                        numeric_values[left_name],
                        numeric_values[right_name],
                        budget,
                    )
                    summary = _numeric_pair_summary(
                        paired_left,
                        paired_right,
                        left_name,
                        right_name,
                    )
                    valid_observations = summary["valid_observations"]
                elif pair_type == "numeric_categorical":
                    numeric_name, category_name = (
                        (left_name, right_name)
                        if _arrow_kind(schema.field(left_name).type) == "numeric"
                        else (right_name, left_name)
                    )
                    summary = _numeric_categorical_summary_from_groups(
                        grouped_values[pair],
                        grouped_displays[pair],
                        numeric_name,
                        category_name,
                        configuration.max_categories,
                    )
                    summary["left_column"] = left_name
                    summary["right_column"] = right_name
                    summary["numeric_column"] = numeric_name
                    summary["category_column"] = category_name
                    valid_observations = grouped_valid[pair]
                elif pair_type == "categorical_categorical":
                    summary = _contingency_summary_from_counts(
                        categories[left_name],
                        categories[right_name],
                        contingency_cells[pair],
                        contingency_valid[pair],
                        left_name,
                        right_name,
                        configuration.max_categories,
                    )
                    valid_observations = summary["total_observations"]
                else:
                    summary = {
                        "method": "unsupported_type_combination",
                        "message": "This pair type is not supported for bivariate analysis.",
                    }
                    valid_observations = 0
                warnings = tuple(summary.get("warnings", ()))
                pair_result = EDAColumnPairResult(
                    left_column=left_name,
                    right_column=right_name,
                    pair_type=pair_type,
                    valid_observations=valid_observations,
                    statistics=summary,
                    warnings=warnings
                    or (("Unsupported type combination for this pair.",) if pair_type == "unsupported" else ()),
                )
                _consume_relationship_budget(
                    budget,
                    "serialized_bytes",
                    len(_canonical_json(pair_result.as_dict())),
                )
                pair_results.append(pair_result)
            analyzed = tuple(
                dict.fromkeys(name for pair in pairs for name in pair)
            )
            summary = {
                "pair_count": len(pairs),
                "analyzed_pairs": len(pair_results),
                "eligible_pairs": eligible_pairs,
                "pairs_truncated": len(pairs) < eligible_pairs,
                "pair_selection_mode": (
                    "explicit" if configuration.column_pairs else "all_selected_columns"
                ),
                "warnings": (
                    [
                        f"Automatic pair selection was limited to max_pairs={configuration.max_pairs}; "
                        f"{eligible_pairs - len(pairs)} eligible pairs were omitted."
                    ]
                    if len(pairs) < eligible_pairs
                    else []
                ),
            }
            result_status = (
                "completed"
                if all(pair.pair_type != "unsupported" for pair in pair_results)
                else "partially_completed"
            )
            result = _make_relationship_result(
                configuration,
                dataset_id,
                file_id,
                artifact_sha256,
                normalization_version,
                table_name,
                row_count,
                analyzed,
                result_status,
                summary,
                pair_results=tuple(pair_results),
            )
        else:
            features = [name for name in read_columns]
            matrix = {name: {} for name in features}
            counts = {name: {} for name in features}
            for index, left_name in enumerate(features):
                for right_name in features[index:]:
                    left_series, right_series = _intersect_numeric_values(
                        numeric_values[left_name],
                        numeric_values[right_name],
                        budget,
                    )
                    coefficient = _pearson_corr(left_series, right_series)
                    count = len(left_series)
                    matrix[left_name][right_name] = _json_number(coefficient)
                    counts[left_name][right_name] = count
                    if left_name != right_name:
                        matrix[right_name][left_name] = matrix[left_name][right_name]
                        counts[right_name][left_name] = count
            summary = {
                "selected_features": features,
                "numeric_features": features,
                "feature_count": len(features),
                "eligible_features": len(numeric_columns),
                "features_truncated": features_truncated,
                "rows_analyzed": row_count,
                "missing_value_policy": "Rows with nulls or non-finite values are excluded per pair.",
                "correlation_matrix": matrix,
                "pairwise_valid_counts": counts,
                "warnings": (
                    [
                        f"Feature selection was limited to max_features={configuration.max_features}; "
                        f"{len(numeric_columns) - len(features)} eligible numeric features were omitted."
                    ]
                    if features_truncated
                    else []
                ),
            }
            result = _make_relationship_result(
                configuration,
                dataset_id,
                file_id,
                artifact_sha256,
                normalization_version,
                table_name,
                row_count,
                tuple(features),
                "completed",
                summary,
            )

        encoded_result = _canonical_json(result.as_dict())
        if len(encoded_result) > configuration.max_result_bytes:
            raise EDAError(
                "result_size_limit",
                "EDA result exceeds the configured serialized size limit.",
            )
        return result
    except ExecutionBudgetExceeded as error:
        codes = {
            "numeric_values": (
                "numeric_value_limit",
                f"Exact numeric EDA is limited to {MAX_EDA_NUMERIC_VALUES} finite values per request.",
            ),
            "categories": (
                "category_tracking_limit",
                f"Exact EDA is limited to {MAX_EDA_TRACKED_CATEGORIES} distinct category entries per request.",
            ),
            "grouped_values": (
                "grouped_value_limit",
                f"Numeric-categorical EDA is limited to {MAX_EDA_GROUPED_VALUES} grouped values per request.",
            ),
            "contingency_cells": (
                "contingency_cell_limit",
                f"Categorical EDA is limited to {MAX_EDA_CONTINGENCY_CELLS} non-empty contingency cells per request.",
            ),
            "pairwise_values": (
                "pairwise_work_limit",
                f"Relationship analysis is limited to {MAX_EDA_PAIRWISE_VALUES} pairwise values per request.",
            ),
            "decoded_value_bytes": (
                "decoded_value_size_limit",
                f"Decoded categorical values are limited to {MAX_EDA_DECODED_VALUE_BYTES} estimated Python bytes per request.",
            ),
            "serialized_bytes": (
                "result_size_limit",
                "EDA result exceeds the configured serialized size limit.",
            ),
        }
        code, message = codes[error.resource]
        raise EDAError(code, message) from error


def _consume_relationship_budget(
    budget: ExecutionBudget,
    resource: str,
    amount: int = 1,
) -> None:
    budget.consume(resource, amount)


def _intersect_numeric_values(
    left_values: dict[int, float],
    right_values: dict[int, float],
    budget: ExecutionBudget,
) -> tuple[list[float], list[float]]:
    if len(left_values) <= len(right_values):
        candidates = left_values.items()
        other_values = right_values
        left_is_candidate = True
    else:
        candidates = right_values.items()
        other_values = left_values
        left_is_candidate = False
    paired_left = []
    paired_right = []
    for row_index, candidate_value in candidates:
        _consume_relationship_budget(budget, "pairwise_values")
        other_value = other_values.get(row_index)
        if other_value is None:
            continue
        if left_is_candidate:
            paired_left.append(candidate_value)
            paired_right.append(other_value)
        else:
            paired_left.append(other_value)
            paired_right.append(candidate_value)
    return paired_left, paired_right


def _make_relationship_result(
    configuration: EDAConfiguration,
    dataset_id: int,
    file_id: int,
    artifact_sha256: str | None,
    normalization_version: str | None,
    table_name: str | None,
    row_count: int,
    analyzed_columns: tuple[str, ...],
    status: str,
    summary: dict[str, Any],
    *,
    pair_results: tuple[EDAColumnPairResult, ...] = (),
    warnings: tuple[str, ...] = (),
) -> EDAResult:
    encoded_configuration = _canonical_json(configuration.as_dict())
    return EDAResult(
        schema_version=EDA_RESULT_SCHEMA_VERSION,
        analysis_type=configuration.analysis_type,
        status=status,
        dataset_id=dataset_id,
        file_id=file_id,
        table_index=configuration.table_index,
        table_name=table_name,
        artifact_sha256=artifact_sha256,
        normalization_version=normalization_version,
        algorithm_version=_algorithm_version_for_analysis(configuration.analysis_type),
        configuration=configuration.as_dict(),
        configuration_sha256=hashlib.sha256(encoded_configuration).hexdigest(),
        executed_at=datetime.now(timezone.utc).isoformat(),
        requested_columns=configuration.selected_columns,
        analyzed_columns=analyzed_columns,
        rows_analyzed=row_count,
        exact=True,
        sampling=None,
        columns=(),
        warnings=warnings,
        pair_results=pair_results,
        multivariate_summary=summary,
    )


def _arrow_kind(data_type: pa.DataType) -> str:
    if pa.types.is_dictionary(data_type):
        data_type = data_type.value_type
    if (
        pa.types.is_integer(data_type)
        or pa.types.is_floating(data_type)
        or pa.types.is_decimal(data_type)
    ):
        return "numeric"
    if pa.types.is_boolean(data_type):
        return "boolean"
    if pa.types.is_string(data_type) or pa.types.is_large_string(data_type):
        return "categorical"
    if pa.types.is_date32(data_type) or pa.types.is_date64(data_type):
        return "date"
    if pa.types.is_timestamp(data_type):
        return "timestamp"
    return "unsupported"


def _make_accumulator(field_value: pa.Field) -> _ColumnAccumulator:
    return _ColumnAccumulator(field=field_value)


def _validate_scan_size(parquet: pq.ParquetFile, selected_columns: list[str]) -> None:
    if not selected_columns or parquet.metadata.num_row_groups == 0:
        return

    selected = set(selected_columns)
    scanned_bytes = 0
    for row_group_index in range(parquet.metadata.num_row_groups):
        row_group = parquet.metadata.row_group(row_group_index)
        matched_columns = {name: 0 for name in selected}
        for column_index in range(row_group.num_columns):
            column = row_group.column(column_index)
            if column.path_in_schema in selected:
                matched_columns[column.path_in_schema] += 1
                scanned_bytes += column.total_uncompressed_size
        if any(match_count != 1 for match_count in matched_columns.values()):
            raise EDAError(
                "artifact_metadata_mismatch",
                "Selected Parquet column metadata does not match its schema.",
            )
        if scanned_bytes > MAX_EDA_SCAN_BYTES:
            raise EDAError(
                "scan_size_limit",
                "Selected Parquet columns exceed the EDA uncompressed scan-size limit.",
            )


def _observe_value(
    accumulator: _ColumnAccumulator,
    value: Any,
    kind: str,
    numeric_budget: list[int],
    category_budget: list[int],
) -> None:
    if value is None:
        accumulator.null_count += 1
        return
    if kind == "numeric":
        if isinstance(value, bool):
            raise EDAError("invalid_parquet_value", "Numeric column contains a boolean value.")
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            raise EDAError("numeric_value_unsupported", "Numeric values exceed supported binary64 representation.") from None
        if not math.isfinite(number):
            accumulator.non_finite_count += 1
            return
        if numeric_budget[0] <= 0:
            raise EDAError(
                "numeric_value_limit",
                f"Exact numeric EDA is limited to {MAX_EDA_NUMERIC_VALUES} finite values per request.",
            )
        numeric_budget[0] -= 1
        accumulator.values.append(number)
        return
    if kind in {"categorical", "boolean"}:
        if kind == "categorical" and value == "":
            accumulator.empty_count += 1
        if kind == "categorical" and len(value) > _MAX_DISPLAY_VALUE_LENGTH:
            accumulator.categories_truncated = True
            return
        key = _category_key(value)
        entry = accumulator.categories.get(key)
        if entry is not None:
            entry[1] += 1
        elif category_budget[0] > 0:
            accumulator.categories[key] = [_display_category(value), 1]
            category_budget[0] -= 1
        else:
            accumulator.categories_truncated = True
        return
    if kind in {"date", "timestamp"}:
        if not isinstance(value, (date, datetime)):
            raise EDAError("invalid_parquet_value", "Temporal column has an unsupported value.")
        if accumulator.minimum is None or value < accumulator.minimum:
            accumulator.minimum = value
        if accumulator.maximum is None or value > accumulator.maximum:
            accumulator.maximum = value


def _compatible_profile(
    profile: dict | None,
    table_index: int,
    table: dict,
    schema: pa.Schema,
    null_counts: list[int],
    empty_counts: list[int],
    selected_indices: list[int],
) -> tuple[bool, list[str]]:
    if profile is None:
        return False, []
    try:
        profile_tables = profile["tables"]
        profile_table = profile_tables[table_index]
        profile_columns = profile_table["columns"]
        is_compatible = (
            profile.get("version") == 1
            and profile_table.get("row_count") == table["row_count"]
            and profile_table.get("column_count") == len(schema)
            and profile_table.get("name") == table.get("table_name")
            and len(profile_columns) == len(schema)
        )
        for index in selected_indices:
            column = profile_columns[index]
            parquet_field = schema.field(index)
            is_compatible = is_compatible and (
                column.get("name") == parquet_field.name
                and column.get("position") == index
                and column.get("missing_value_count") == null_counts[index]
                and column.get("empty_value_count") == empty_counts[index]
            )
        if is_compatible:
            return True, []
    except (IndexError, KeyError, TypeError):
        pass
    return False, ["Stored profile was incompatible with the verified artifact and was not reused."]


def _verified_quality_findings(
    report: dict | None,
    source_sha256: str,
    normalization_result: dict,
) -> tuple[list[dict], list[str]]:
    if report is None:
        return [], []
    try:
        expected_key, _ = quality_analysis_key(
            source_sha256,
            normalization_result,
            QUALITY_ANALYSIS_CONFIGURATION,
        )
        validated = validate_quality_report(
            report,
            expected_summary_totals=normalized_summary_totals(normalization_result),
            expected_source_sha256=source_sha256,
            expected_analysis_key=expected_key,
            expected_normalized_outputs=normalized_output_provenance(
                normalization_result
            ),
        )
        return validated["findings"], []
    except (QualityAnalysisError, KeyError, TypeError, ValueError):
        return [], ["Stored quality findings were stale or incompatible and were not reused."]


def _failure_summary(configuration: EDAConfiguration) -> dict[str, Any]:
    if configuration.analysis_type == "bivariate":
        eligible_pairs = (
            len(configuration.column_pairs)
            if configuration.column_pairs
            else len(configuration.selected_columns)
            * (len(configuration.selected_columns) - 1)
            // 2
        )
        selected_pairs = min(eligible_pairs, configuration.max_pairs)
        pairs_truncated = selected_pairs < eligible_pairs
        warnings = (
            [
                f"Automatic pair selection was limited to max_pairs={configuration.max_pairs}; "
                f"{eligible_pairs - selected_pairs} eligible pairs were omitted."
            ]
            if pairs_truncated and not configuration.column_pairs
            else []
        )
        return {
            "pair_count": selected_pairs,
            "analyzed_pairs": 0,
            "eligible_pairs": eligible_pairs,
            "pairs_truncated": pairs_truncated,
            "pair_selection_mode": (
                "explicit" if configuration.column_pairs else "all_selected_columns"
            ),
            "warnings": warnings,
        }
    if configuration.analysis_type == "multivariate":
        return {
            "selected_features": None,
            "numeric_features": None,
            "feature_count": None,
            "eligible_features": None,
            "features_truncated": None,
            "warnings": [],
        }
    return {}


def _eda_provenance(
    *,
    dataset_id: int,
    file_id: int,
    source_sha256: str | None,
    source_size_bytes: int | None,
    normalization_result: dict,
    table: dict,
    schema: pa.Schema | None,
    configuration: EDAConfiguration,
    result_status: str,
    verification_status: str,
) -> dict[str, Any]:
    inputs = [
        {
            "kind": "source",
            "dataset_id": dataset_id,
            "file_id": file_id,
            "sha256": source_sha256,
            "size_bytes": source_size_bytes,
        },
        {
            "kind": "normalized_parquet",
            "dataset_id": dataset_id,
            "file_id": file_id,
            "table_index": configuration.table_index,
            "object_key": table.get("object_key"),
            "sha256": table.get("sha256"),
            "size_bytes": table.get("file_size_bytes"),
            "schema_sha256": (
                schema_fingerprint(schema)
                if schema is not None
                else None
            ),
        },
    ]
    parents = []
    normalization_provenance = normalization_result.get("provenance")
    if isinstance(normalization_provenance, dict):
        try:
            validate_provenance(normalization_provenance)
        except (KeyError, TypeError, ValueError):
            pass
        else:
            parents.append(normalization_provenance["operation_id"])
    configuration_sha256 = hashlib.sha256(
        _canonical_json(configuration.as_dict())
    ).hexdigest()
    algorithm_version = _algorithm_version_for_analysis(
        configuration.analysis_type
    )
    operation_status = (
        "completed" if result_status == "completed" else
        "partial" if result_status == "partially_completed" else
        "failed"
    )
    return build_provenance(
        operation_type=f"eda:{configuration.analysis_type}",
        operation_version=algorithm_version,
        configuration_sha256=configuration_sha256,
        status=operation_status,
        inputs=inputs,
        outputs=(
            [{
                "kind": "eda_result",
                "analysis_type": configuration.analysis_type,
                "algorithm_version": algorithm_version,
                "configuration_sha256": configuration_sha256,
            }]
            if operation_status != "failed"
            else []
        ),
        verification_status=verification_status,
        checks=[
            {"name": "normalized_artifact_checksum", "status": verification_status},
            {"name": "normalized_schema", "status": verification_status},
            {"name": "normalized_row_count", "status": verification_status},
        ],
        limitations=[
            "Artifact checksum and schema verification do not establish business-semantic correctness."
        ],
        parent_operation_ids=parents,
    )


def _failure_result(
    *,
    dataset_id: int,
    file_id: int,
    configuration: EDAConfiguration,
    artifact_sha256: str | None,
    normalization_version: str | None,
    table_name: str | None,
    source_sha256: str | None = None,
    source_size_bytes: int | None = None,
    normalization_result: dict | None = None,
    code: str,
    message: str,
) -> EDAResult:
    encoded = _canonical_json(configuration.as_dict())
    normalized = normalization_result or {}
    provenance_table = {
        "object_key": None,
        "sha256": artifact_sha256,
        "file_size_bytes": None,
    }
    provenance = _eda_provenance(
        dataset_id=dataset_id,
        file_id=file_id,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        normalization_result=normalized,
        table=provenance_table,
        schema=None,
        configuration=configuration,
        result_status="failed",
        verification_status="unverifiable",
    )
    return EDAResult(
        schema_version=EDA_RESULT_SCHEMA_VERSION,
        analysis_type=configuration.analysis_type,
        status="failed",
        dataset_id=dataset_id,
        file_id=file_id,
        table_index=configuration.table_index,
        table_name=table_name,
        artifact_sha256=artifact_sha256,
        normalization_version=normalization_version,
        algorithm_version=_algorithm_version_for_analysis(configuration.analysis_type),
        configuration=configuration.as_dict(),
        configuration_sha256=hashlib.sha256(encoded).hexdigest(),
        executed_at=datetime.now(timezone.utc).isoformat(),
        requested_columns=configuration.selected_columns,
        analyzed_columns=(),
        rows_analyzed=0,
        exact=False,
        sampling=None,
        columns=(),
        failure=EDAFailure(code, message),
        multivariate_summary=_failure_summary(configuration),
        provenance=provenance,
    )


def analyze_normalized_file(
    *,
    storage,
    processed_bucket: str,
    dataset_id: int,
    file_id: int,
    file_status: str,
    normalization_status: str,
    source_filename: str,
    source_size_bytes: int,
    source_sha256: str,
    parsed_result: dict,
    normalization_result: dict,
    configuration: EDAConfiguration,
    profile_result: dict | None = None,
    quality_report: dict | None = None,
) -> EDAResult:
    """Analyze one verified normalized Parquet table without accepting local paths."""
    if not isinstance(configuration, EDAConfiguration):
        raise EDAError("invalid_configuration", "EDA configuration is invalid.")
    _validate_configuration(configuration)
    if file_status != "Ready" or normalization_status != "Ready":
        raise EDAError(
            "normalization_not_ready",
            "EDA requires a successfully ingested file with ready normalization.",
        )
    if (
        isinstance(dataset_id, bool)
        or not isinstance(dataset_id, int)
        or dataset_id <= 0
        or isinstance(file_id, bool)
        or not isinstance(file_id, int)
        or file_id <= 0
    ):
        raise EDAError("invalid_source_identity", "EDA source identity is invalid.")
    if not isinstance(processed_bucket, str) or not _BUCKET_NAME.fullmatch(processed_bucket):
        raise EDAError("invalid_storage_boundary", "EDA processed-storage bucket is invalid.")
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise EDAError("invalid_source_provenance", "EDA source checksum metadata is invalid.")
    if not isinstance(source_size_bytes, int) or isinstance(source_size_bytes, bool) or source_size_bytes <= 0:
        raise EDAError("invalid_source_provenance", "EDA source size metadata is invalid.")
    try:
        normalized = validate_normalization_result(
            normalization_result,
            parsed_result,
            source_filename,
            source_size_bytes,
            source_sha256,
            dataset_id,
            file_id,
        )
    except NormalizationError as error:
        raise EDAError(
            "normalization_metadata_invalid",
            "Stored normalization metadata failed validation.",
        ) from error
    if normalized.get("status") != "Ready" or normalized.get("validation_status") != "passed":
        raise EDAError("normalization_not_ready", "Normalized artifact is not validated and ready.")
    if normalized.get("output_size_bytes", 0) > DEFAULT_MAX_OUTPUT_BYTES:
        raise EDAError(
            "artifact_size_limit",
            "Normalized output exceeds the EDA artifact-size limit.",
        )
    tables = normalized["tables"]
    if configuration.table_index >= len(tables):
        raise EDAError("unknown_table", "Selected normalized table does not exist.")
    table = tables[configuration.table_index]
    if table.get("object_key") is None:
        raise EDAError("artifact_unavailable", "Selected normalized table has no Parquet artifact.")
    if table.get("file_size_bytes", 0) > DEFAULT_MAX_OUTPUT_BYTES:
        raise EDAError("artifact_size_limit", "Selected Parquet artifact exceeds the EDA size limit.")
    if table.get("row_count", 0) > MAX_EDA_ROWS:
        raise EDAError(
            "row_limit",
            f"Exact EDA is limited to {MAX_EDA_ROWS} rows per table.",
        )
    object_key = table["object_key"]
    expected_prefix = (
        f"{dataset_id}/{file_id}/{source_sha256}/"
        f"{normalized['configuration']['configuration_sha256']}/"
        f"table-{configuration.table_index:04d}-"
    )
    if not object_key.startswith(expected_prefix) or not object_key.endswith(".parquet"):
        raise EDAError("invalid_storage_boundary", "Normalized artifact key is outside its managed prefix.")

    temp_path = None
    response = None
    try:
        response = storage.get_object(processed_bucket, object_key)
        digest = hashlib.sha256()
        size = 0
        with tempfile.NamedTemporaryFile(
            prefix="insightos-eda-",
            suffix=".parquet",
            delete=False,
        ) as output:
            temp_path = Path(output.name)
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > table["file_size_bytes"] or size > DEFAULT_MAX_OUTPUT_BYTES:
                    raise EDAError(
                        "artifact_size_mismatch",
                        "Stored normalized artifact exceeded its verified size.",
                    )
                digest.update(chunk)
                output.write(chunk)
            output.flush()
        if size != table["file_size_bytes"] or digest.hexdigest() != table["sha256"]:
            raise EDAError(
                "artifact_integrity_failed",
                "Stored normalized artifact failed size or checksum verification.",
            )
        return _analyze_local_parquet(
            temp_path,
            normalized,
            table,
            dataset_id,
            file_id,
            source_sha256,
            configuration,
            profile_result,
            quality_report,
        )
    except EDAError:
        raise
    except (
        KeyError,
        MinioException,
        S3Error,
        HTTPError,
        OSError,
        ValueError,
        pa.ArrowException,
    ) as error:
        return _failure_result(
            dataset_id=dataset_id,
            file_id=file_id,
            configuration=configuration,
            artifact_sha256=table.get("sha256"),
            normalization_version=normalized.get("pipeline_version"),
            table_name=table.get("table_name"),
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
            normalization_result=normalized,
            code="artifact_analysis_failed",
            message=f"EDA could not safely analyze the normalized artifact ({type(error).__name__}).",
        )
    finally:
        try:
            if response is not None:
                try:
                    response.close()
                finally:
                    release = getattr(response, "release_conn", None)
                    if release is not None:
                        release()
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass


def _analyze_local_parquet(
    path: Path,
    normalization_result: dict,
    table: dict,
    dataset_id: int,
    file_id: int,
    source_sha256: str,
    configuration: EDAConfiguration,
    profile_result: dict | None,
    quality_report: dict | None,
) -> EDAResult:
    parquet = None
    try:
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        if (
            schema.names != [field["name"] for field in table["schema"]]
            or [
                {"name": field.name, "type": str(field.type)}
                for field in schema
            ]
            != table["schema"]
            or parquet.metadata.num_rows != table["row_count"]
        ):
            raise EDAError(
                "artifact_metadata_mismatch",
                "Parquet schema or row count does not match validated normalization metadata.",
            )
        column_indices = []
        for name in configuration.selected_columns:
            try:
                column_indices.append(schema.get_field_index(name))
            except (KeyError, ValueError):
                column_indices.append(-1)
            if column_indices[-1] < 0:
                raise EDAError("unknown_column", f"Selected column {name!r} is not in the normalized schema.")

        if configuration.analysis_type != "univariate":
            if configuration.analysis_type == "bivariate" and configuration.column_pairs:
                requested_for_relationship = list(dict.fromkeys(
                    name for pair in configuration.column_pairs for name in pair
                ))
                if any(name not in schema.names for name in requested_for_relationship):
                    raise EDAError(
                        "unknown_column",
                        "Selected pair contains a column that is not present in the table.",
                    )
            else:
                requested_for_relationship = list(configuration.selected_columns)
            if not requested_for_relationship:
                raise EDAError("unknown_column", "Selected relationship columns are not in the normalized schema.")
            if parquet.metadata.num_rows > MAX_EDA_ROWS:
                raise EDAError(
                    "row_limit",
                    f"Exact EDA is limited to {MAX_EDA_ROWS} rows per table.",
                )
            relationship_result = _relationship_analysis(
                parquet,
                schema,
                configuration,
                dataset_id=dataset_id,
                file_id=file_id,
                artifact_sha256=table["sha256"],
                normalization_version=normalization_result["pipeline_version"],
                table_name=table.get("table_name"),
            )
            relationship_result = replace(
                relationship_result,
                provenance=_eda_provenance(
                    dataset_id=dataset_id,
                    file_id=file_id,
                    source_sha256=source_sha256,
                    source_size_bytes=normalization_result["source"][
                        "file_size_bytes"
                    ],
                    normalization_result=normalization_result,
                    table=table,
                    schema=schema,
                    configuration=configuration,
                    result_status=relationship_result.status,
                    verification_status="verified",
                ),
            )
            if len(_canonical_json(relationship_result.as_dict())) > configuration.max_result_bytes:
                raise EDAError("result_size_limit", "EDA result exceeds the configured serialized size limit.")
            return relationship_result

        selected_fields = [schema.field(index) for index in column_indices]
        kinds = [_arrow_kind(field_value.type) for field_value in selected_fields]
        accumulators = [
            _make_accumulator(field_value)
            for field_value, kind in zip(selected_fields, kinds)
            if kind != "unsupported"
        ]
        supported_positions = [
            index for index, kind in enumerate(kinds) if kind != "unsupported"
        ]
        accumulator_by_position = dict(zip(supported_positions, accumulators))
        numeric_budget = [MAX_EDA_NUMERIC_VALUES]
        category_budget = [MAX_EDA_TRACKED_CATEGORIES]
        decoded_value_budget = ExecutionBudget(
            {"decoded_value_bytes": MAX_EDA_DECODED_VALUE_BYTES}
        )
        scanned_rows = 0
        if supported_positions:
            _validate_scan_size(
                parquet,
                [
                    configuration.selected_columns[index]
                    for index in supported_positions
                ],
            )
            for batch in parquet.iter_batches(
                batch_size=EDA_BATCH_SIZE,
                columns=[configuration.selected_columns[index] for index in supported_positions],
                use_threads=False,
            ):
                scanned_rows += batch.num_rows
                if scanned_rows > MAX_EDA_ROWS:
                    raise EDAError("row_limit", f"Exact EDA is limited to {MAX_EDA_ROWS} rows per table.")
                for selected_position, accumulator in accumulator_by_position.items():
                    kind = kinds[selected_position]
                    array = batch.column(supported_positions.index(selected_position))
                    values = _materialize_values(array, decoded_value_budget)
                    for value in values:
                        _observe_value(
                            accumulator,
                            value,
                            kind,
                            numeric_budget,
                            category_budget,
                        )
                    del values
            if scanned_rows != table["row_count"]:
                raise EDAError("artifact_metadata_mismatch", "Parquet scan row count did not match normalization metadata.")

        null_counts = [0] * len(schema)
        empty_counts = [0] * len(schema)
        for position, accumulator in accumulator_by_position.items():
            null_counts[column_indices[position]] = accumulator.null_count
            empty_counts[column_indices[position]] = accumulator.empty_count
        compatible_profile, profile_warnings = _compatible_profile(
            profile_result,
            configuration.table_index,
            table,
            schema,
            null_counts,
            empty_counts,
            [column_indices[index] for index in supported_positions],
        )
        if profile_result is not None and not compatible_profile and not profile_warnings:
            profile_warnings.append("Stored profile did not match the verified artifact and was not reused.")
        verified_findings, quality_warnings = _verified_quality_findings(
            quality_report,
            source_sha256,
            normalization_result,
        )

        column_results = []
        analyzed = []
        global_warnings = profile_warnings + quality_warnings
        for index, (name, field_value, kind) in enumerate(
            zip(configuration.selected_columns, selected_fields, kinds)
        ):
            if kind == "unsupported":
                column_results.append(
                    EDAColumnResult(
                        name=name,
                        data_type=str(field_value.type),
                        status="unsupported",
                        total_rows=table["row_count"],
                        non_null_count=None,
                        null_count=None,
                        null_percentage=None,
                        warnings=("This Arrow type is not supported for univariate EDA.",),
                    )
                )
                continue
            analyzed.append(name)
            accumulator = accumulator_by_position[index]
            non_null = table["row_count"] - accumulator.null_count
            statistics: dict[str, Any]
            warnings = []
            if kind == "numeric":
                statistics, numeric_warnings = _numeric_statistics(
                    accumulator.values,
                    configuration,
                )
                warnings.extend(numeric_warnings)
                statistics["non_finite_count"] = accumulator.non_finite_count
                if accumulator.non_finite_count:
                    warnings.append("NaN and infinity values are excluded from numeric statistics and counted separately.")
            elif kind in {"categorical", "boolean"}:
                ordered_categories = sorted(
                    accumulator.categories.values(),
                    key=lambda item: (-item[1], str(item[0])),
                )
                returned = ordered_categories[: configuration.max_categories]
                statistics = {
                    "distinct_value_count": (
                        len(accumulator.categories)
                        if not accumulator.categories_truncated
                        else None
                    ),
                    "distinct_count_exact": not accumulator.categories_truncated,
                    "frequencies": [
                        {
                            "value": value,
                            "count": count,
                            "percentage_of_non_null": (
                                count / non_null * 100 if non_null else None
                            ),
                        }
                        for value, count in returned
                    ],
                    "frequency_selection": (
                        "first_seen_bounded_exact_counts"
                        if accumulator.categories_truncated
                        else "all_categories_sorted_by_count"
                    ),
                    "frequencies_truncated": (
                        accumulator.categories_truncated
                        or len(ordered_categories) > configuration.max_categories
                    ),
                    "category_tracking_truncated": accumulator.categories_truncated,
                    "empty_string_count": accumulator.empty_count,
                }
                if accumulator.categories_truncated:
                    warnings.append(
                        "Distinct categories exceeded the bounded tracking budget or contained values longer than the display limit; distinct count is unavailable."
                    )
            elif kind in {"date", "timestamp"}:
                minimum = accumulator.minimum
                maximum = accumulator.maximum
                statistics = {
                    "minimum": minimum.isoformat() if minimum is not None else None,
                    "maximum": maximum.isoformat() if maximum is not None else None,
                    "coverage_seconds": (
                        (maximum - minimum).total_seconds()
                        if kind == "timestamp" and minimum is not None and maximum is not None
                        else None
                    ),
                    "coverage_days": (
                        (maximum - minimum).days
                        if kind == "date" and minimum is not None and maximum is not None
                        else None
                    ),
                }
            references = tuple(
                {
                    "finding_id": finding["finding_id"],
                    "rule_id": finding["rule_id"],
                    "classification": finding["classification"],
                    "severity": finding["severity"],
                }
                for finding in verified_findings
                if isinstance(finding.get("scope"), dict)
                and finding["scope"].get("table_index") == configuration.table_index
                and finding["scope"].get("column_name") == name
            )
            column_results.append(
                EDAColumnResult(
                    name=name,
                    data_type=str(field_value.type),
                    status="completed",
                    total_rows=table["row_count"],
                    non_null_count=non_null,
                    null_count=accumulator.null_count,
                    null_percentage=(
                        accumulator.null_count / table["row_count"] * 100
                        if table["row_count"]
                        else None
                    ),
                    statistics=statistics,
                    warnings=tuple(warnings),
                    related_quality_findings=references,
                )
            )

        status: Literal["completed", "partially_completed"] = (
            "partially_completed"
            if any(result.status == "unsupported" for result in column_results)
            else "completed"
        )
        configuration_dict = configuration.as_dict()
        encoded_configuration = _canonical_json(configuration_dict)
        result = EDAResult(
            schema_version=EDA_RESULT_SCHEMA_VERSION,
            analysis_type=configuration.analysis_type,
            status=status,
            dataset_id=dataset_id,
            file_id=file_id,
            table_index=configuration.table_index,
            table_name=table.get("table_name"),
            artifact_sha256=table["sha256"],
            normalization_version=normalization_result["pipeline_version"],
            algorithm_version=_algorithm_version_for_analysis(configuration.analysis_type),
            configuration=configuration_dict,
            configuration_sha256=hashlib.sha256(encoded_configuration).hexdigest(),
            executed_at=datetime.now(timezone.utc).isoformat(),
            requested_columns=configuration.selected_columns,
            analyzed_columns=tuple(analyzed),
            rows_analyzed=scanned_rows,
            exact=True,
            sampling=None,
            columns=tuple(column_results),
            warnings=tuple(global_warnings),
            provenance=_eda_provenance(
                dataset_id=dataset_id,
                file_id=file_id,
                source_sha256=source_sha256,
                source_size_bytes=normalization_result["source"][
                    "file_size_bytes"
                ],
                normalization_result=normalization_result,
                table=table,
                schema=schema,
                configuration=configuration,
                result_status=status,
                verification_status="verified",
            ),
        )
        if len(_canonical_json(result.as_dict())) > configuration.max_result_bytes:
            raise EDAError("result_size_limit", "EDA result exceeds the configured serialized size limit.")
        return result
    except EDAError:
        raise
    except (OSError, ValueError, pa.ArrowException, OverflowError) as error:
        return _failure_result(
            dataset_id=dataset_id,
            file_id=file_id,
            configuration=configuration,
            artifact_sha256=table.get("sha256"),
            normalization_version=normalization_result.get("pipeline_version"),
            table_name=table.get("table_name"),
            source_sha256=source_sha256,
            source_size_bytes=normalization_result.get("source", {}).get(
                "file_size_bytes"
            ),
            normalization_result=normalization_result,
            code="parquet_analysis_failed",
            message=f"EDA could not safely scan the normalized artifact ({type(error).__name__}).",
        )
    finally:
        if parquet is not None:
            parquet.close()
