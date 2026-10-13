import hashlib
import json
import math
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError

from app.normalization import (
    DEFAULT_MAX_OUTPUT_BYTES,
    NormalizationError,
    validate_normalization_result,
)
from app.profiling import _MAX_DISPLAY_VALUE_LENGTH
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
MIN_HISTOGRAM_BINS = 2
MAX_HISTOGRAM_BINS = 100
MAX_EDA_PERCENTILES = 16
MAX_EDA_CATEGORIES = 100
MAX_EDA_RESULT_BYTES = 1024 * 1024
DEFAULT_EDA_RESULT_BYTES = 256 * 1024
MAX_EDA_NUMERIC_VALUES = 250_000
MAX_EDA_TRACKED_CATEGORIES = 50_000
MAX_EDA_ROWS = 1_000_000
MAX_EDA_SCAN_BYTES = DEFAULT_MAX_OUTPUT_BYTES
EDA_BATCH_SIZE = 4096
_COLUMN_NAME_LIMIT = 256
_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


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
    histogram_bins: int = 10
    percentiles: tuple[float, ...] = (25.0, 50.0, 75.0)
    max_categories: int = 10
    max_result_bytes: int = DEFAULT_EDA_RESULT_BYTES

    def __post_init__(self) -> None:
        _validate_configuration(self)

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "analysis_type": self.analysis_type,
            "table_index": self.table_index,
            "selected_columns": list(self.selected_columns),
            "histogram_bins": self.histogram_bins,
            "percentiles": list(self.percentiles),
            "max_categories": self.max_categories,
            "max_result_bytes": self.max_result_bytes,
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
    if configuration.analysis_type != "univariate":
        raise EDAError("unsupported_analysis_type", "Only univariate EDA is supported.")
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


def eda_configuration_from_dict(value: dict) -> EDAConfiguration:
    if not isinstance(value, dict):
        raise EDAError("invalid_configuration", "EDA configuration must be an object.")
    allowed = {
        "schema_version",
        "analysis_type",
        "table_index",
        "selected_columns",
        "histogram_bins",
        "percentiles",
        "max_categories",
        "max_result_bytes",
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
        return number if math.isfinite(number) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


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
    mean = math.fsum(value / count for value in ordered)
    minimum, maximum = ordered[0], ordered[-1]
    data_range = maximum - minimum
    variance_overflow = False
    if count > 1:
        try:
            divisor = math.sqrt(count - 1)
            variance = math.fsum(
                ((value - mean) / divisor) ** 2 for value in ordered
            )
            if not math.isfinite(variance):
                variance = None
                variance_overflow = True
        except (OverflowError, ValueError):
            variance = None
            variance_overflow = True
    else:
        variance = None
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


def _failure_result(
    *,
    dataset_id: int,
    file_id: int,
    configuration: EDAConfiguration,
    artifact_sha256: str | None,
    normalization_version: str | None,
    table_name: str | None,
    code: str,
    message: str,
) -> EDAResult:
    encoded = _canonical_json(configuration.as_dict())
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
        algorithm_version=EDA_ALGORITHM_VERSION,
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
                    for value in array.to_pylist():
                        _observe_value(
                            accumulator,
                            value,
                            kind,
                            numeric_budget,
                            category_budget,
                        )
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
            algorithm_version=EDA_ALGORITHM_VERSION,
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
            code="parquet_analysis_failed",
            message=f"EDA could not safely scan the normalized artifact ({type(error).__name__}).",
        )
    finally:
        if parquet is not None:
            parquet.close()
