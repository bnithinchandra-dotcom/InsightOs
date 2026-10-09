import csv
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time
from io import TextIOWrapper
from typing import BinaryIO

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from openpyxl.styles.numbers import is_datetime
from openpyxl.utils.exceptions import InvalidFileException
import pyarrow as pa
import pyarrow.parquet as pq
from xml.etree.ElementTree import ParseError, TreeBuilder

from app.profiling import DatasetProfiler, MISSING


FORMAT_BY_EXTENSION = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".xlsx": "xlsx",
    ".json": "json",
    ".parquet": "parquet",
    ".xml": "xml",
}


class ParsingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ParserConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class ParseLimits:
    max_rows: int
    max_columns: int
    max_xlsx_uncompressed_bytes: int
    max_xlsx_entries: int
    max_parquet_row_groups: int
    max_xml_file_bytes: int
    max_xml_depth: int
    max_xml_elements: int
    max_json_depth: int
    max_profile_distinct_values: int
    max_profile_numeric_values: int
    max_profile_duplicate_rows: int


@dataclass
class ParseResult:
    detected_format: str
    columns: list[dict]
    row_count: int
    metadata: dict
    profile_result: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "detected_format": self.detected_format,
            "columns": self.columns,
            "row_count": self.row_count,
            "metadata": self.metadata,
        }


@dataclass
class _ColumnStats:
    name: str
    position: int
    observed_types: set[str] = field(default_factory=set)
    missing_values: int = 0
    empty_values: int = 0

    def observe(self, value, type_name: str | None = None) -> None:
        if value is None:
            self.missing_values += 1
            return
        if value == "":
            self.empty_values += 1
        self.observed_types.add(type_name or _json_physical_type(value))

    def as_dict(self) -> dict:
        types = sorted(self.observed_types)
        physical_type = types[0] if len(types) == 1 else ("mixed" if types else "unknown")
        return {
            "name": self.name,
            "position": self.position,
            "physical_type": physical_type,
            "physical_types": types,
            "missing_values": self.missing_values,
            "empty_values": self.empty_values,
        }


def _positive_limit(name: str, default: int) -> int:
    supplied = os.getenv(name)
    try:
        value = int(supplied) if supplied is not None else default
    except ValueError:
        raise ParserConfigurationError(f"{name} must be a positive integer.") from None
    if value <= 0:
        raise ParserConfigurationError(f"{name} must be a positive integer.")
    return value


def get_parse_limits() -> ParseLimits:
    return ParseLimits(
        max_rows=_positive_limit("MAX_DATASET_ROWS", 1_000_000),
        max_columns=_positive_limit("MAX_DATASET_COLUMNS", 10_000),
        max_xlsx_uncompressed_bytes=_positive_limit(
            "MAX_XLSX_UNCOMPRESSED_MB", 512
        )
        * 1024
        * 1024,
        max_xlsx_entries=_positive_limit("MAX_XLSX_ENTRIES", 100_000),
        max_parquet_row_groups=_positive_limit("MAX_PARQUET_ROW_GROUPS", 100_000),
        max_xml_file_bytes=_positive_limit("MAX_XML_SIZE_MB", 50) * 1024 * 1024,
        max_xml_depth=_positive_limit("MAX_XML_DEPTH", 64),
        max_xml_elements=_positive_limit("MAX_XML_ELEMENTS", 100_000),
        max_json_depth=_positive_limit("MAX_JSON_DEPTH", 64),
        max_profile_distinct_values=_positive_limit(
            "MAX_PROFILE_DISTINCT_VALUES", 100_000
        ),
        max_profile_numeric_values=_positive_limit(
            "MAX_PROFILE_NUMERIC_VALUES", 250_000
        ),
        max_profile_duplicate_rows=_positive_limit(
            "MAX_PROFILE_DUPLICATE_ROWS", 250_000
        ),
    )


def _profile_for_format(detected_format: str, limits: ParseLimits) -> DatasetProfiler:
    return DatasetProfiler(
        detected_format,
        limits.max_profile_distinct_values,
        limits.max_profile_numeric_values,
        limits.max_profile_duplicate_rows,
    )


def _fail(code: str, message: str) -> None:
    raise ParsingError(code, message)


def _check_size(file_size: int) -> None:
    if file_size == 0:
        _fail("empty_file", "The uploaded file is empty.")


def _json_physical_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "unknown"


def _merge_json_type(stats: _ColumnStats, value) -> None:
    stats.observed_types.add(_json_physical_type(value))


def _read_json(stream: BinaryIO, limits: ParseLimits) -> ParseResult:
    try:
        stream.seek(0)
        text = stream.read().decode("utf-8-sig")
        if not text.strip():
            _fail("empty_file", "The uploaded JSON file is empty.")

        def object_without_duplicate_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    _fail(
                        "malformed_json",
                        "The JSON document contains a duplicate object key.",
                    )
                result[key] = value
            return result

        document = json.loads(
            text,
            object_pairs_hook=object_without_duplicate_keys,
            parse_constant=lambda _value: _fail(
                "malformed_json",
                "The JSON document contains a non-standard numeric value.",
            ),
        )
    except ParsingError:
        raise
    except UnicodeDecodeError:
        _fail("malformed_json", "The JSON file must use UTF-8 encoding.")
    except (json.JSONDecodeError, RecursionError):
        _fail("malformed_json", "The JSON document is malformed.")

    _check_json_depth(document, limits.max_json_depth)
    container_path = "$"
    if isinstance(document, list):
        records = document
    elif isinstance(document, dict):
        array_fields = [
            (key, value)
            for key, value in document.items()
            if isinstance(value, list)
            and all(isinstance(item, dict) for item in value)
        ]
        if array_fields:
            if len(document) != 1 or len(array_fields) != 1:
                _fail(
                    "unsupported_json_structure",
                    "A JSON wrapper must contain only one array of record objects; "
                    "sibling fields or arrays are not supported.",
                )
            key, records = array_fields[0]
            container_path = f"$.{key}"
        elif all(not isinstance(value, (dict, list)) for value in document.values()):
            records = [document]
        else:
            _fail(
                "unsupported_json_structure",
                "JSON records must be an array of objects, a single flat object, "
                "or an object containing one array of objects.",
            )
    else:
        _fail(
            "unsupported_json_structure",
            "JSON records must be objects in an array or a flat object.",
        )

    if not records:
        _fail("empty_records", "The JSON document contains no records.")
    if len(records) > limits.max_rows:
        _fail("resource_limit", "The JSON document exceeds the configured row limit.")
    if any(not isinstance(record, dict) for record in records):
        _fail(
            "unsupported_json_structure",
            "Every JSON record must be an object; mixed record types are unsupported.",
        )

    ordered_names = []
    stats_by_name = {}
    for record in records:
        for name in record:
            if name not in stats_by_name:
                ordered_names.append(name)
                stats_by_name[name] = _ColumnStats(
                    name=name,
                    position=len(ordered_names) - 1,
                )

    if len(ordered_names) > limits.max_columns:
        _fail("resource_limit", "The JSON document exceeds the configured column limit.")
    if not ordered_names:
        _fail("empty_records", "The JSON records contain no columns.")

    for record in records:
        for name in ordered_names:
            stats = stats_by_name[name]
            if name not in record:
                stats.missing_values += 1
            else:
                value = record[name]
                if value == "":
                    stats.empty_values += 1
                _merge_json_type(stats, value)

    columns = [stats_by_name[name].as_dict() for name in ordered_names]
    profiler = _profile_for_format("json", limits)
    profile_table = profiler.add_table(None, columns)
    for record in records:
        profile_table.observe(
            [record[name] if name in record else MISSING for name in ordered_names]
        )
    return ParseResult(
        detected_format="json",
        columns=columns,
        row_count=len(records),
        metadata={
            "record_path": container_path,
            "record_shape": "object",
            "nested_values": "retained as object or array values",
        },
        profile_result=profiler.as_dict(),
    )


def _check_json_depth(document, max_depth: int) -> None:
    pending = [(document, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > max_depth:
            _fail("resource_limit", "The JSON document exceeds the configured nesting limit.")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)


def _read_delimited(
    filename: str,
    stream: BinaryIO,
    file_size: int,
    limits: ParseLimits,
) -> ParseResult:
    delimiter = "\t" if filename.lower().endswith(".tsv") else ","
    stream.seek(0)
    signature = stream.read(4096)
    stream.seek(0)
    sample = signature.lstrip(b"\xef\xbb\xbf \t\r\n")
    likely_json = sample.startswith(b'{"') or sample.startswith(b"[{")
    if sample.startswith((b"{", b"[")) and not likely_json:
        try:
            json_value, _ = json.JSONDecoder().raw_decode(sample.decode("utf-8"))
            likely_json = isinstance(json_value, (dict, list))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            pass
    if sample.startswith((b"PK\x03\x04", b"PAR1", b"<?xml", b"<!DOCTYPE")) or likely_json:
        _fail(
            "content_mismatch",
            "The file content does not match the declared delimited-text format.",
        )
    if sample.startswith(b"<"):
        try:
            _parse_xml_tree(stream, limits)
        except ParsingError as error:
            if error.code != "malformed_xml":
                raise
        else:
            _fail(
                "content_mismatch",
                "The file contains a well-formed XML document, not delimited text.",
            )
        finally:
            stream.seek(0)
    if b"\x00" in signature:
        _fail("content_mismatch", "The delimited-text file contains binary data.")

    text_stream = TextIOWrapper(stream, encoding="utf-8-sig", errors="strict", newline="")
    try:
        reader = csv.reader(text_stream, delimiter=delimiter, strict=True)
        try:
            headers = next(reader)
        except StopIteration:
            _fail("empty_file", "The uploaded delimited-text file is empty.")
        if not headers:
            _fail("malformed_delimited_text", "The file does not contain a header row.")
        if len(headers) > limits.max_columns:
            _fail(
                "resource_limit",
                "The delimited-text file exceeds the configured column limit.",
            )

        stats = [
            _ColumnStats(name=name, position=index)
            for index, name in enumerate(headers)
        ]
        profiler = _profile_for_format(
            "tsv" if delimiter == "\t" else "csv",
            limits,
        )
        profile_table = profiler.add_table(None, [column.as_dict() for column in stats])
        row_count = 0
        for row in reader:
            if len(row) != len(headers):
                _fail(
                    "malformed_delimited_text",
                    f"Record {reader.line_num} has {len(row)} fields; "
                    f"the header has {len(headers)}.",
                )
            row_count += 1
            if row_count > limits.max_rows:
                _fail(
                    "resource_limit",
                    "The delimited-text file exceeds the configured row limit.",
                )
            for column, value in zip(stats, row):
                column.observe(value, "string")
            profile_table.observe(row)
        profile_table.set_column_descriptors(
            [column.as_dict() for column in stats]
        )
    except ParsingError:
        raise
    except UnicodeDecodeError:
        _fail("malformed_delimited_text", "The file must use UTF-8 encoding.")
    except csv.Error:
        _fail("malformed_delimited_text", "The delimited-text file is malformed.")
    finally:
        try:
            text_stream.detach()
        except ValueError:
            pass

    return ParseResult(
        detected_format="tsv" if delimiter == "\t" else "csv",
        columns=[column.as_dict() for column in stats],
        row_count=row_count,
        metadata={
            "delimiter": "tab" if delimiter == "\t" else "comma",
            "encoding": "utf-8-sig",
            "header_row": 1,
            "header_policy": "first record; names and duplicate names preserved",
            "row_width_policy": "every data record must match the header width",
        },
        profile_result=profiler.as_dict(),
    )


def _excel_type(cell) -> str:
    if cell.data_type == "f":
        return "formula"
    if cell.data_type == "e":
        return "error"
    value = cell.value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, datetime):
        if value.time() == time.min and is_datetime(cell.number_format) == "date":
            return "date"
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, time):
        return "time"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _read_xlsx(stream: BinaryIO, file_size: int, limits: ParseLimits) -> ParseResult:
    stream.seek(0)
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if (
                len(entries) > limits.max_xlsx_entries
                or sum(item.file_size for item in entries)
                > limits.max_xlsx_uncompressed_bytes
            ):
                _fail(
                    "resource_limit",
                    "The XLSX archive exceeds the configured expanded-size limit.",
                )
            names = {item.filename for item in entries}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                _fail("content_mismatch", "The file is not a valid XLSX workbook.")
    except ParsingError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError):
        _fail("corrupt_xlsx", "The XLSX workbook is corrupt or malformed.")

    stream.seek(0)
    try:
        workbook = load_workbook(
            stream,
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except (InvalidFileException, KeyError, OSError, ValueError, zipfile.BadZipFile):
        _fail("corrupt_xlsx", "The XLSX workbook is corrupt or malformed.")

    row_count_total = 0
    all_columns = []
    sheet_metadata = []
    found_data = False
    profiler = _profile_for_format("xlsx", limits)
    formula_cells_seen = False
    try:
        for worksheet in workbook.worksheets:
            iterator = worksheet.iter_rows()
            first_row = next(iterator, None)
            if first_row is None or not any(cell.value is not None for cell in first_row):
                sheet_metadata.append(
                    {"name": worksheet.title, "row_count": 0, "columns": []}
                )
                profiler.add_table(worksheet.title, [])
                continue

            found_data = True
            headers = ["" if cell.value is None else str(cell.value) for cell in first_row]
            if len(headers) > limits.max_columns:
                _fail("resource_limit", "An XLSX worksheet exceeds the configured column limit.")
            stats = [
                _ColumnStats(name=name, position=index)
                for index, name in enumerate(headers)
            ]
            profile_table = profiler.add_table(
                worksheet.title,
                [column.as_dict() for column in stats],
            )
            sheet_rows = 0
            for row in iterator:
                if len(row) != len(headers):
                    _fail("corrupt_xlsx", "An XLSX worksheet has inconsistent row widths.")
                sheet_rows += 1
                row_count_total += 1
                if row_count_total > limits.max_rows:
                    _fail("resource_limit", "The XLSX workbook exceeds the configured row limit.")
                for column, cell in zip(stats, row):
                    column.observe(cell.value, _excel_type(cell))
                    formula_cells_seen = formula_cells_seen or cell.data_type == "f"
                profile_table.observe([cell.value for cell in row])
            profile_table.set_column_descriptors(
                [column.as_dict() for column in stats]
            )
            sheet_columns = [column.as_dict() for column in stats]
            if len(all_columns) + len(sheet_columns) > limits.max_columns:
                _fail(
                    "resource_limit",
                    "The XLSX workbook exceeds the configured total column limit.",
                )
            all_columns.extend(
                {
                    **column,
                    "table": worksheet.title,
                }
                for column in sheet_columns
            )
            sheet_metadata.append(
                {
                    "name": worksheet.title,
                    "row_count": sheet_rows,
                    "columns": sheet_columns,
                }
            )
    except ParsingError:
        raise
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        _fail("corrupt_xlsx", "An XLSX worksheet is corrupt or malformed.")
    finally:
        workbook.close()

    if not found_data:
        _fail("empty_records", "The XLSX workbook contains no non-empty worksheets.")
    if formula_cells_seen:
        profiler.budget.warning(
            "XLSX formula cells are profiled as their stored formula text; formulas are not evaluated.",
            "Calculated formula results are unavailable because formula evaluation is not performed.",
        )
    return ParseResult(
        detected_format="xlsx",
        columns=all_columns,
        row_count=row_count_total,
        metadata={
            "worksheets": sheet_metadata,
            "header_policy": "first non-empty row in each worksheet",
            "formula_policy": "formula cells are reported; formulas are not evaluated",
            "archive_size_bytes": file_size,
        },
        profile_result=profiler.as_dict(),
    )


def _read_parquet(stream: BinaryIO, file_size: int, limits: ParseLimits) -> ParseResult:
    stream.seek(0)
    if stream.read(4) != b"PAR1":
        _fail("content_mismatch", "The file does not have a Parquet header.")
    stream.seek(max(0, file_size - 4))
    if stream.read(4) != b"PAR1":
        _fail("corrupt_parquet", "The Parquet file footer is missing or corrupt.")
    stream.seek(0)
    try:
        parquet_file = pq.ParquetFile(stream)
        schema = parquet_file.schema_arrow
        if len(schema) > limits.max_columns:
            _fail("resource_limit", "The Parquet file exceeds the configured column limit.")
        metadata = parquet_file.metadata
        if metadata.num_row_groups > limits.max_parquet_row_groups:
            _fail("resource_limit", "The Parquet file exceeds the configured row-group limit.")
        row_count = 0
        profiler = _profile_for_format("parquet", limits)
        profile_columns = [
            {
                "name": field.name,
                "position": index,
                "physical_types": [str(field.type)],
            }
            for index, field in enumerate(schema)
        ]
        profile_table = profiler.add_table(None, profile_columns)
        for batch in parquet_file.iter_batches(batch_size=8192):
            row_count += batch.num_rows
            if row_count > limits.max_rows:
                _fail("resource_limit", "The Parquet file exceeds the configured row limit.")
            for row_index in range(batch.num_rows):
                profile_table.observe(
                    [
                        batch.column(column_index)[row_index].as_py()
                        for column_index in range(batch.num_columns)
                    ]
                )
        if row_count != metadata.num_rows:
            _fail("corrupt_parquet", "The Parquet row count does not match its metadata.")
    except ParsingError:
        raise
    except (pa.ArrowException, OSError, ValueError, EOFError):
        _fail("corrupt_parquet", "The Parquet file is corrupt or malformed.")

    columns = [
        {
            "name": field.name,
            "position": index,
            "physical_type": str(field.type),
            "physical_types": [str(field.type)],
            "missing_values": None,
            "empty_values": None,
        }
        for index, field in enumerate(schema)
    ]
    return ParseResult(
        detected_format="parquet",
        columns=columns,
        row_count=row_count,
        metadata={
            "row_groups": metadata.num_row_groups,
            "created_by": metadata.created_by,
            "schema": str(schema),
            "rows_scanned": row_count,
        },
        profile_result=profiler.as_dict(),
    )


def _xml_name(name: str) -> str:
    return name


def _xml_depths(root, limits: ParseLimits) -> tuple[dict[int, int], int]:
    depths = {}
    stack = [(root, 1)]
    elements = 0
    while stack:
        element, depth = stack.pop()
        elements += 1
        if elements > limits.max_xml_elements:
            _fail("resource_limit", "The XML document exceeds the configured element limit.")
        if depth > limits.max_xml_depth:
            _fail("resource_limit", "The XML document exceeds the configured nesting limit.")
        depths[id(element)] = depth
        stack.extend((child, depth + 1) for child in reversed(list(element)))
    return depths, elements


def _xml_candidates(root, depths: dict[int, int]):
    candidates = []
    for parent in root.iter():
        grouped = {}
        for child in list(parent):
            grouped.setdefault(child.tag, []).append(child)
        parent_depth = depths[id(parent)]
        for tag, children in grouped.items():
            if len(children) > 1:
                candidates.append((parent_depth, parent, tag, children))
    return candidates


def _flatten_xml_record(record) -> dict[str, str]:
    fields = {}

    def add(name: str, value: str) -> None:
        if name in fields:
            _fail(
                "unsupported_xml_structure",
                "A record contains repeated nested fields that cannot be represented "
                "as a single flat row.",
            )
        fields[name] = value

    def visit(element, prefix: str) -> None:
        for attribute, value in element.attrib.items():
            add(f"{prefix}/@{_xml_name(attribute)}", value)
        children = list(element)
        if not children:
            add(prefix, element.text or "")
            return

        if element.text and element.text.strip():
            _fail(
                "unsupported_xml_structure",
                "Mixed XML text and child elements cannot be represented as a flat row.",
            )
        for child in children:
            if child.tail and child.tail.strip():
                _fail(
                    "unsupported_xml_structure",
                    "Mixed XML text and child elements cannot be represented as a flat row.",
                )
            visit(child, f"{prefix}/{_xml_name(child.tag)}")

    for attribute, value in record.attrib.items():
        add(f"@{_xml_name(attribute)}", value)
    children = list(record)
    if record.text and record.text.strip():
        if children:
            _fail(
                "unsupported_xml_structure",
                "Mixed XML text and child elements cannot be represented as a flat row.",
            )
        add("#text", record.text)
    for child in children:
        if child.tail and child.tail.strip():
            _fail(
                "unsupported_xml_structure",
                "Mixed XML text and child elements cannot be represented as a flat row.",
            )
        visit(child, _xml_name(child.tag))
    return fields


class _LimitedTreeBuilder:
    def __init__(self, limits: ParseLimits):
        self._builder = TreeBuilder()
        self._limits = limits
        self._depth = 0
        self.element_count = 0

    def start(self, tag, attributes):
        depth = self._depth + 1
        if depth > self._limits.max_xml_depth:
            _fail("resource_limit", "The XML document exceeds the configured nesting limit.")
        if self.element_count >= self._limits.max_xml_elements:
            _fail("resource_limit", "The XML document exceeds the configured element limit.")
        self._depth = depth
        self.element_count += 1
        return self._builder.start(tag, attributes)

    def end(self, tag):
        element = self._builder.end(tag)
        self._depth -= 1
        return element

    def data(self, data):
        self._builder.data(data)

    def comment(self, text):
        self._builder.comment(text)

    def pi(self, target, text):
        self._builder.pi(target, text)

    def close(self):
        return self._builder.close()


def _parse_xml_tree(stream: BinaryIO, limits: ParseLimits):
    stream.seek(0)
    target = _LimitedTreeBuilder(limits)
    parser = SafeElementTree.DefusedXMLParser(
        target=target,
        forbid_dtd=True,
        forbid_entities=True,
        forbid_external=True,
    )
    try:
        root = SafeElementTree.parse(stream, parser=parser).getroot()
    except ParsingError:
        raise
    except (DefusedXmlException, ParseError, OSError, ValueError):
        _fail("malformed_xml", "The XML document is malformed or contains forbidden declarations.")
    return root, target.element_count


def _read_xml(stream: BinaryIO, file_size: int, limits: ParseLimits) -> ParseResult:
    if file_size > limits.max_xml_file_bytes:
        _fail("resource_limit", "The XML document exceeds the configured file-size limit.")
    root, element_count = _parse_xml_tree(stream, limits)

    depths, measured_count = _xml_depths(root, limits)
    if measured_count != element_count:
        _fail("malformed_xml", "The XML document could not be parsed consistently.")
    candidates = _xml_candidates(root, depths)
    if not candidates:
        _fail(
            "unsupported_xml_structure",
            "No repeated sibling elements were found to serve as tabular records.",
        )
    if len(candidates) != 1:
        _fail(
            "ambiguous_xml_records",
            "Multiple repeated XML element groups could represent records.",
        )
    parent_depth, parent, record_tag, records = candidates[0]
    if any(child.tag != record_tag for child in list(parent)):
        _fail(
            "ambiguous_xml_records",
            "The record container has unrelated sibling elements; record selection is ambiguous.",
        )
    if parent.text and parent.text.strip():
        _fail(
            "unsupported_xml_structure",
            "Non-record text outside the selected record group is unsupported.",
        )
    if any(record.tail and record.tail.strip() for record in records):
        _fail(
            "unsupported_xml_structure",
            "Non-record text outside the selected record group is unsupported.",
        )

    ancestors = []
    path = []
    cursor = root
    path.append(_xml_name(root.tag))
    while cursor is not parent:
        children = list(cursor)
        next_nodes = [
            child
            for child in children
            if child is parent or any(descendant is parent for descendant in child.iter())
        ]
        if len(next_nodes) != 1 or len(children) != 1:
            _fail(
                "ambiguous_xml_records",
                "The record group is mixed with unrelated XML structure.",
            )
        if cursor.text and cursor.text.strip():
            _fail(
                "unsupported_xml_structure",
                "Non-record text outside the selected record group is unsupported.",
            )
        next_node = next_nodes[0]
        if next_node.tail and next_node.tail.strip():
            _fail(
                "unsupported_xml_structure",
                "Non-record text outside the selected record group is unsupported.",
            )
        ancestors.append(
            {
                "element": _xml_name(cursor.tag),
                "attributes": dict(cursor.attrib),
            }
        )
        cursor = next_node
        path.append(_xml_name(cursor.tag))
    ancestors.append(
        {
            "element": _xml_name(parent.tag),
            "attributes": dict(parent.attrib),
        }
    )
    if len(path) > parent_depth:
        _fail("ambiguous_xml_records", "The XML record path could not be determined.")

    if len(records) > limits.max_rows:
        _fail("resource_limit", "The XML document exceeds the configured row limit.")

    ordered_names = []
    stats_by_name = {}
    record_rows = []
    for record in records:
        values = _flatten_xml_record(record)
        record_rows.append(values)
        for name in values:
            if name not in stats_by_name:
                if len(ordered_names) >= limits.max_columns:
                    _fail("resource_limit", "The XML document exceeds the configured column limit.")
                ordered_names.append(name)
                stats_by_name[name] = _ColumnStats(name=name, position=len(ordered_names) - 1)

    if not ordered_names:
        _fail(
            "unsupported_xml_structure",
            "The repeated XML records contain no attributes or values to form columns.",
        )
    for values in record_rows:
        for name in ordered_names:
            if name not in values:
                stats_by_name[name].missing_values += 1
            else:
                stats_by_name[name].observe(values[name], "string")

    columns = [stats_by_name[name].as_dict() for name in ordered_names]
    profiler = _profile_for_format("xml", limits)
    profile_table = profiler.add_table(None, columns)
    for values in record_rows:
        profile_table.observe(
            [values[name] if name in values else MISSING for name in ordered_names]
        )
    return ParseResult(
        detected_format="xml",
        columns=columns,
        row_count=len(records),
        metadata={
            "record_element": _xml_name(record_tag),
            "record_path": "/" + "/".join(path + [_xml_name(record_tag)]),
            "container_attributes": ancestors,
            "namespace_representation": "expanded Clark notation {uri}local",
            "nested_representation": "nested leaf paths use /; attributes use /@name",
            "missing_values": "reported separately from empty element values",
            "elements_scanned": element_count,
            "source_size_bytes": file_size,
        },
        profile_result=profiler.as_dict(),
    )


def parse_dataset_file(filename: str, stream: BinaryIO, file_size: int) -> ParseResult:
    extension = os.path.splitext(filename)[1].lower()
    detected_format = FORMAT_BY_EXTENSION.get(extension)
    if detected_format is None:
        _fail("unsupported_format", "This file format is not supported for parsing.")
    _check_size(file_size)
    limits = get_parse_limits()
    try:
        if detected_format in {"csv", "tsv"}:
            return _read_delimited(filename, stream, file_size, limits)
        if detected_format == "json":
            return _read_json(stream, limits)
        if detected_format == "xlsx":
            return _read_xlsx(stream, file_size, limits)
        if detected_format == "parquet":
            return _read_parquet(stream, file_size, limits)
        return _read_xml(stream, file_size, limits)
    except ParsingError:
        raise
