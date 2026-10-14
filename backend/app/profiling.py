import hashlib
import json
import math
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation, localcontext


_MISSING = object()
_MAX_SUMMARY_VALUES = 10
_MAX_DISPLAY_VALUE_LENGTH = 256
MAX_PROFILE_VALUE_ESTIMATE_BYTES = 64 * 1024
MAX_PROFILE_TRACKING_ESTIMATE_BYTES = 16 * 1024 * 1024
MISSING = _MISSING


def _bounded_value_size_estimate(value, limit: int) -> int:
    pending = [iter((value,))]
    estimated = 64
    while pending:
        try:
            item = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        if isinstance(item, str):
            estimated += len(item) * 8 + 64
        elif isinstance(item, bytes):
            estimated += len(item) + 64
        elif isinstance(item, dict):
            estimated += len(item) * 16 + 64
            pending.append(iter(item.values()))
            pending.append(iter(item.keys()))
        elif isinstance(item, (list, tuple)):
            estimated += len(item) * 8 + 64
            pending.append(iter(item))
        elif isinstance(item, bool):
            estimated += 64
        elif isinstance(item, int):
            estimated += (item.bit_length() + 7) // 8 + 64
        elif isinstance(item, Decimal):
            estimated += len(item.as_tuple().digits) * 2 + 128
        else:
            estimated += 128
        if estimated > limit:
            return limit + 1
    return estimated


def _canonical_value(value) -> str:
    normalized = _normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _normalize(value):
    if value is _MISSING:
        return ["missing"]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            return ["float", repr(value)]
        return ["float", value.hex()]
    if isinstance(value, (dict, list)):
        if isinstance(value, list):
            return ["array", [_normalize(item) for item in value]]
        return [
            "object",
            [
                [str(key), _normalize(item)]
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ],
        ]
    if hasattr(value, "isoformat"):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    return [type(value).__name__, str(value)]


def _fingerprint(value) -> str:
    return hashlib.sha256(_canonical_value(value).encode("utf-8")).hexdigest()


def _row_fingerprint(values: Sequence) -> str:
    encoded = json.dumps(
        [
            ["missing"]
            if value is _MISSING
            else ["value", _normalize(value)]
            for value in values
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _display_value(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    if isinstance(value, (Decimal, bool, int, float)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _json_number(value: Decimal):
    if value == value.to_integral_value():
        return int(value)
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _decimal_addition_precision(values: Sequence[Decimal]) -> int:
    nonzero = [value for value in values if value]
    if not nonzero:
        return 28
    minimum_exponent = min(value.as_tuple().exponent for value in nonzero)
    maximum_adjusted = max(value.adjusted() for value in nonzero)
    return max(
        28,
        maximum_adjusted - minimum_exponent + len(str(len(values))) + 2,
    )


class ProfileBudget:
    def __init__(
        self,
        max_distinct_values: int,
        max_numeric_values: int,
        max_duplicate_rows: int,
        max_tracking_bytes: int = MAX_PROFILE_TRACKING_ESTIMATE_BYTES,
    ):
        self.max_distinct_values = max_distinct_values
        self.max_numeric_values = max_numeric_values
        self.max_duplicate_rows = max_duplicate_rows
        self.max_tracking_bytes = max_tracking_bytes
        self.distinct_values_used = 0
        self.numeric_values_used = 0
        self.duplicate_rows_used = 0
        self.tracking_bytes_used = 0
        self.warnings: list[str] = []
        self.unsupported: list[str] = []

    def warning(self, message: str, explanation: str | None = None) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
        if explanation and explanation not in self.unsupported:
            self.unsupported.append(explanation)


class ColumnProfiler:
    def __init__(
        self,
        descriptor: dict,
        budget: ProfileBudget,
        coerce_numeric_strings: bool,
    ):
        self.descriptor = descriptor
        self.budget = budget
        self.coerce_numeric_strings = coerce_numeric_strings
        self.missing = 0
        self.empty = 0
        self.non_numeric = 0
        self.numeric_count = 0
        self.numeric_sum = Decimal(0)
        self.numeric_min_exponent: int | None = None
        self.numeric_max_adjusted: int | None = None
        self.numeric_min: Decimal | None = None
        self.numeric_max: Decimal | None = None
        self.numeric_values: list[Decimal] | None = []
        self.value_counts: dict[str, list] | None = {}
        self.tracked_bytes = 0
        self.numeric_tracked_bytes = 0
        self.distinct_complete = True

    def observe(self, value) -> None:
        if value is _MISSING or value is None:
            self.missing += 1
            return
        is_empty = value == ""
        if is_empty:
            self.empty += 1

        value_estimate = _bounded_value_size_estimate(
            value,
            MAX_PROFILE_VALUE_ESTIMATE_BYTES,
        )
        fingerprint = None
        if self.value_counts is not None and value_estimate <= MAX_PROFILE_VALUE_ESTIMATE_BYTES:
            fingerprint = _fingerprint(value)
        if self.value_counts is not None:
            if fingerprint is not None and fingerprint in self.value_counts:
                self.value_counts[fingerprint][1] += 1
            elif (
                fingerprint is not None
                and self.budget.distinct_values_used < self.budget.max_distinct_values
                and self.budget.tracking_bytes_used + value_estimate * 2 + 256
                <= self.budget.max_tracking_bytes
            ):
                display = _display_value(value)
                truncated = len(display) > _MAX_DISPLAY_VALUE_LENGTH
                if truncated:
                    display = display[:_MAX_DISPLAY_VALUE_LENGTH]
                self.value_counts[fingerprint] = [display, 1, truncated]
                self.budget.distinct_values_used += 1
                retained_estimate = value_estimate * 2 + 256
                self.budget.tracking_bytes_used += retained_estimate
                self.tracked_bytes += retained_estimate
            else:
                self._discard_value_tracking(
                    "Exact distinct-value and categorical summaries exceeded the configured memory budget.",
                    "Exact distinct counts and categorical summaries are unavailable for columns exceeding the profile tracking budget.",
                )

        if is_empty:
            return
        if value_estimate > MAX_PROFILE_VALUE_ESTIMATE_BYTES:
            self.non_numeric += 1
            self.budget.warning(
                "Oversized values were excluded from exact profile summaries.",
                "Values above the per-value profile estimate limit are excluded from numeric coercion, exact distinct counts, and categorical summaries.",
            )
            return
        number = self._number(value, self.coerce_numeric_strings)
        if number is None:
            self.non_numeric += 1
            return

        self.numeric_count += 1
        self.numeric_min_exponent = (
            number.as_tuple().exponent
            if self.numeric_min_exponent is None
            else min(self.numeric_min_exponent, number.as_tuple().exponent)
        )
        self.numeric_max_adjusted = (
            number.adjusted()
            if self.numeric_max_adjusted is None
            else max(self.numeric_max_adjusted, number.adjusted())
        )
        precision = max(
            28,
            self.numeric_max_adjusted
            - self.numeric_min_exponent
            + len(str(self.numeric_count))
            + 2,
        )
        with localcontext() as context:
            context.prec = precision
            self.numeric_sum += number
        self.numeric_min = number if self.numeric_min is None else min(self.numeric_min, number)
        self.numeric_max = number if self.numeric_max is None else max(self.numeric_max, number)
        if self.numeric_values is not None:
            numeric_estimate = value_estimate + 128
            if (
                self.budget.numeric_values_used < self.budget.max_numeric_values
                and self.budget.tracking_bytes_used + numeric_estimate
                <= self.budget.max_tracking_bytes
            ):
                self.numeric_values.append(number)
                self.budget.numeric_values_used += 1
                self.budget.tracking_bytes_used += numeric_estimate
                self.numeric_tracked_bytes += numeric_estimate
            else:
                self.budget.numeric_values_used -= len(self.numeric_values)
                self.budget.tracking_bytes_used -= self.numeric_tracked_bytes
                self.numeric_tracked_bytes = 0
                self.numeric_values = None
                self.budget.warning(
                    "Exact numeric medians exceeded the configured memory budget.",
                    "Numeric minimum, maximum, and mean remain exact; median is unavailable where retaining all numeric values would exceed the profile tracking budget.",
                )

    def _discard_value_tracking(
        self,
        warning: str,
        explanation: str,
    ) -> None:
        if self.value_counts is None:
            return
        self.distinct_complete = False
        self.budget.distinct_values_used -= len(self.value_counts)
        self.budget.tracking_bytes_used -= self.tracked_bytes
        self.tracked_bytes = 0
        self.value_counts = None
        self.budget.warning(warning, explanation)

    @staticmethod
    def _number(value, coerce_numeric_strings: bool) -> Decimal | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            if (
                not coerce_numeric_strings
                or not isinstance(value, str)
                or not value.strip()
            ):
                return None
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if not number.is_finite():
            return None
        try:
            converted = float(number)
            return (
                number
                if math.isfinite(converted) and (converted != 0 or number == 0)
                else None
            )
        except (OverflowError, ValueError):
            return None

    def as_dict(self) -> dict:
        if self.value_counts is None:
            distinct_count = None
            categorical = None
        else:
            distinct_count = len(self.value_counts)
            values = sorted(
                self.value_counts.values(),
                key=lambda item: (-item[1], item[0]),
            )[:_MAX_SUMMARY_VALUES]
            categorical = [
                {
                    "value": value,
                    "count": count,
                    "value_truncated": truncated,
                }
                for value, count, truncated in values
            ]
            if self.numeric_count:
                categorical = None

        if self.numeric_count == 0:
            numeric_statistics = None
            numeric_note = "No finite numeric values were present."
        elif self.non_numeric or self._has_mixed_physical_types():
            numeric_statistics = None
            numeric_note = "Numeric statistics are unsupported because the column contains mixed or non-numeric values."
            self.budget.warning(
                "Numeric statistics were skipped for columns containing mixed or non-numeric values.",
                "Numeric statistics require every non-missing, non-empty value in a column to be a finite number.",
            )
        else:
            median = None
            median_exact = self.numeric_values is not None
            if median_exact:
                ordered = sorted(self.numeric_values)
                middle = len(ordered) // 2
                if len(ordered) % 2:
                    median = ordered[middle]
                else:
                    midpoint_values = [ordered[middle - 1], ordered[middle]]
                    with localcontext() as context:
                        context.prec = _decimal_addition_precision(midpoint_values)
                        median = sum(midpoint_values, Decimal(0)) / 2
            with localcontext() as context:
                context.prec = max(40, len(self.numeric_sum.as_tuple().digits) + 2)
                mean = self.numeric_sum / self.numeric_count
            numeric_statistics = {
                "minimum": _json_number(self.numeric_min),
                "maximum": _json_number(self.numeric_max),
                "mean": _json_number(mean),
                "median": _json_number(median) if median is not None else None,
                "median_exact": median_exact,
            }
            numeric_note = None if median_exact else "Median exceeded the configured tracking budget."
            if any(
                numeric_statistics[name] is None
                for name in ("minimum", "maximum", "mean")
            ) or (median is not None and numeric_statistics["median"] is None):
                numeric_note = "One or more statistics are outside the finite JSON number range."
                self.budget.warning(
                    "Some numeric statistics could not be represented as finite JSON numbers.",
                    "Statistics outside the finite JSON number range are returned as unavailable.",
                )

        return {
            "name": self.descriptor["name"],
            "position": self.descriptor["position"],
            "physical_types": self.descriptor.get("physical_types", []),
            "missing_value_count": self.missing,
            "empty_value_count": self.empty,
            "distinct_value_count": distinct_count,
            "distinct_count_exact": self.distinct_complete and distinct_count is not None,
            "numeric_statistics": numeric_statistics,
            "numeric_statistics_note": numeric_note,
            "categorical_summary": categorical,
            "categorical_summary_complete": categorical is not None,
            "categorical_summary_note": (
                "The column contains numeric values."
                if self.numeric_count and categorical is None
                else (
                    "Categorical summary exceeded the configured tracking budget."
                    if self.value_counts is None
                    else None
                )
            ),
        }

    def _has_mixed_physical_types(self) -> bool:
        physical_types = {
            value
            for value in self.descriptor.get("physical_types", [])
            if value != "null"
        }
        if physical_types and physical_types <= {"integer", "number"}:
            return False
        return len(physical_types) > 1


class TableProfiler:
    def __init__(
        self,
        name: str | None,
        columns: Sequence[dict],
        budget: ProfileBudget,
        coerce_numeric_strings: bool,
    ):
        self.name = name
        self.columns = [
            ColumnProfiler(column, budget, coerce_numeric_strings)
            for column in columns
        ]
        self.row_count = 0
        self.row_fingerprints: set[str] | None = set()
        self.row_fingerprint_estimate_bytes = 0
        self.duplicate_rows = 0
        self.budget = budget

    def set_column_descriptors(self, columns: Sequence[dict]) -> None:
        if len(columns) != len(self.columns):
            raise ValueError("Profile descriptors do not match the table width.")
        for profiler, descriptor in zip(self.columns, columns):
            profiler.descriptor = descriptor

    def observe(self, values: Sequence) -> None:
        if len(values) != len(self.columns):
            raise ValueError("Profile row width does not match its column descriptors.")
        self.row_count += 1
        for column, value in zip(self.columns, values):
            column.observe(value)

        if self.row_fingerprints is not None:
            row_estimate = _bounded_value_size_estimate(
                values,
                MAX_PROFILE_VALUE_ESTIMATE_BYTES,
            )
            if (
                self.budget.duplicate_rows_used >= self.budget.max_duplicate_rows
                or row_estimate > MAX_PROFILE_VALUE_ESTIMATE_BYTES
            ):
                self.budget.duplicate_rows_used -= len(self.row_fingerprints)
                self.budget.tracking_bytes_used -= self.row_fingerprint_estimate_bytes
                self.row_fingerprint_estimate_bytes = 0
                self.row_fingerprints = None
                self.budget.warning(
                    "Duplicate-row detection exceeded the configured memory budget.",
                    "Duplicate-row counts are unavailable when the dataset exceeds the configured row-fingerprint tracking budget.",
                )
            else:
                fingerprint = _row_fingerprint(values)
                if fingerprint in self.row_fingerprints:
                    self.duplicate_rows += 1
                elif (
                    self.budget.tracking_bytes_used + 256
                    > self.budget.max_tracking_bytes
                ):
                    self.budget.duplicate_rows_used -= len(self.row_fingerprints)
                    self.budget.tracking_bytes_used -= self.row_fingerprint_estimate_bytes
                    self.row_fingerprint_estimate_bytes = 0
                    self.row_fingerprints = None
                    self.budget.warning(
                        "Duplicate-row detection exceeded the configured memory budget.",
                        "Duplicate-row counts are unavailable when the dataset exceeds the configured row-fingerprint tracking budget.",
                    )
                else:
                    self.row_fingerprints.add(fingerprint)
                    self.budget.duplicate_rows_used += 1
                    self.budget.tracking_bytes_used += 256
                    self.row_fingerprint_estimate_bytes += 256

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "row_count": self.row_count,
            "column_count": len(self.columns),
            "duplicate_row_count": (
                self.duplicate_rows if self.row_fingerprints is not None else None
            ),
            "duplicate_row_count_exact": self.row_fingerprints is not None,
            "columns": [column.as_dict() for column in self.columns],
        }


class DatasetProfiler:
    def __init__(
        self,
        detected_format: str,
        max_distinct_values: int,
        max_numeric_values: int,
        max_duplicate_rows: int,
    ):
        self.detected_format = detected_format
        self.budget = ProfileBudget(
            max_distinct_values,
            max_numeric_values,
            max_duplicate_rows,
        )
        self.tables: list[TableProfiler] = []
        self.coerce_numeric_strings = detected_format in {"csv", "tsv", "xml"}

    def add_table(self, name: str | None, columns: Sequence[dict]) -> TableProfiler:
        table = TableProfiler(
            name,
            columns,
            self.budget,
            self.coerce_numeric_strings,
        )
        self.tables.append(table)
        return table

    def as_dict(self) -> dict:
        tables = [table.as_dict() for table in self.tables]
        return {
            "version": 1,
            "detected_format": self.detected_format,
            "row_count": sum(table["row_count"] for table in tables),
            "column_count": sum(table["column_count"] for table in tables),
            "tables": tables,
            "warnings": self.budget.warnings,
            "unsupported_analyses": self.budget.unsupported,
        }
