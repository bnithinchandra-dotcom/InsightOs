import copy
import hashlib
import json
import logging
import math
import os
import re
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any, Literal
import unicodedata
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from email.message import Message
from os import getenv

from fastapi import Body, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import MinioException, S3Error
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr
import pyarrow as pa
import pyarrow.parquet as pq
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send
from urllib3.exceptions import HTTPError

from app.database import SessionLocal
from app.execution import ExecutionBudget
from app.jev import (
    VerificationAssessment,
    evidence_for,
    policy_for,
    verify_claim,
)
from app.models import (
    Dataset,
    DatasetFile,
    EDAAnalysis,
    PreprocessingExecution,
    Project,
    SchemaAnalysis,
)
from app.eda import (
    EDAConfiguration,
    EDAError,
    EDAResult,
    analyze_normalized_file,
    eda_configuration_from_dict,
    _algorithm_version_for_analysis,
)

from app.normalization import (
    DEFAULT_MAX_OUTPUT_BYTES,
    NormalizationError,
    get_normalization_configuration,
    normalize_file,
    validate_normalization_result,
    verify_normalization_result,
    verify_normalization_result_detailed,
)
from app.parsing import (
    ParserConfigurationError,
    ParsingError,
    parse_dataset_file,
)
from app.preprocessing import (
    DEFAULT_TRANSFORMATION_REGISTRY,
    PreprocessingApproval,
    PreprocessingOrchestrationError,
    PreprocessingResult,
    PreprocessingStep,
    TransformationOperation,
    TransformationRegistryError,
    arrow_table_fingerprint,
    canonical_sha256,
    orchestrate_preprocessing,
    _validate_orchestration_table,
)
from app.provenance import schema_fingerprint
from app.provenance import build_provenance, canonical_sha256
from app.schema_architect import (
    MAX_COLUMNS_PER_TABLE as MAX_SCHEMA_COLUMNS,
    MAX_TABLES as MAX_SCHEMA_TABLES,
    MAX_TOTAL_ARTIFACT_BYTES,
    MAX_TOTAL_ROWS as MAX_SCHEMA_ROWS,
    MAX_RESULT_BYTES as MAX_SCHEMA_RESULT_BYTES,
    SCHEMA_ARCHITECT_VERSION,
    SchemaAnalysisError,
    SchemaTableInput,
    analyze_schema,
)
from app.quality_analysis import (
    QUALITY_ANALYSIS_CONFIGURATION,
    QualityAnalysisError,
    build_contract_report,
    normalized_output_provenance,
    normalized_summary_totals,
    quality_analysis_key,
    validate_quality_report,
)
from app.quality_catalog import (
    QUALITY_RULE_CATALOG_VERSION,
    get_quality_rule_catalog_entry,
    list_quality_rule_catalog,
)
from app.quality_planning import (
    QualityPlanningContext,
    QualityRulePlanResponse,
    plan_quality_rules,
)
from app.quality_rules import MAX_RULE_INPUT_BYTES, run_quality_rules
from app.service_health import check_database, check_redis, check_storage


logger = logging.getLogger(__name__)

_UPLOAD_CONNECTION_QUARANTINE: list[object] = []
_UPLOAD_CONNECTION_QUARANTINE_LOCK = Lock()

app = FastAPI(
    title="InsightOS API",
    version="0.1.0",
)

UPLOAD_CHUNK_SIZE = 1024 * 1024
UPLOAD_MIME_TYPES = {
    ".csv": {"text/csv", "application/csv", "text/plain"},
    ".tsv": {"text/tab-separated-values", "text/tsv", "text/plain"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
    ".json": {"application/json", "text/json"},
    ".parquet": {"application/vnd.apache.parquet", "application/x-parquet"},
    ".xml": {"application/xml", "text/xml"},
}


class UploadFilenameSafetyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not re.fullmatch(r"/api/v1/datasets/\d+/files", scope["path"])
        ):
            await self.app(scope, receive, send)
            return

        headers = {name.lower(): value for name, value in scope["headers"]}
        content_type = headers.get(b"content-type", b"").decode("latin-1")
        message = Message()
        message["content-type"] = content_type
        boundary = message.get_param("boundary", header="content-type")
        if not boundary:
            await self.app(scope, receive, send)
            return

        encoded_boundary = str(boundary).encode("latin-1")
        initial_boundary = b"--" + encoded_boundary + b"\r\n"
        part_boundary = b"\r\n--" + encoded_boundary
        buffer = bytearray()
        parser_state = "initial"
        unsafe_filename = False

        async def inspect_receive():
            nonlocal parser_state, unsafe_filename
            request_message = await receive()
            if request_message["type"] != "http.request" or unsafe_filename:
                return request_message

            buffer.extend(request_message.get("body", b""))
            while True:
                if parser_state == "initial":
                    boundary_index = buffer.find(initial_boundary)
                    if boundary_index < 0:
                        if len(buffer) > len(initial_boundary) + 16_384:
                            unsafe_filename = True
                        break
                    del buffer[: boundary_index + len(initial_boundary)]
                    parser_state = "headers"

                if parser_state == "headers":
                    header_end = buffer.find(b"\r\n\r\n")
                    if header_end < 0:
                        if len(buffer) > 16_384:
                            unsafe_filename = True
                        break

                    part_headers = bytes(buffer[:header_end])
                    del buffer[: header_end + 4]
                    for line in part_headers.split(b"\r\n"):
                        if not line.lower().startswith(b"content-disposition:"):
                            continue
                        match = re.search(
                            rb"(?:^|;)\s*filename\s*=\s*(?:\"((?:\\.|[^\"])*)\"|([^;]*))",
                            line.partition(b":")[2],
                            flags=re.IGNORECASE,
                        )
                        if match is not None:
                            filename = match.group(1) or match.group(2) or b""
                            if b"/" in filename or b"\\" in filename:
                                unsafe_filename = True
                    parser_state = "body"

                if parser_state == "body":
                    boundary_index = buffer.find(part_boundary)
                    if boundary_index < 0:
                        del buffer[: max(0, len(buffer) - len(part_boundary) - 2)]
                        break
                    trailer_start = boundary_index + len(part_boundary)
                    if len(buffer) < trailer_start + 2:
                        del buffer[:boundary_index]
                        break

                    trailer = bytes(buffer[trailer_start : trailer_start + 2])
                    del buffer[: trailer_start + 2]
                    if trailer == b"--":
                        parser_state = "done"
                        buffer.clear()
                        break
                    if trailer != b"\r\n":
                        unsafe_filename = True
                        break
                    parser_state = "headers"

                if parser_state == "done" or unsafe_filename:
                    break

            if unsafe_filename:
                scope.setdefault("state", {})["unsafe_upload_filename"] = True
            return request_message

        await self.app(scope, inspect_receive, send)


app.add_middleware(UploadFilenameSafetyMiddleware)


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class QualityAnalysisRequest(BaseModel):
    recompute: bool = False


class SchemaFileSelection(BaseModel):
    file_id: StrictInt = Field(gt=0)
    table_indices: list[StrictInt] | None = Field(default=None, max_length=MAX_SCHEMA_TABLES)

    class Config:
        extra = "forbid"


class SchemaAnalysisRequest(BaseModel):
    files: list[SchemaFileSelection] | None = Field(default=None, max_length=MAX_SCHEMA_TABLES)
    business_context: StrictStr | None = Field(default=None, max_length=5000)
    recompute: StrictBool = False

    class Config:
        extra = "forbid"


class InvalidParsingResultError(ValueError):
    pass


class InvalidProfileResultError(ValueError):
    pass


def get_storage_client() -> Minio:
    return Minio(
        getenv("MINIO_ENDPOINT", "minio:9000"),
        access_key=getenv("MINIO_ACCESS_KEY"),
        secret_key=getenv("MINIO_SECRET_KEY"),
        secure=getenv("MINIO_SECURE", "false").lower() in {"1", "true", "yes"},
    )


def get_max_dataset_size_bytes() -> int:
    try:
        size_mb = float(getenv("MAX_DATASET_SIZE_MB", "150"))
    except ValueError:
        logger.error("MAX_DATASET_SIZE_MB is not a valid number")
        raise HTTPException(
            status_code=500,
            detail="File upload is unavailable due to server configuration.",
        ) from None

    if not math.isfinite(size_mb) or size_mb <= 0:
        logger.error("MAX_DATASET_SIZE_MB must be a positive finite number")
        raise HTTPException(
            status_code=500,
            detail="File upload is unavailable due to server configuration.",
        )

    return int(size_mb * 1024 * 1024)


def validate_parsing_result(filename: str, result, file_size: int) -> dict:
    expected_format = filename.rsplit(".", 1)[-1].lower()
    try:
        parsed = result if isinstance(result, dict) else result.as_dict()
        if (
            parsed["detected_format"] != expected_format
            or not isinstance(parsed["columns"], list)
            or not isinstance(parsed["row_count"], int)
            or isinstance(parsed["row_count"], bool)
            or parsed["row_count"] < 0
            or not isinstance(parsed["metadata"], dict)
        ):
            raise ValueError("Parser returned inconsistent top-level metadata.")

        positions = set()
        for column in parsed["columns"]:
            if not isinstance(column, dict):
                raise ValueError("Parser returned an invalid column descriptor.")
            name = column.get("name")
            position = column.get("position")
            physical_type = column.get("physical_type")
            physical_types = column.get("physical_types")
            table = column.get("table")
            if (
                not isinstance(name, str)
                or not isinstance(position, int)
                or isinstance(position, bool)
                or position < 0
                or (table is not None and not isinstance(table, str))
                or (table, position) in positions
                or not isinstance(physical_type, str)
                or not physical_type
                or not isinstance(physical_types, list)
                or any(not isinstance(value, str) for value in physical_types)
            ):
                raise ValueError("Parser returned invalid column metadata.")
            positions.add((table, position))

        if file_size <= 0:
            raise ValueError("An empty upload cannot be successfully parsed.")
        # Validate that metadata can be safely persisted by the configured JSON column.
        return json.loads(json.dumps(parsed, allow_nan=False))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise InvalidParsingResultError(
            "Parser returned incomplete or invalid metadata."
        ) from error


def validate_profile_result(result, parsing_result: dict) -> dict:
    try:
        if not isinstance(result, dict):
            raise ValueError("Profile result must be an object.")
        tables = result["tables"]
        if (
            not isinstance(result.get("version"), int)
            or isinstance(result["version"], bool)
            or result["version"] != 1
            or result.get("detected_format") != parsing_result["detected_format"]
            or not isinstance(result.get("row_count"), int)
            or isinstance(result["row_count"], bool)
            or result["row_count"] < 0
            or result["row_count"] != parsing_result["row_count"]
            or not isinstance(result.get("column_count"), int)
            or isinstance(result["column_count"], bool)
            or result["column_count"] < 0
            or not isinstance(tables, list)
            or not tables
            or not isinstance(result.get("warnings"), list)
            or any(not isinstance(warning, str) for warning in result["warnings"])
            or not isinstance(result.get("unsupported_analyses"), list)
            or any(
                not isinstance(explanation, str)
                for explanation in result["unsupported_analyses"]
            )
        ):
            raise ValueError("Profile result metadata is inconsistent.")

        row_count = 0
        column_count = 0
        for table in tables:
            if (
                not isinstance(table, dict)
                or not isinstance(table.get("row_count"), int)
                or isinstance(table["row_count"], bool)
                or table["row_count"] < 0
                or not isinstance(table.get("column_count"), int)
                or isinstance(table["column_count"], bool)
                or table["column_count"] < 0
                or not isinstance(table.get("columns"), list)
                or table["column_count"] != len(table["columns"])
                or table.get("name") is not None
                and not isinstance(table["name"], str)
                or not isinstance(table.get("duplicate_row_count_exact"), bool)
                or table.get("duplicate_row_count") is not None
                and (
                    not isinstance(table["duplicate_row_count"], int)
                    or isinstance(table["duplicate_row_count"], bool)
                    or table["duplicate_row_count"] < 0
                )
                or table["duplicate_row_count_exact"]
                != (table.get("duplicate_row_count") is not None)
            ):
                raise ValueError("Profile result contains an invalid table.")
            row_count += table["row_count"]
            column_count += len(table["columns"])
            for column in table["columns"]:
                if (
                    not isinstance(column, dict)
                    or not isinstance(column.get("name"), str)
                    or not isinstance(column.get("position"), int)
                    or isinstance(column["position"], bool)
                    or column["position"] < 0
                    or not isinstance(column.get("physical_types"), list)
                    or any(
                        not isinstance(value, str)
                        for value in column["physical_types"]
                    )
                    or any(
                        not isinstance(column.get(count), int)
                        or isinstance(column[count], bool)
                        or column[count] < 0
                        for count in (
                            "missing_value_count",
                            "empty_value_count",
                        )
                    )
                    or column.get("distinct_value_count") is not None
                    and (
                        not isinstance(column["distinct_value_count"], int)
                        or isinstance(column["distinct_value_count"], bool)
                        or column["distinct_value_count"] < 0
                    )
                    or not isinstance(column.get("distinct_count_exact"), bool)
                    or not isinstance(column.get("categorical_summary_complete"), bool)
                    or column.get("categorical_summary") is not None
                    and (
                        not isinstance(column["categorical_summary"], list)
                        or len(column["categorical_summary"]) > 10
                    )
                    or column["distinct_count_exact"]
                    != (column.get("distinct_value_count") is not None)
                    or column["categorical_summary_complete"]
                    != (column.get("categorical_summary") is not None)
                    or column.get("numeric_statistics") is not None
                    and not isinstance(column["numeric_statistics"], dict)
                ):
                    raise ValueError("Profile result contains an invalid column.")
                numeric_statistics = column.get("numeric_statistics")
                if numeric_statistics is not None and (
                    not isinstance(numeric_statistics.get("median_exact"), bool)
                    or any(
                        numeric_statistics.get(statistic) is not None
                        and not isinstance(numeric_statistics[statistic], (int, float))
                        for statistic in ("minimum", "maximum", "mean", "median")
                    )
                ):
                    raise ValueError("Profile result contains invalid numeric statistics.")
                categorical = column.get("categorical_summary")
                if categorical is not None and any(
                    not isinstance(value, dict)
                    or not isinstance(value.get("value"), str)
                    or not isinstance(value.get("count"), int)
                    or isinstance(value["count"], bool)
                    or value["count"] <= 0
                    or not isinstance(value.get("value_truncated"), bool)
                    for value in categorical
                ):
                    raise ValueError("Profile result contains an invalid categorical summary.")
        if (
            row_count != result["row_count"]
            or column_count != result.get("column_count")
            or column_count != len(parsing_result["columns"])
        ):
            raise ValueError("Profile counts do not match the parsing result.")

        if parsing_result["detected_format"] == "xlsx":
            worksheets = parsing_result["metadata"].get("worksheets")
            if not isinstance(worksheets, list) or len(worksheets) != len(tables):
                raise ValueError("Profile tables do not match the parsed worksheets.")
            parsed_columns_by_table = {}
            for column in parsing_result["columns"]:
                table_name = column.get("table")
                if not isinstance(table_name, str):
                    raise ValueError("Parsed XLSX columns must identify their worksheet.")
                parsed_columns_by_table.setdefault(table_name, []).append(column)

            expected_tables = []
            worksheet_names = set()
            for worksheet in worksheets:
                if (
                    not isinstance(worksheet, dict)
                    or not isinstance(worksheet.get("name"), str)
                    or not isinstance(worksheet.get("row_count"), int)
                    or isinstance(worksheet["row_count"], bool)
                    or worksheet["row_count"] < 0
                    or not isinstance(worksheet.get("columns"), list)
                ):
                    raise ValueError("Parsed worksheet metadata is invalid.")
                worksheet_name = worksheet["name"]
                if worksheet_name in worksheet_names:
                    raise ValueError("Parsed worksheet names must be unique.")
                worksheet_names.add(worksheet_name)
                parsed_columns = parsed_columns_by_table.pop(
                    worksheet_name, []
                )
                worksheet_columns = worksheet["columns"]
                if len(worksheet_columns) != len(parsed_columns):
                    raise ValueError(
                        "Worksheet columns do not match the parsing result."
                    )
                for worksheet_column, parsed_column in zip(
                    worksheet_columns, parsed_columns
                ):
                    if not isinstance(worksheet_column, dict) or any(
                        worksheet_column.get(key) != parsed_column.get(key)
                        for key in (
                            "name",
                            "position",
                            "physical_types",
                            "missing_values",
                            "empty_values",
                        )
                    ):
                        raise ValueError(
                            "Worksheet columns do not match the parsing result."
                        )
                expected_tables.append(
                    (
                        worksheet_name,
                        worksheet["row_count"],
                        parsed_columns,
                    )
                )
            if parsed_columns_by_table:
                raise ValueError("Parsed columns refer to unknown worksheets.")
        else:
            expected_tables = [
                (None, parsing_result["row_count"], parsing_result["columns"])
            ]

        if len(tables) != len(expected_tables):
            raise ValueError("Profile tables do not match the parsing result.")
        for table, (expected_name, expected_rows, expected_columns) in zip(
            tables, expected_tables
        ):
            if (
                table["name"] != expected_name
                or table["row_count"] != expected_rows
                or len(table["columns"]) != len(expected_columns)
            ):
                raise ValueError("Profile tables do not match the parsing result.")
            for profile_column, parsed_column in zip(
                table["columns"], expected_columns
            ):
                if (
                    profile_column["name"] != parsed_column["name"]
                    or profile_column["position"] != parsed_column["position"]
                    or profile_column["physical_types"]
                    != parsed_column["physical_types"]
                ):
                    raise ValueError(
                        "Profile columns do not match the parsing result."
                    )
                for profile_key, parsing_key in (
                    ("missing_value_count", "missing_values"),
                    ("empty_value_count", "empty_values"),
                ):
                    parsed_count = parsed_column.get(parsing_key)
                    if (
                        parsed_count is not None
                        and profile_column[profile_key] != parsed_count
                    ):
                        raise ValueError(
                            "Profile value counts do not match the parsing result."
                        )
        return json.loads(json.dumps(result, allow_nan=False))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise InvalidProfileResultError(
            "Profiler returned incomplete or invalid profile metadata."
        ) from error


def _file_response(dataset: Dataset, dataset_file: DatasetFile) -> dict:
    return {
        "dataset_id": dataset.id,
        "dataset_status": dataset.status,
        "file": {
            "id": dataset_file.id,
            "filename": dataset_file.filename,
            "storage_key": dataset_file.storage_key,
            "file_size_bytes": dataset_file.file_size_bytes,
            "mime_type": dataset_file.mime_type,
            "checksum": dataset_file.checksum,
            "detected_format": dataset_file.detected_format,
            "parsing_result": dataset_file.parsing_result,
            "profile_result": dataset_file.profile_result,
            "normalization_status": dataset_file.normalization_status,
            "normalization_result": dataset_file.normalization_result,
            "normalization_error_code": dataset_file.normalization_error_code,
            "normalization_error_message": dataset_file.normalization_error_message,
            "status": dataset_file.status,
            "error_code": dataset_file.error_code,
            "error_message": dataset_file.error_message,
        },
    }


def _mark_ingestion_failed(
    dataset_id: int,
    dataset_file_id: int,
    idempotency_key: str,
    session: Session,
    error_code: str,
    error_message: str,
    *,
    retryable: bool = False,
) -> None:
    try:
        session.rollback()
        dataset = session.get(Dataset, dataset_id)
        dataset_file = session.get(DatasetFile, dataset_file_id)
        if dataset is None or dataset_file is None:
            raise SQLAlchemyError("Ingestion records disappeared during processing.")
        dataset_file.status = "Processing" if retryable else "Failed"
        dataset_file.error_code = error_code
        dataset_file.error_message = error_message
        dataset.status = "Processing" if retryable else "Failed"
        dataset.active_upload_key = idempotency_key if retryable else None
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.error(
            "Could not persist failure state for dataset %s (%s)",
            dataset_id,
            type(error).__name__,
        )


def _upload_lock_id(dataset_id: int, idempotency_key: str) -> int:
    lock_identity = f"{dataset_id}:{idempotency_key}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(lock_identity).digest()[:8], "big", signed=True)


class _UploadConnectionDisposalError(RuntimeError):
    def __init__(
        self,
        connection,
        cleanup_error: BaseException,
        invalidate_error: BaseException,
        detach_error: BaseException,
        physical_close_error: BaseException,
    ) -> None:
        self.connection = connection
        self.cleanup_error = cleanup_error
        self.invalidate_error = invalidate_error
        self.detach_error = detach_error
        self.physical_close_error = physical_close_error
        self.session_close_error = None
        super().__init__(
            "Could not safely dispose the connection holding an upload "
            "advisory lock; it remains quarantined and checked out."
        )


def _quarantine_upload_connection(connection) -> None:
    with _UPLOAD_CONNECTION_QUARANTINE_LOCK:
        if not any(
            quarantined is connection
            for quarantined in _UPLOAD_CONNECTION_QUARANTINE
        ):
            _UPLOAD_CONNECTION_QUARANTINE.append(connection)


def _upload_connection_was_discarded(connection) -> bool:
    try:
        if connection.invalidated:
            return True
        proxied_connection = connection.connection
        return not proxied_connection.is_valid or proxied_connection.is_detached
    except BaseException:
        return False


def _discard_upload_connection(connection, error: BaseException) -> None:
    invalidation_error = None
    detachment_error = None
    try:
        connection.invalidate(error)
        return
    except BaseException as error_during_invalidation:
        invalidation_error = error_during_invalidation
        logger.error(
            "Could not invalidate connection holding upload lock (%s)",
            type(invalidation_error).__name__,
        )
    try:
        connection.detach()
        return
    except BaseException as error_during_detachment:
        detachment_error = error_during_detachment
        logger.error(
            "Could not detach connection holding upload lock (%s)",
            type(detachment_error).__name__,
        )

    if _upload_connection_was_discarded(connection):
        return

    try:
        connection.connection.dbapi_connection.close()
    except BaseException as physical_close_error:
        _quarantine_upload_connection(connection)
        logger.critical(
            "Could not physically close connection with uncertain upload lock "
            "ownership (%s); connection remains checked out",
            type(physical_close_error).__name__,
        )
        raise _UploadConnectionDisposalError(
            connection,
            error,
            invalidation_error,
            detachment_error,
            physical_close_error,
        ) from error
    logger.critical(
        "SQLAlchemy invalidation and detachment failed; closed the underlying "
        "DBAPI connection to release the upload lock"
    )


@contextmanager
def _pinned_upload_session():
    session = SessionLocal()
    connection = None
    unsafe_disposal = None
    try:
        bind = session.get_bind()
        connection = bind.connect()
        session.bind = connection
        yield session, connection
    except _UploadConnectionDisposalError as error:
        unsafe_disposal = error
        raise
    finally:
        try:
            session.close()
        except BaseException as error:
            if unsafe_disposal is None:
                raise
            unsafe_disposal.session_close_error = error
            logger.critical(
                "Could not close upload session after connection disposal "
                "failed (%s)",
                type(error).__name__,
            )
        finally:
            if connection is not None:
                if unsafe_disposal is None:
                    connection.close()
                else:
                    logger.critical(
                        "Leaving uncertain upload-lock connection checked out "
                        "to prevent pool reuse"
                    )


def _try_acquire_upload_lock(
    session: Session,
    connection,
    dataset_id: int,
    key: str,
) -> int | None:
    if session.get_bind() is not connection:
        raise RuntimeError("Upload session is not bound to its pinned connection.")
    if session.get_bind().dialect.name != "postgresql":
        raise HTTPException(
            status_code=503,
            detail="Upload concurrency protection requires PostgreSQL.",
        )

    lock_id = _upload_lock_id(dataset_id, key)
    try:
        acquired = session.execute(
            sa.select(sa.func.pg_try_advisory_lock(lock_id))
        ).scalar_one()
    except BaseException as error:
        _discard_upload_connection(connection, error)
        raise
    return lock_id if acquired else None


def _release_upload_lock(session: Session, connection, lock_id: int) -> None:
    try:
        if session.get_bind() is not connection:
            raise RuntimeError("Upload session lost its pinned connection.")
        unlocked = session.scalar(sa.select(sa.func.pg_advisory_unlock(lock_id)))
        if unlocked is not True:
            raise RuntimeError("PostgreSQL did not confirm advisory-lock release.")
        session.commit()
    except SQLAlchemyError as error:
        logger.error(
            "Could not release upload advisory lock (%s)",
            type(error).__name__,
        )
        _discard_upload_connection(connection, error)
        raise RuntimeError(
            "Could not release PostgreSQL advisory lock; connection was discarded."
        ) from error
    except BaseException as error:
        logger.error(
            "Could not confirm upload advisory-lock release (%s)",
            type(error).__name__,
        )
        _discard_upload_connection(connection, error)
        if not isinstance(error, Exception):
            raise
        raise RuntimeError(
            "Could not confirm PostgreSQL advisory-lock release; connection was discarded."
        ) from error


def _object_is_missing(error: S3Error) -> bool:
    return error.code in {"NoSuchKey", "NoSuchObject"}


def _verify_stored_object(storage, bucket: str, dataset_file: DatasetFile) -> bool:
    try:
        stored = storage.stat_object(bucket, dataset_file.storage_key)
    except S3Error as error:
        if _object_is_missing(error):
            return False
        raise

    metadata = {
        str(key).lower(): str(value)
        for key, value in (getattr(stored, "metadata", None) or {}).items()
    }
    stored_checksum = metadata.get("x-amz-meta-sha256", metadata.get("sha256"))
    if (
        stored.size != dataset_file.file_size_bytes
        or stored_checksum != dataset_file.checksum
    ):
        return False

    response = storage.get_object(bucket, dataset_file.storage_key)
    checksum = hashlib.sha256()
    file_size = 0
    try:
        while chunk := response.read(UPLOAD_CHUNK_SIZE):
            file_size += len(chunk)
            if file_size > dataset_file.file_size_bytes:
                return False
            checksum.update(chunk)
    finally:
        response.close()
        release_connection = getattr(response, "release_conn", None)
        if release_connection is not None:
            release_connection()
    return file_size == dataset_file.file_size_bytes and checksum.hexdigest() == (
        dataset_file.checksum
    )


def _materialize_quality_parquet_inputs(storage, bucket: str, normalized: dict, cleanup):
    tables = normalized["tables"]
    total_size = sum(
        table["file_size_bytes"]
        for table in tables
        if table["object_key"] is not None
    )
    if total_size > MAX_RULE_INPUT_BYTES:
        return {}, (
            "Normalized Parquet exceeds the configured quality-analysis input "
            "limit; value-scanning rules were not run."
        )

    paths = {}
    for table in tables:
        table_index = table["table_index"]
        object_key = table["object_key"]
        if object_key is None:
            paths[table_index] = None
            continue

        output = tempfile.NamedTemporaryFile(
            prefix="insightos-quality-",
            suffix=".parquet",
            delete=False,
        )
        path = output.name
        cleanup.callback(os.unlink, path)
        response = storage.get_object(bucket, object_key)
        digest = hashlib.sha256()
        size = 0
        try:
            while chunk := response.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > table["file_size_bytes"] or size > MAX_RULE_INPUT_BYTES:
                    raise OSError("Normalized quality input exceeded its verified size.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
        finally:
            output.close()
            try:
                response.close()
            finally:
                release_connection = getattr(response, "release_conn", None)
                if release_connection is not None:
                    release_connection()
        if size != table["file_size_bytes"] or digest.hexdigest() != table["sha256"]:
            raise QualityAnalysisError(
                "normalized_output_integrity_failed",
                "Stored normalized output changed while preparing quality analysis.",
            )
        paths[table_index] = path
    return paths, None


def get_upload_filename(filename: str | None) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="A valid filename is required.")

    clean_filename = unicodedata.normalize("NFC", filename)
    if (
        clean_filename != clean_filename.strip()
        or clean_filename in {".", ".."}
        or len(clean_filename) > 255
        or clean_filename.endswith((".", " "))
        or any(
            character in clean_filename
            for character in '/\\<>:"|?*'
        )
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in clean_filename
        )
    ):
        raise HTTPException(status_code=400, detail="A valid filename is required.")
    return clean_filename


def validate_upload_type(filename: str, content_type: str | None) -> None:
    extension = filename.rpartition(".")[2].lower()
    extension = f".{extension}" if extension else ""
    accepted_types = UPLOAD_MIME_TYPES.get(extension)
    if accepted_types is None:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file extension. Accepted formats are CSV, TSV, XLSX, JSON, Parquet, and XML.",
        )

    if content_type is None:
        return

    supplied_type = content_type.partition(";")[0].strip().lower()
    if (
        len(content_type) > 255
        or (
            supplied_type != "application/octet-stream"
            and supplied_type not in accepted_types
        )
    ):
        raise HTTPException(
            status_code=415,
            detail="The supplied MIME type does not match the filename extension.",
        )


@app.post("/api/v1/projects/{project_id}/datasets", status_code=201)
def create_dataset(project_id: int, payload: DatasetCreate):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Dataset name cannot be blank.")

    with SessionLocal() as session:
        try:
            project = session.get(Project, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found.")

            dataset = Dataset(
                project_id=project.id,
                name=name,
                description=payload.description,
                status="Created",
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            session.rollback()
            logger.error("Dataset creation failed (%s)", type(error).__name__)
            raise HTTPException(
                status_code=500,
                detail="Dataset could not be created.",
            ) from None

        return {
            "id": dataset.id,
            "project_id": dataset.project_id,
            "name": dataset.name,
            "description": dataset.description,
            "status": dataset.status,
        }


@app.post("/api/v1/datasets/{dataset_id}/files", status_code=201)
def upload_dataset_file(
    dataset_id: int,
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if getattr(request.state, "unsafe_upload_filename", False):
        raise HTTPException(
            status_code=400,
            detail="Path separators are not allowed in filenames.",
        )

    filename = get_upload_filename(file.filename)
    validate_upload_type(filename, file.content_type)
    if idempotency_key is not None and (
        not idempotency_key.strip() or len(idempotency_key) > 255
    ):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 1 to 255 characters.",
        )

    max_size_bytes = get_max_dataset_size_bytes()
    checksum = hashlib.sha256()
    file_size = 0

    with _pinned_upload_session() as (session, connection), ExitStack() as cleanup:
        try:
            dataset = session.get(Dataset, dataset_id)
        except SQLAlchemyError as error:
            logger.error(
                "Dataset lookup failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset could not be checked.",
            ) from None

        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found.")

        try:
            while chunk := file.file.read(UPLOAD_CHUNK_SIZE):
                file_size += len(chunk)
                if file_size > max_size_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="File exceeds the maximum dataset size.",
                    )
                checksum.update(chunk)
            file.file.seek(0)
        except (OSError, ValueError) as error:
            logger.warning(
                "Uploaded file could not be read (%s)",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=400,
                detail="Uploaded file could not be read.",
            ) from None

        file_checksum = checksum.hexdigest()
        request_identity = (
            f"client:{idempotency_key}"
            if idempotency_key is not None
            else f"content:{filename}:{file_checksum}"
        )
        upload_key = hashlib.sha256(request_identity.encode("utf-8")).hexdigest()

        try:
            lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                upload_key,
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve upload for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Upload concurrency protection is temporarily unavailable.",
            ) from None
        if lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "upload_in_progress",
                    "message": "An upload with this idempotency key is already in progress.",
                },
            )
        cleanup.callback(_release_upload_lock, session, connection, lock_id)

        try:
            dataset = (
                session.query(Dataset)
                .filter(Dataset.id == dataset_id)
                .with_for_update()
                .one_or_none()
            )
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Could not lock dataset %s for upload (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Upload could not be started.",
            ) from None

        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found.")

        try:
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.dataset_id == dataset_id,
                    DatasetFile.idempotency_key == upload_key,
                )
                .one_or_none()
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not check upload retry for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Upload could not be checked.",
            ) from None

        if dataset_file is None and idempotency_key is None:
            try:
                dataset_file = (
                    session.query(DatasetFile)
                    .filter(
                        DatasetFile.dataset_id == dataset_id,
                        DatasetFile.idempotency_key.is_(None),
                        DatasetFile.filename == filename,
                        DatasetFile.file_size_bytes == file_size,
                        DatasetFile.checksum == file_checksum,
                    )
                    .order_by(DatasetFile.id.asc())
                    .first()
                )
            except SQLAlchemyError as error:
                logger.error(
                    "Could not find a legacy upload for dataset %s (%s)",
                    dataset_id,
                    type(error).__name__,
                )
                raise HTTPException(
                    status_code=500,
                    detail="Upload could not be checked.",
                ) from None

        if dataset_file is not None and (
            dataset_file.filename != filename
            or dataset_file.file_size_bytes != file_size
            or dataset_file.checksum != file_checksum
        ):
            raise HTTPException(
                status_code=409,
                detail="This idempotency key was already used for different file contents.",
            )

        if dataset_file is not None and dataset_file.status == "Ready":
            bucket = getenv("MINIO_RAW_BUCKET", "insightos-raw")
            try:
                storage = get_storage_client()
                object_valid = _verify_stored_object(storage, bucket, dataset_file)
            except (MinioException, HTTPError, OSError) as error:
                logger.error(
                    "Stored object verification failed for dataset %s (%s)",
                    dataset_id,
                    type(error).__name__,
                )
                raise HTTPException(
                    status_code=502,
                    detail="The previously uploaded file could not be verified.",
                ) from None
            if object_valid:
                return _file_response(dataset, dataset_file)

        if dataset.active_upload_key not in (None, upload_key):
            raise HTTPException(
                status_code=409,
                detail="Another upload is currently being processed for this dataset.",
            )

        if dataset_file is None:
            dataset_file = DatasetFile(
                dataset_id=dataset_id,
                filename=filename,
                storage_key=f"{dataset_id}/{upload_key}/{filename}",
                file_size_bytes=file_size,
                checksum=file_checksum,
                mime_type=file.content_type,
                status="Processing",
                idempotency_key=upload_key,
            )
            session.add(dataset_file)
        else:
            dataset_file.status = "Processing"
            dataset_file.idempotency_key = upload_key
            dataset_file.error_code = None
            dataset_file.error_message = None
        dataset.active_upload_key = upload_key
        dataset.status = "Uploading"
        try:
            session.commit()
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Could not persist upload state for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Upload could not be started.",
            ) from None
        dataset_file_id = dataset_file.id

        if dataset_file.parsing_result is not None:
            try:
                parsing_data = validate_parsing_result(
                    filename,
                    dataset_file.parsing_result,
                    file_size,
                )
            except ValueError:
                _mark_ingestion_failed(
                    dataset_id,
                    dataset_file_id,
                    upload_key,
                    session,
                    "invalid_persisted_metadata",
                    "Previously saved parsing metadata failed validation.",
                )
                raise HTTPException(
                    status_code=500,
                    detail="Previously saved parsing metadata is invalid.",
                ) from None
        else:
            parsing_data = None

        parsing_result = None
        try:
            if parsing_data is None or dataset_file.profile_result is None:
                parsing_result = parse_dataset_file(filename, file.file, file_size)
                file.file.seek(0)
                if parsing_data is None:
                    parsing_data = validate_parsing_result(
                        filename,
                        parsing_result,
                        file_size,
                    )
                profile_data = validate_profile_result(
                    getattr(parsing_result, "profile_result", None),
                    parsing_data,
                )
            else:
                profile_data = validate_profile_result(
                    dataset_file.profile_result,
                    parsing_data,
                )
        except InvalidParsingResultError as error:
            _mark_ingestion_failed(
                dataset_id,
                dataset_file_id,
                upload_key,
                session,
                "invalid_parsing_metadata",
                "The parser could not produce valid metadata for this file.",
            )
            logger.error(
                "Parser returned invalid metadata for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="The parser returned invalid metadata.",
            ) from None
        except InvalidProfileResultError as error:
            _mark_ingestion_failed(
                dataset_id,
                dataset_file_id,
                upload_key,
                session,
                "invalid_profile_metadata",
                "The profiler could not produce valid metadata for this file.",
            )
            logger.error(
                "Profiler returned invalid metadata for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="The profiler returned invalid metadata.",
            ) from None
        except ParsingError as error:
            _mark_ingestion_failed(
                dataset_id,
                dataset_file_id,
                upload_key,
                session,
                error.code,
                error.message,
            )
            raise HTTPException(
                status_code=422,
                detail={"code": error.code, "message": error.message},
            ) from None
        except ParserConfigurationError as error:
            _mark_ingestion_failed(
                dataset_id,
                dataset_file_id,
                upload_key,
                session,
                "parser_configuration_error",
                "File parsing is unavailable due to server configuration.",
            )
            logger.error("Dataset parser configuration is invalid (%s)", error)
            raise HTTPException(
                status_code=500,
                detail="File parsing is unavailable due to server configuration.",
            ) from None
        except (OSError, ValueError) as error:
            _mark_ingestion_failed(
                dataset_id,
                dataset_file_id,
                upload_key,
                session,
                "invalid_parsing_metadata",
                "The parser could not produce valid metadata for this file.",
            )
            logger.warning(
                "Uploaded file could not be parsed (%s)",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=400,
                detail="Uploaded file could not be read for parsing.",
            ) from None

        dataset_file = session.get(DatasetFile, dataset_file_id)
        dataset_file.parsing_result = parsing_data
        dataset_file.detected_format = parsing_data["detected_format"]
        dataset_file.profile_result = profile_data
        dataset.status = "Processing"
        try:
            session.commit()
        except SQLAlchemyError as error:
            logger.error(
                "Could not persist parsing metadata for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            _mark_ingestion_failed(
                dataset_id,
                dataset_file_id,
                upload_key,
                session,
                "metadata_persistence_failed",
                "Parsed metadata could not be saved; retry the same upload.",
                retryable=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Parsed metadata could not be saved.",
            ) from None

        bucket = getenv("MINIO_RAW_BUCKET", "insightos-raw")
        storage = None
        object_upload_started = False
        try:
            storage = get_storage_client()
            if not storage.bucket_exists(bucket):
                storage.make_bucket(bucket)

            if not _verify_stored_object(storage, bucket, dataset_file):
                try:
                    storage.stat_object(bucket, dataset_file.storage_key)
                except S3Error as missing_error:
                    if not _object_is_missing(missing_error):
                        raise
                else:
                    object_upload_started = True
                    storage.remove_object(bucket, dataset_file.storage_key)

                file.file.seek(0)
                object_upload_started = True
                storage.put_object(
                    bucket,
                    dataset_file.storage_key,
                    file.file,
                    file_size,
                    content_type=file.content_type or "application/octet-stream",
                    metadata={"sha256": file_checksum},
                )

            if not _verify_stored_object(storage, bucket, dataset_file):
                raise ValueError("Stored object integrity verification failed.")

            dataset = session.get(Dataset, dataset_id)
            dataset_file = session.get(DatasetFile, dataset_file_id)
            dataset_file.status = "Ready"
            dataset_file.error_code = None
            dataset_file.error_message = None
            dataset.status = "Uploaded"
            dataset.active_upload_key = None
            session.commit()
        except (MinioException, HTTPError, OSError, ValueError) as error:
            session.rollback()
            cleanup_succeeded = not object_upload_started
            if object_upload_started and storage is not None:
                try:
                    storage.remove_object(bucket, dataset_file.storage_key)
                    cleanup_succeeded = True
                except (MinioException, HTTPError, OSError) as cleanup_error:
                    logger.error(
                        "Could not clean up failed upload for dataset %s (%s)",
                        dataset_id,
                        type(cleanup_error).__name__,
                    )
            _mark_ingestion_failed(
                dataset_id,
                dataset_file_id,
                upload_key,
                session,
                "storage_failure",
                (
                    "Object storage failed; retry this upload."
                    if cleanup_succeeded
                    else "Object cleanup could not be confirmed; retry this upload."
                ),
                retryable=not cleanup_succeeded,
            )
            logger.error(
                "Object storage upload failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            if not cleanup_succeeded:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "storage_cleanup_unconfirmed",
                        "message": "File upload failed and object cleanup could not be confirmed; retry the same upload.",
                    },
                ) from None
            raise HTTPException(
                status_code=502,
                detail="File could not be stored.",
            ) from None
        except SQLAlchemyError as error:
            session.rollback()
            if storage is not None:
                try:
                    persisted = session.get(DatasetFile, dataset_file_id)
                    dataset = session.get(Dataset, dataset_id)
                    if (
                        persisted is not None
                        and persisted.status == "Ready"
                        and dataset is not None
                    ):
                        if _verify_stored_object(storage, bucket, persisted):
                            return _file_response(dataset, persisted)
                except (
                    MinioException,
                    HTTPError,
                    OSError,
                    SQLAlchemyError,
                ) as verification_error:
                    logger.error(
                        "Could not reconcile dataset %s after a database error (%s)",
                        dataset_id,
                        type(verification_error).__name__,
                    )

            cleanup_succeeded = not object_upload_started
            if object_upload_started and storage is not None:
                try:
                    storage.remove_object(bucket, dataset_file.storage_key)
                    cleanup_succeeded = True
                except (MinioException, HTTPError, OSError) as cleanup_error:
                    logger.error(
                        "Could not clean up upload for dataset %s (%s)",
                        dataset_id,
                        type(cleanup_error).__name__,
                    )
            _mark_ingestion_failed(
                dataset_id,
                dataset_file_id,
                upload_key,
                session,
                "metadata_persistence_failed",
                (
                    "File metadata could not be saved; retry the same upload."
                    if cleanup_succeeded
                    else "File metadata could not be saved and object cleanup is unconfirmed; retry the same upload."
                ),
                retryable=True,
            )
            logger.error(
                "File metadata save failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            if not cleanup_succeeded:
                raise HTTPException(
                    status_code=500,
                    detail="File metadata could not be saved and object cleanup could not be confirmed; retry the same upload.",
                ) from None
            raise HTTPException(
                status_code=500,
                detail="File metadata could not be saved; retry the same upload.",
            ) from None

        return _file_response(dataset, dataset_file)


def serialize_project(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "user_id": project.user_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def serialize_dataset(dataset: Dataset) -> dict:
    return {
        "id": dataset.id,
        "project_id": dataset.project_id,
        "name": dataset.name,
        "description": dataset.description,
        "status": dataset.status,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


def serialize_dataset_file(dataset_file: DatasetFile) -> dict:
    return {
        "id": dataset_file.id,
        "dataset_id": dataset_file.dataset_id,
        "filename": dataset_file.filename,
        "storage_key": dataset_file.storage_key,
        "file_size_bytes": dataset_file.file_size_bytes,
        "mime_type": dataset_file.mime_type,
        "checksum": dataset_file.checksum,
        "detected_format": dataset_file.detected_format,
        "parsing_result": dataset_file.parsing_result,
        "profile_result": dataset_file.profile_result,
        "normalization_status": dataset_file.normalization_status,
        "normalization_result": dataset_file.normalization_result,
        "normalization_error_code": dataset_file.normalization_error_code,
        "normalization_error_message": dataset_file.normalization_error_message,
        "status": dataset_file.status,
        "error_code": dataset_file.error_code,
        "error_message": dataset_file.error_message,
        "created_at": dataset_file.created_at,
        "updated_at": dataset_file.updated_at,
    }


@app.get("/api/v1/quality-rules")
def list_quality_rules(category: str | None = None, status: str | None = None):
    try:
        rules = list_quality_rule_catalog(category=category, status=status)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {
        "catalog_version": QUALITY_RULE_CATALOG_VERSION,
        "rules": rules,
    }


@app.get("/api/v1/quality-rules/{rule_id}")
def get_quality_rule(rule_id: str):
    rule = get_quality_rule_catalog_entry(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Quality rule not found.")
    return {
        "catalog_version": QUALITY_RULE_CATALOG_VERSION,
        "rule": rule,
    }


@app.get("/api/v1/projects")
def list_projects():
    with SessionLocal() as session:
        try:
            projects = session.query(Project).order_by(Project.id.asc()).all()
        except SQLAlchemyError as error:
            logger.error("Project listing failed (%s)", type(error).__name__)
            raise HTTPException(
                status_code=500,
                detail="Projects could not be retrieved.",
            ) from None

        return [serialize_project(project) for project in projects]


@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: int):
    with SessionLocal() as session:
        try:
            project = session.get(Project, project_id)
        except SQLAlchemyError as error:
            logger.error(
                "Project lookup failed for project %s (%s)",
                project_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Project could not be retrieved.",
            ) from None

        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        return serialize_project(project)


@app.get("/api/v1/projects/{project_id}/datasets")
def list_project_datasets(project_id: int):
    with SessionLocal() as session:
        try:
            project = session.get(Project, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found.")

            datasets = (
                session.query(Dataset)
                .filter(Dataset.project_id == project_id)
                .order_by(Dataset.id.asc())
                .all()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Dataset listing failed for project %s (%s)",
                project_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Project datasets could not be retrieved.",
            ) from None

        return [serialize_dataset(dataset) for dataset in datasets]


@app.get("/api/v1/datasets/{dataset_id}")
def get_dataset(dataset_id: int):
    with SessionLocal() as session:
        try:
            dataset = session.get(Dataset, dataset_id)
        except SQLAlchemyError as error:
            logger.error(
                "Dataset lookup failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset could not be retrieved.",
            ) from None

        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found.")

        return serialize_dataset(dataset)


@app.get("/api/v1/datasets/{dataset_id}/files")
def list_dataset_files(dataset_id: int):
    with SessionLocal() as session:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")

            dataset_files = (
                session.query(DatasetFile)
                .filter(DatasetFile.dataset_id == dataset_id)
                .order_by(DatasetFile.id.asc())
                .all()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "File listing failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset files could not be retrieved.",
            ) from None

        return [serialize_dataset_file(dataset_file) for dataset_file in dataset_files]


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}")
def get_dataset_file(dataset_id: int, file_id: int):
    with SessionLocal() as session:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")

            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "File lookup failed for dataset %s file %s (%s)",
                dataset_id,
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset file could not be retrieved.",
            ) from None

        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")

        return serialize_dataset_file(dataset_file)


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}/profile")
def get_dataset_file_profile(dataset_id: int, file_id: int):
    with SessionLocal() as session:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")

            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "File profile lookup failed for dataset %s file %s (%s)",
                dataset_id,
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset file profile could not be retrieved.",
            ) from None

        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")
        if dataset_file.status != "Ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "profile_unavailable",
                    "message": "A profile is available only after file processing succeeds.",
                    "status": dataset_file.status,
                },
            )
        if dataset_file.profile_result is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "profile_not_available",
                    "message": "No profiling result is stored for this file.",
                },
            )

        return {
            "dataset_id": dataset_id,
            "file_id": file_id,
            "profile_result": dataset_file.profile_result,
        }


@app.get(
    "/api/v1/datasets/{dataset_id}/files/{file_id}/quality-rule-plan",
    response_model=QualityRulePlanResponse,
)
def get_dataset_file_quality_rule_plan(
    dataset_id: int,
    file_id: int,
    category: Literal[
        "BA",
        "DA",
        "statistical",
        "temporal",
        "relational",
        "governance",
    ]
    | None = None,
    status: Literal[
        "implemented",
        "partial",
        "planned",
        "unsupported",
    ]
    | None = None,
):
    with SessionLocal() as session:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Quality-rule planning lookup failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Quality-rule planning context could not be retrieved.",
            ) from None

        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")

        parsed_result = None
        if dataset_file.parsing_result is not None:
            try:
                parsed_result = validate_parsing_result(
                    dataset_file.filename,
                    dataset_file.parsing_result,
                    dataset_file.file_size_bytes,
                )
            except InvalidParsingResultError:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "parsing_result_invalid",
                        "message": "Stored parsing metadata failed validation.",
                    },
                ) from None

        profile_result = None
        if dataset_file.profile_result is not None:
            if parsed_result is None:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "profile_context_invalid",
                        "message": "Stored profile metadata has no valid parsing context.",
                    },
                )
            try:
                profile_result = validate_profile_result(
                    dataset_file.profile_result,
                    parsed_result,
                )
            except InvalidProfileResultError:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "profile_result_invalid",
                        "message": "Stored profile metadata failed validation.",
                    },
                ) from None

        normalized_result = None
        if dataset_file.normalization_status == "Ready":
            if (
                parsed_result is None
                or not dataset_file.checksum
                or not isinstance(dataset_file.normalization_result, dict)
            ):
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "normalization_context_invalid",
                        "message": "Ready normalization metadata is incomplete.",
                    },
                )
            try:
                normalized_result = validate_normalization_result(
                    dataset_file.normalization_result,
                    parsed_result,
                    dataset_file.filename,
                    dataset_file.file_size_bytes,
                    dataset_file.checksum,
                    dataset_id,
                    file_id,
                )
            except NormalizationError:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "normalization_result_invalid",
                        "message": "Stored normalization metadata failed validation.",
                    },
                ) from None

        quality_report = None
        quality_report_current = False
        if dataset_file.quality_report is not None:
            try:
                quality_report = validate_quality_report(
                    dataset_file.quality_report,
                )
            except QualityAnalysisError:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "quality_report_invalid",
                        "message": "The stored quality report failed validation.",
                    },
                ) from None

            if (
                normalized_result is not None
                and dataset_file.checksum
                and dataset_file.quality_analysis_status
                in {"Completed", "PartiallyCompleted"}
            ):
                try:
                    current_key, _configuration_sha256 = quality_analysis_key(
                        dataset_file.checksum,
                        normalized_result,
                        QUALITY_ANALYSIS_CONFIGURATION,
                    )
                    quality_report_current = (
                        quality_report["analysis"]["analysis_key"] == current_key
                    )
                except QualityAnalysisError:
                    quality_report_current = False
            if not quality_report_current:
                quality_report = None

        try:
            return plan_quality_rules(
                QualityPlanningContext(
                    dataset_id=dataset.id,
                    file_id=dataset_file.id,
                    dataset_status=dataset.status,
                    file_status=dataset_file.status,
                    detected_format=dataset_file.detected_format,
                    profile_result=profile_result,
                    normalization_result=normalized_result,
                    quality_report=quality_report,
                    quality_report_current=quality_report_current,
                ),
                category=category,
                status=status,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None


def _download_normalization_source(storage, dataset_file: DatasetFile, destination) -> None:
    bucket = getenv("MINIO_RAW_BUCKET", "insightos-raw")
    response = storage.get_object(bucket, dataset_file.storage_key)
    checksum = hashlib.sha256()
    file_size = 0
    try:
        while chunk := response.read(UPLOAD_CHUNK_SIZE):
            file_size += len(chunk)
            if file_size > dataset_file.file_size_bytes:
                raise NormalizationError(
                    "source_integrity_failed",
                    "Stored source size does not match its ingestion metadata.",
                )
            checksum.update(chunk)
            destination.write(chunk)
    finally:
        response.close()
        release_connection = getattr(response, "release_conn", None)
        if release_connection is not None:
            release_connection()
    if (
        file_size != dataset_file.file_size_bytes
        or checksum.hexdigest() != dataset_file.checksum
    ):
        raise NormalizationError(
            "source_integrity_failed",
            "Stored source checksum does not match its ingestion metadata.",
        )


def _normalization_response(dataset_id: int, file_id: int, dataset_file: DatasetFile) -> dict:
    return {
        "dataset_id": dataset_id,
        "file_id": file_id,
        "status": dataset_file.normalization_status,
        "normalization_result": dataset_file.normalization_result,
        "error": (
            {
                "code": dataset_file.normalization_error_code,
                "message": dataset_file.normalization_error_message,
            }
            if dataset_file.normalization_error_code is not None
            else None
        ),
    }


def _quality_analysis_response(
    dataset_id: int,
    file_id: int,
    dataset_file: DatasetFile,
    current_analysis_key: str | None = None,
) -> dict:
    report = dataset_file.quality_report
    status = dataset_file.quality_analysis_status
    report_key = (
        report.get("analysis", {}).get("analysis_key")
        if isinstance(report, dict)
        else None
    )
    return {
        "dataset_id": dataset_id,
        "file_id": file_id,
        "status": status,
        "analysis_key": dataset_file.quality_analysis_key,
        "report_is_current": (
            report_key is not None and report_key == current_analysis_key
        ),
        "quality_report": report,
        "error": (
            {
                "code": dataset_file.quality_analysis_error_code,
                "message": dataset_file.quality_analysis_error_message,
            }
            if dataset_file.quality_analysis_error_code is not None
            else None
        ),
    }


def _assess_verified_quality_input_lineage(
    report: dict,
    *,
    dataset_id: int,
    file_id: int,
    source_sha256: str,
    source_size_bytes: int,
    normalization_result: dict,
) -> VerificationAssessment:
    tables = normalization_result["tables"]
    verified_inputs = [
        {
            "kind": "source",
            "dataset_id": dataset_id,
            "file_id": file_id,
            "sha256": source_sha256,
            "size_bytes": source_size_bytes,
        },
        *(
            {
                "kind": "normalized_parquet",
                "table_index": table["table_index"],
                "sha256": table.get("sha256"),
                "schema_sha256": (
                    schema_fingerprint(table["schema"])
                    if isinstance(table.get("schema"), list)
                    else None
                ),
            }
            for table in tables
        ),
    ]
    provenance = report.get("provenance")
    claimed_inputs = provenance.get("inputs") if isinstance(provenance, dict) else None
    evidence = evidence_for(
        claimed_inputs,
        verified_inputs,
        reference="quality-lineage:verified-object-readback",
    )
    budget = ExecutionBudget(
        {
            "jev_checks": 1,
            "jev_evidence_bytes": 16 * 1024,
        }
    )
    return verify_claim(
        claimed_inputs,
        evidence,
        policy_for("schema"),
        budget=budget,
    )


def _log_internal_quality_lineage_assessment(
    report: dict,
    dataset_file: DatasetFile,
    *,
    dataset_id: int,
    file_id: int,
    normalization_result: dict,
) -> None:
    try:
        assessment = _assess_verified_quality_input_lineage(
            report,
            dataset_id=dataset_id,
            file_id=file_id,
            source_sha256=dataset_file.checksum,
            source_size_bytes=dataset_file.file_size_bytes,
            normalization_result=normalization_result,
        )
    except (TypeError, ValueError, OverflowError) as error:
        logger.warning(
            "Internal quality lineage assessment unavailable for file %s (%s)",
            file_id,
            type(error).__name__,
        )
        return
    if assessment.status != "verified":
        logger.warning(
            "Internal quality lineage assessment did not verify for file %s (%s)",
            file_id,
            assessment.reason_code,
        )


def _mark_quality_analysis_failed(
    session: Session,
    dataset_id: int,
    file_id: int,
    analysis_key: str | None,
    code: str,
    message: str,
) -> bool:
    try:
        session.rollback()
        dataset_file = (
            session.query(DatasetFile)
            .filter(
                DatasetFile.id == file_id,
                DatasetFile.dataset_id == dataset_id,
            )
            .one_or_none()
        )
        if dataset_file is None:
            return False
        dataset_file.quality_analysis_status = "Failed"
        dataset_file.quality_analysis_key = analysis_key
        dataset_file.quality_analysis_error_code = code
        dataset_file.quality_analysis_error_message = message
        if dataset_file.quality_analysis_started_at is None:
            dataset_file.quality_analysis_started_at = datetime.now(timezone.utc)
        session.commit()
        return True
    except Exception as error:
        try:
            session.rollback()
        except Exception as rollback_error:
            logger.error(
                "Could not roll back failed quality-analysis state for file %s (%s)",
                file_id,
                type(rollback_error).__name__,
            )
        logger.error(
            "Could not persist quality-analysis failure for file %s (%s)",
            file_id,
            type(error).__name__,
        )
        return False


def _mark_normalization_failed(
    session: Session,
    dataset_id: int,
    file_id: int,
    code: str,
    message: str,
) -> None:
    session.rollback()
    try:
        dataset_file = (
            session.query(DatasetFile)
            .filter(
                DatasetFile.id == file_id,
                DatasetFile.dataset_id == dataset_id,
            )
            .one_or_none()
        )
        if dataset_file is None or dataset_file.normalization_status == "Ready":
            return
        dataset_file.normalization_status = "Failed"
        dataset_file.normalization_error_code = code
        dataset_file.normalization_error_message = message
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.error(
            "Could not persist normalization failure for file %s (%s)",
            file_id,
            type(error).__name__,
        )


@app.post("/api/v1/datasets/{dataset_id}/files/{file_id}/normalize")
def normalize_dataset_file(dataset_id: int, file_id: int):
    try:
        configuration = get_normalization_configuration()
    except NormalizationError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": error.code, "message": error.message},
        ) from None

    with _pinned_upload_session() as (session, connection), ExitStack() as cleanup:
        try:
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except SQLAlchemyError as error:
            logger.error(
                "Normalization file lookup failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset file could not be checked for normalization.",
            ) from None

        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")
        if dataset_file.status != "Ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "file_not_ready",
                    "message": "A file can be normalized only after ingestion succeeds.",
                    "status": dataset_file.status,
                },
            )
        if not dataset_file.checksum or not dataset_file.parsing_result:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "source_metadata_unavailable",
                    "message": "The source file is missing validated parsing metadata.",
                },
            )

        try:
            lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"normalization:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve normalization for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Normalization concurrency protection is temporarily unavailable.",
            ) from None
        if lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalization_in_progress",
                    "message": "Normalization is already in progress for this file.",
                },
            )
        cleanup.callback(_release_upload_lock, session, connection, lock_id)

        try:
            quality_lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"quality-analysis:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve quality analysis during normalization of file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Quality-analysis concurrency protection is unavailable.",
            ) from None
        if quality_lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "quality_analysis_in_progress",
                    "message": "Quality analysis is using this file; retry normalization later.",
                },
            )
        cleanup.callback(
            _release_upload_lock,
            session,
            connection,
            quality_lock_id,
        )

        try:
            eda_lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"eda:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve EDA lock during normalization of file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="EDA concurrency protection is unavailable.",
            ) from None
        if eda_lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "eda_in_progress",
                    "message": "EDA is analyzing this file; retry normalization later.",
                },
            )
        cleanup.callback(
            _release_upload_lock,
            session,
            connection,
            eda_lock_id,
        )

        try:
            preprocessing_lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"preprocessing:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve preprocessing lock during normalization of file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Preprocessing concurrency protection is unavailable.",
            ) from None
        if preprocessing_lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preprocessing_in_progress",
                    "message": "Preprocessing is analyzing this file; retry normalization later.",
                },
            )
        cleanup.callback(
            _release_upload_lock,
            session,
            connection,
            preprocessing_lock_id,
        )

        try:
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
            if dataset_file is None or dataset_file.status != "Ready":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "file_not_ready",
                        "message": "A file can be normalized only after ingestion succeeds.",
                    },
                )
            existing = dataset_file.normalization_result
            storage = get_storage_client()
            processed_bucket = getenv(
                "MINIO_PROCESSED_BUCKET",
                "insightos-processed",
            )
            if isinstance(existing, dict):
                try:
                    existing = validate_normalization_result(
                        existing,
                        dataset_file.parsing_result,
                        dataset_file.filename,
                        dataset_file.file_size_bytes,
                        dataset_file.checksum,
                        dataset_id,
                        file_id,
                    )
                except NormalizationError:
                    existing = None
            if (
                dataset_file.normalization_status == "Ready"
                and isinstance(existing, dict)
                and isinstance(existing.get("configuration"), dict)
                and existing["configuration"].get("configuration_sha256")
                == configuration["configuration_sha256"]
                and verify_normalization_result(storage, processed_bucket, existing)
            ):
                return _normalization_response(dataset_id, file_id, dataset_file)

            dataset_file.normalization_status = "Processing"
            dataset_file.normalization_result = None
            dataset_file.normalization_error_code = None
            dataset_file.normalization_error_message = None
            session.commit()

            suffix = "." + dataset_file.filename.rpartition(".")[2]
            with tempfile.TemporaryDirectory(
                prefix="insightos-normalization-"
            ) as temporary_directory:
                source_path = os.path.join(
                    temporary_directory,
                    "source" + suffix,
                )
                with open(source_path, "wb") as source:
                    _download_normalization_source(storage, dataset_file, source)
                normalized = normalize_file(
                    source_path,
                    dataset_file.filename,
                    dataset_file.parsing_result,
                    dataset_id,
                    file_id,
                    dataset_file.checksum,
                    storage,
                    processed_bucket,
                )

            normalized = validate_normalization_result(
                normalized,
                dataset_file.parsing_result,
                dataset_file.filename,
                dataset_file.file_size_bytes,
                dataset_file.checksum,
                dataset_id,
                file_id,
            )
            dataset_file = session.get(DatasetFile, file_id)
            if dataset_file is None:
                raise SQLAlchemyError("Dataset file disappeared during normalization.")
            dataset_file.normalization_result = normalized
            dataset_file.normalization_status = "Ready"
            dataset_file.normalization_error_code = None
            dataset_file.normalization_error_message = None
            session.commit()
            return _normalization_response(dataset_id, file_id, dataset_file)
        except HTTPException:
            raise
        except NormalizationError as error:
            _mark_normalization_failed(
                session,
                dataset_id,
                file_id,
                error.code,
                error.message,
            )
            status_code = (
                502
                if error.code
                in {"source_integrity_failed", "storage_verification_failed"}
                else 422
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": error.code, "message": error.message},
            ) from None
        except (MinioException, HTTPError, OSError) as error:
            logger.error(
                "Normalization storage operation failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            _mark_normalization_failed(
                session,
                dataset_id,
                file_id,
                "storage_failure",
                "Object storage failed during normalization; retry the operation.",
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "storage_failure",
                    "message": "Object storage failed during normalization; retry the operation.",
                },
            ) from None
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Normalization metadata operation failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            _mark_normalization_failed(
                session,
                dataset_id,
                file_id,
                "metadata_persistence_failed",
                "Normalization metadata could not be saved; retry the operation.",
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "metadata_persistence_failed",
                    "message": "Normalization metadata could not be saved; retry the operation.",
                },
            ) from None


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}/normalization")
def get_dataset_file_normalization(dataset_id: int, file_id: int):
    with SessionLocal() as session:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Normalization status lookup failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Normalization status could not be retrieved.",
            ) from None
        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")
        return _normalization_response(dataset_id, file_id, dataset_file)


@app.post("/api/v1/datasets/{dataset_id}/files/{file_id}/quality-analysis")
def analyze_dataset_file_quality(
    dataset_id: int,
    file_id: int,
    request: QualityAnalysisRequest = Body(default=QualityAnalysisRequest()),
):
    with _pinned_upload_session() as (session, connection), ExitStack() as cleanup:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Quality-analysis file lookup failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset file could not be checked for quality analysis.",
            ) from None

        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")
        if dataset_file.status != "Ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "file_not_ready",
                    "message": "Quality analysis requires successful file ingestion.",
                    "status": dataset_file.status,
                },
            )
        if dataset_file.normalization_status != "Ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalization_unavailable",
                    "message": "Quality analysis requires ready normalized output.",
                    "status": dataset_file.normalization_status,
                },
            )
        if (
            not dataset_file.checksum
            or not dataset_file.parsing_result
            or not isinstance(dataset_file.normalization_result, dict)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_provenance_unavailable",
                    "message": "Validated source and normalized metadata are required.",
                },
            )

        try:
            lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"quality-analysis:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve quality analysis for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Quality-analysis concurrency protection is unavailable.",
            ) from None
        if lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "quality_analysis_in_progress",
                    "message": "Quality analysis is already in progress for this file.",
                },
            )
        cleanup.callback(_release_upload_lock, session, connection, lock_id)

        analysis_key = None
        started_at = datetime.now(timezone.utc)
        try:
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
            if (
                dataset_file is None
                or dataset_file.status != "Ready"
                or dataset_file.normalization_status != "Ready"
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "analysis_input_changed",
                        "message": "File readiness changed before analysis could start.",
                    },
                )
            try:
                normalized = validate_normalization_result(
                    dataset_file.normalization_result,
                    dataset_file.parsing_result,
                    dataset_file.filename,
                    dataset_file.file_size_bytes,
                    dataset_file.checksum,
                    dataset_id,
                    file_id,
                )
            except NormalizationError as error:
                raise QualityAnalysisError(
                    "normalized_output_invalid",
                    "Stored normalized output metadata failed validation.",
                ) from error

            storage = get_storage_client()
            raw_bucket = getenv("MINIO_RAW_BUCKET", "insightos-raw")
            processed_bucket = getenv(
                "MINIO_PROCESSED_BUCKET",
                "insightos-processed",
            )
            if not _verify_stored_object(storage, raw_bucket, dataset_file):
                raise QualityAnalysisError(
                    "source_integrity_failed",
                    "Stored source data failed integrity verification.",
                )
            normalized_verification = verify_normalization_result_detailed(
                storage,
                processed_bucket,
                normalized,
            )
            if normalized_verification["status"] != "verified":
                raise QualityAnalysisError(
                    f"normalized_output_{normalized_verification['status']}",
                    "Stored normalized output could not be verified as intact.",
                )

            analysis_key, configuration_sha256 = quality_analysis_key(
                dataset_file.checksum,
                normalized,
                QUALITY_ANALYSIS_CONFIGURATION,
            )
            if (
                not request.recompute
                and dataset_file.quality_analysis_status
                in {"Completed", "PartiallyCompleted"}
                and dataset_file.quality_analysis_key == analysis_key
                and isinstance(dataset_file.quality_report, dict)
            ):
                try:
                    dataset_file.quality_report = validate_quality_report(
                        dataset_file.quality_report,
                        expected_summary_totals=normalized_summary_totals(
                            normalized
                        ),
                        expected_source_sha256=dataset_file.checksum,
                        expected_analysis_key=analysis_key,
                        expected_normalized_outputs=normalized_output_provenance(
                            normalized
                        ),
                    )
                except QualityAnalysisError:
                    pass
                else:
                    _log_internal_quality_lineage_assessment(
                        dataset_file.quality_report,
                        dataset_file,
                        dataset_id=dataset_id,
                        file_id=file_id,
                        normalization_result=normalized,
                    )
                    return _quality_analysis_response(
                        dataset_id,
                        file_id,
                        dataset_file,
                        current_analysis_key=analysis_key,
                    )

            dataset_file.quality_analysis_status = "Requested"
            dataset_file.quality_analysis_key = analysis_key
            dataset_file.quality_analysis_started_at = started_at
            dataset_file.quality_analysis_error_code = None
            dataset_file.quality_analysis_error_message = None
            session.commit()

            dataset_file = session.get(DatasetFile, file_id)
            if dataset_file is None:
                raise SQLAlchemyError("Dataset file disappeared during quality analysis.")
            dataset_file.quality_analysis_status = "Running"
            session.commit()

            report_started_at = datetime.now(timezone.utc)
            rule_inputs = dict(normalized)
            rule_inputs["_parsing_result"] = dataset_file.parsing_result
            if dataset_file.profile_result is not None:
                try:
                    rule_inputs["_profile_result"] = validate_profile_result(
                        dataset_file.profile_result,
                        dataset_file.parsing_result,
                    )
                except InvalidProfileResultError as error:
                    logger.warning(
                        "Ignoring invalid profile metadata for quality analysis of file %s (%s)",
                        file_id,
                        type(error).__name__,
                    )
            parquet_paths, parquet_input_reason = (
                _materialize_quality_parquet_inputs(
                    storage,
                    processed_bucket,
                    normalized,
                    cleanup,
                )
            )
            rule_inputs["_parquet_paths"] = parquet_paths
            if parquet_input_reason is not None:
                rule_inputs["_parquet_input_reason"] = parquet_input_reason
            rule_results = run_quality_rules(
                rule_inputs,
                configuration={
                    key: value
                    for key, value in QUALITY_ANALYSIS_CONFIGURATION.items()
                    if key != "batch_size"
                },
            )
            report = build_contract_report(
                dataset_file.checksum,
                normalized,
                analysis_key,
                configuration_sha256,
                report_started_at,
                datetime.now(timezone.utc),
                rule_results=rule_results,
                verification_status="verified",
            )
            report = validate_quality_report(
                report,
                expected_summary_totals=normalized_summary_totals(normalized),
                expected_source_sha256=dataset_file.checksum,
                expected_analysis_key=analysis_key,
                expected_normalized_outputs=normalized_output_provenance(
                    normalized
                ),
            )
            _log_internal_quality_lineage_assessment(
                report,
                dataset_file,
                dataset_id=dataset_id,
                file_id=file_id,
                normalization_result=normalized,
            )

            dataset_file = session.get(DatasetFile, file_id)
            if dataset_file is None:
                raise SQLAlchemyError("Dataset file disappeared during quality analysis.")
            dataset_file.quality_report = report
            dataset_file.quality_analysis_status = report["completion_status"]
            dataset_file.quality_analysis_error_code = None
            dataset_file.quality_analysis_error_message = None
            dataset_file.quality_analysis_completed_at = datetime.now(timezone.utc)
            session.commit()
            return _quality_analysis_response(
                dataset_id,
                file_id,
                dataset_file,
                current_analysis_key=analysis_key,
            )
        except HTTPException:
            raise
        except QualityAnalysisError as error:
            if not _mark_quality_analysis_failed(
                session,
                dataset_id,
                file_id,
                analysis_key,
                error.code,
                error.message,
            ):
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "quality_analysis_failure_persistence_failed",
                        "message": "Quality-analysis failure details could not be saved.",
                    },
                ) from None
            raise HTTPException(
                status_code=422,
                detail={"code": error.code, "message": error.message},
            ) from None
        except (MinioException, HTTPError, OSError) as error:
            logger.error(
                "Quality-analysis storage operation failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            if not _mark_quality_analysis_failed(
                session,
                dataset_id,
                file_id,
                analysis_key,
                "storage_failure",
                "Object storage failed during quality analysis; retry the operation.",
            ):
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "quality_analysis_failure_persistence_failed",
                        "message": "Quality-analysis failure details could not be saved.",
                    },
                ) from None
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "storage_failure",
                    "message": "Object storage failed during quality analysis; retry the operation.",
                },
            ) from None
        except SQLAlchemyError as error:
            logger.error(
                "Quality-analysis metadata operation failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            if not _mark_quality_analysis_failed(
                session,
                dataset_id,
                file_id,
                analysis_key,
                "metadata_persistence_failed",
                "Quality-analysis metadata could not be saved; retry the operation.",
            ):
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "quality_analysis_failure_persistence_failed",
                        "message": "Quality-analysis failure details could not be saved.",
                    },
                ) from None
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "metadata_persistence_failed",
                    "message": "Quality-analysis metadata could not be saved; retry the operation.",
                },
            ) from None
        except Exception as error:
            logger.error(
                "Unexpected quality-analysis failure for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            if not _mark_quality_analysis_failed(
                session,
                dataset_id,
                file_id,
                analysis_key,
                "quality_analysis_failed",
                "Quality analysis failed unexpectedly; retry the operation.",
            ):
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "quality_analysis_failure_persistence_unconfirmed",
                        "message": (
                            "The analysis failure state could not be confirmed; "
                            "check the current status before retrying."
                        ),
                    },
                ) from None
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "quality_analysis_failed",
                    "message": "Quality analysis failed unexpectedly; retry the operation.",
                },
            ) from None


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}/quality-analysis")
def get_dataset_file_quality_analysis(dataset_id: int, file_id: int):
    with SessionLocal() as session:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Quality-analysis status lookup failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Quality-analysis status could not be retrieved.",
            ) from None
        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")
        if dataset_file.quality_report is not None:
            try:
                dataset_file.quality_report = validate_quality_report(
                    dataset_file.quality_report,
                )
            except QualityAnalysisError:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "quality_report_invalid",
                        "message": "The stored quality report failed validation.",
                    },
                ) from None

        current_analysis_key = None
        if (
            dataset_file.normalization_status == "Ready"
            and isinstance(dataset_file.normalization_result, dict)
            and isinstance(dataset_file.parsing_result, dict)
            and dataset_file.checksum
        ):
            try:
                normalized = validate_normalization_result(
                    dataset_file.normalization_result,
                    dataset_file.parsing_result,
                    dataset_file.filename,
                    dataset_file.file_size_bytes,
                    dataset_file.checksum,
                    dataset_id,
                    file_id,
                )
                current_analysis_key, _configuration_sha256 = quality_analysis_key(
                    dataset_file.checksum,
                    normalized,
                    QUALITY_ANALYSIS_CONFIGURATION,
                )
            except (NormalizationError, QualityAnalysisError):
                current_analysis_key = None
        return _quality_analysis_response(
            dataset_id,
            file_id,
            dataset_file,
            current_analysis_key=current_analysis_key,
        )


def eda_analysis_key(
    source_sha256: str,
    table_index: int,
    table_sha256: str,
    configuration_sha256: str,
    algorithm_version: str,
) -> str:
    raw = f"{source_sha256}:{table_index}:{table_sha256}:{configuration_sha256}:{algorithm_version}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _assess_verified_eda_input_lineage(
    provenance: dict,
    *,
    dataset_id: int,
    file_id: int,
    source_sha256: str,
    source_size_bytes: int,
    table: dict,
    table_index: int,
    schema: list[dict[str, str]] | None,
) -> VerificationAssessment:
    verified_inputs = [
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
            "table_index": table_index,
            "object_key": table.get("object_key"),
            "sha256": table.get("sha256"),
            "size_bytes": table.get("file_size_bytes"),
            "schema_sha256": (
                schema_fingerprint(schema)
                if isinstance(schema, list)
                else None
            ),
        },
    ]
    claimed_inputs = provenance.get("inputs") if isinstance(provenance, dict) else None
    evidence = evidence_for(
        claimed_inputs,
        verified_inputs,
        reference="eda-lineage:verified-object-readback",
    )
    budget = ExecutionBudget(
        {
            "jev_checks": 1,
            "jev_evidence_bytes": 16 * 1024,
        }
    )
    return verify_claim(
        claimed_inputs,
        evidence,
        policy_for("schema"),
        budget=budget,
    )


def _log_internal_eda_lineage_assessment(
    provenance: dict,
    *,
    dataset_id: int,
    file_id: int,
    source_sha256: str,
    source_size_bytes: int,
    table: dict,
    table_index: int,
    schema: list[dict[str, str]] | None,
) -> None:
    try:
        assessment = _assess_verified_eda_input_lineage(
            provenance,
            dataset_id=dataset_id,
            file_id=file_id,
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
            table=table,
            table_index=table_index,
            schema=schema,
        )
    except (TypeError, ValueError, OverflowError) as error:
        logger.warning(
            "Internal EDA lineage assessment unavailable for file %s (%s)",
            file_id,
            type(error).__name__,
        )
        return
    if assessment.status != "verified":
        logger.warning(
            "Internal EDA lineage assessment did not verify for file %s (%s)",
            file_id,
            assessment.reason_code,
        )


def _eda_analysis_response(record: EDAAnalysis) -> dict:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "file_id": record.file_id,
        "table_index": record.table_index,
        "analysis_type": record.analysis_type,
        "status": record.status,
        "analysis_key": record.analysis_key,
        "configuration": record.configuration,
        "configuration_sha256": record.configuration_sha256,
        "result": record.result,
        "provenance": record.provenance,
        "error": (
            {
                "code": record.error_code,
                "message": record.error_message,
            }
            if record.error_code is not None
            else None
        ),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _eda_analysis_summary(record: EDAAnalysis) -> dict:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "file_id": record.file_id,
        "table_index": record.table_index,
        "analysis_type": record.analysis_type,
        "status": record.status,
        "analysis_key": record.analysis_key,
        "configuration": record.configuration,
        "error": (
            {
                "code": record.error_code,
                "message": record.error_message,
            }
            if record.error_code is not None
            else None
        ),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


@app.post("/api/v1/datasets/{dataset_id}/files/{file_id}/eda")
def analyze_dataset_file_eda(
    dataset_id: int,
    file_id: int,
    request: dict = Body(default_factory=dict),
    recompute: bool = False,
):
    recompute = recompute or bool(request.get("recompute", False))
    config_payload = {k: v for k, v in request.items() if k != "recompute"}
    try:
        configuration = eda_configuration_from_dict(config_payload)
    except EDAError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": error.code, "message": error.message},
        ) from None

    with _pinned_upload_session() as (session, connection), ExitStack() as cleanup:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "EDA file lookup failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset file could not be checked for EDA.",
            ) from None

        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")
        if dataset_file.status != "Ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "file_not_ready",
                    "message": "EDA requires successful file ingestion.",
                    "status": dataset_file.status,
                },
            )
        if dataset_file.normalization_status != "Ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalization_unavailable",
                    "message": "EDA requires ready normalized output.",
                    "status": dataset_file.normalization_status,
                },
            )
        if (
            not dataset_file.checksum
            or not dataset_file.parsing_result
            or not isinstance(dataset_file.normalization_result, dict)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_provenance_unavailable",
                    "message": "Validated source and normalized metadata are required.",
                },
            )

        try:
            eda_lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"eda:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve EDA for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="EDA concurrency protection is unavailable.",
            ) from None
        if eda_lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "eda_in_progress",
                    "message": "EDA is already in progress for this file.",
                },
            )
        cleanup.callback(_release_upload_lock, session, connection, eda_lock_id)

        try:
            norm_lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"normalization:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not check normalization lock for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Normalization concurrency protection is unavailable.",
            ) from None
        if norm_lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalization_in_progress",
                    "message": "Normalization is using this file; retry EDA later.",
                },
            )
        cleanup.callback(_release_upload_lock, session, connection, norm_lock_id)

        try:
            normalized = validate_normalization_result(
                dataset_file.normalization_result,
                dataset_file.parsing_result,
                dataset_file.filename,
                dataset_file.file_size_bytes,
                dataset_file.checksum,
                dataset_id,
                file_id,
            )
        except NormalizationError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalized_output_invalid",
                    "message": "Stored normalized output metadata failed validation.",
                },
            ) from error

        tables = normalized["tables"]
        if configuration.table_index >= len(tables):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "unknown_table",
                    "message": "Selected normalized table does not exist.",
                },
            )
        selected_table = tables[configuration.table_index]
        if not selected_table.get("sha256"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "artifact_unavailable",
                    "message": "Selected normalized table has no verified checksum.",
                },
            )

        storage = get_storage_client()
        raw_bucket = getenv("MINIO_RAW_BUCKET", "insightos-raw")
        processed_bucket = getenv("MINIO_PROCESSED_BUCKET", "insightos-processed")

        if not _verify_stored_object(storage, raw_bucket, dataset_file):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "source_integrity_failed",
                    "message": "Stored source data failed integrity verification.",
                },
            )

        normalized_verification = verify_normalization_result_detailed(
            storage,
            processed_bucket,
            normalized,
        )
        if normalized_verification["status"] != "verified":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": f"normalized_output_{normalized_verification['status']}",
                    "message": "Stored normalized output could not be verified as intact.",
                },
            )

        algorithm_version = _algorithm_version_for_analysis(configuration.analysis_type)
        configuration_dict = configuration.as_dict()
        configuration_sha256 = hashlib.sha256(
            json.dumps(
                configuration_dict,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        analysis_key = eda_analysis_key(
            dataset_file.checksum,
            configuration.table_index,
            selected_table["sha256"],
            configuration_sha256,
            algorithm_version,
        )

        existing_record = (
            session.query(EDAAnalysis)
            .filter(
                EDAAnalysis.file_id == file_id,
                EDAAnalysis.analysis_key == analysis_key,
            )
            .one_or_none()
        )

        if (
            not recompute
            and existing_record is not None
            and existing_record.status in ("Completed", "PartiallyCompleted")
            and isinstance(existing_record.result, dict)
        ):
            _log_internal_eda_lineage_assessment(
                existing_record.provenance or {},
                dataset_id=dataset_id,
                file_id=file_id,
                source_sha256=dataset_file.checksum,
                source_size_bytes=dataset_file.file_size_bytes,
                table=selected_table,
                table_index=configuration.table_index,
                schema=selected_table.get("schema"),
            )
            return _eda_analysis_response(existing_record)

        started_at = datetime.now(timezone.utc)
        if existing_record is None:
            record = EDAAnalysis(
                dataset_id=dataset_id,
                file_id=file_id,
                table_index=configuration.table_index,
                analysis_type=configuration.analysis_type,
                analysis_key=analysis_key,
                status="Running",
                configuration=configuration_dict,
                configuration_sha256=configuration_sha256,
                started_at=started_at,
            )
            session.add(record)
        else:
            record = existing_record
            record.status = "Running"
            record.started_at = started_at
            record.completed_at = None
            record.error_code = None
            record.error_message = None
            record.result = None
            record.provenance = None
        session.commit()

        try:
            eda_result = analyze_normalized_file(
                storage=storage,
                processed_bucket=processed_bucket,
                dataset_id=dataset_id,
                file_id=file_id,
                file_status=dataset_file.status,
                normalization_status=dataset_file.normalization_status,
                source_filename=dataset_file.filename,
                source_size_bytes=dataset_file.file_size_bytes,
                source_sha256=dataset_file.checksum,
                parsed_result=dataset_file.parsing_result,
                normalization_result=normalized,
                configuration=configuration,
                profile_result=dataset_file.profile_result,
                quality_report=dataset_file.quality_report,
            )
        except EDAError as error:
            record.status = "Failed"
            record.error_code = error.code
            record.error_message = error.message
            record.completed_at = datetime.now(timezone.utc)
            session.commit()
            status_code = (
                422
                if error.code in ("row_limit", "result_size_limit", "artifact_size_limit")
                else 400
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": error.code, "message": error.message},
            ) from None
        except Exception as error:
            record.status = "Failed"
            record.error_code = "analysis_failed"
            record.error_message = "EDA analysis failed unexpectedly."
            record.completed_at = datetime.now(timezone.utc)
            session.commit()
            logger.error(
                "EDA analysis execution failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "analysis_failed",
                    "message": "EDA analysis failed unexpectedly.",
                },
            ) from None

        result_dict = eda_result.as_dict()
        if eda_result.status == "failed":
            record.status = "Failed"
            record.error_code = (
                eda_result.failure.code
                if eda_result.failure
                else "analysis_failed"
            )
            record.error_message = (
                eda_result.failure.message
                if eda_result.failure
                else "EDA analysis failed."
            )
        elif eda_result.status == "partially_completed":
            record.status = "PartiallyCompleted"
            record.error_code = None
            record.error_message = None
        else:
            record.status = "Completed"
            record.error_code = None
            record.error_message = None

        record.result = result_dict
        record.provenance = eda_result.provenance
        record.completed_at = datetime.now(timezone.utc)
        session.commit()

        if record.status != "Failed":
            _log_internal_eda_lineage_assessment(
                record.provenance or {},
                dataset_id=dataset_id,
                file_id=file_id,
                source_sha256=dataset_file.checksum,
                source_size_bytes=dataset_file.file_size_bytes,
                table=selected_table,
                table_index=configuration.table_index,
                schema=selected_table.get("schema"),
            )

        return _eda_analysis_response(record)


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}/eda")
def list_dataset_file_eda_analyses(
    dataset_id: int,
    file_id: int,
    limit: int = 50,
    offset: int = 0,
    analysis_type: str | None = None,
    table_index: int | None = None,
):
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 100.",
        )
    if offset < 0:
        raise HTTPException(
            status_code=422,
            detail="offset must be greater than or equal to 0.",
        )
    with SessionLocal() as session:
        dataset = session.get(Dataset, dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found.")
        dataset_file = (
            session.query(DatasetFile)
            .filter(
                DatasetFile.id == file_id,
                DatasetFile.dataset_id == dataset_id,
            )
            .one_or_none()
        )
        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")

        query = session.query(EDAAnalysis).filter(
            EDAAnalysis.dataset_id == dataset_id,
            EDAAnalysis.file_id == file_id,
        )
        if analysis_type is not None:
            query = query.filter(EDAAnalysis.analysis_type == analysis_type)
        if table_index is not None:
            query = query.filter(EDAAnalysis.table_index == table_index)

        total = query.count()
        records = (
            query.order_by(EDAAnalysis.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "dataset_id": dataset_id,
            "file_id": file_id,
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_eda_analysis_summary(record) for record in records],
        }


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}/eda/{analysis_id}")
def get_dataset_file_eda_analysis(
    dataset_id: int,
    file_id: int,
    analysis_id: int,
):
    with SessionLocal() as session:
        dataset = session.get(Dataset, dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found.")
        dataset_file = (
            session.query(DatasetFile)
            .filter(
                DatasetFile.id == file_id,
                DatasetFile.dataset_id == dataset_id,
            )
            .one_or_none()
        )
        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")

        record = (
            session.query(EDAAnalysis)
            .filter(
                EDAAnalysis.id == analysis_id,
                EDAAnalysis.dataset_id == dataset_id,
                EDAAnalysis.file_id == file_id,
            )
            .one_or_none()
        )
        if record is None:
            raise HTTPException(status_code=404, detail="EDA analysis not found.")

        return _eda_analysis_response(record)


# =============================================================================
# Phase 4: Deterministic Schema Architect
# =============================================================================

def _schema_input_manifest(
    dataset_files: list[DatasetFile],
    selections: list[dict],
) -> dict:
    files_by_id = {dataset_file.id: dataset_file for dataset_file in dataset_files}
    manifest_files = []
    for selection in selections:
        dataset_file = files_by_id.get(selection["file_id"])
        if dataset_file is None:
            raise SchemaAnalysisError(
                "source_changed",
                "A selected dataset file is no longer available.",
            )
        normalized = dataset_file.normalization_result
        if not isinstance(normalized, dict):
            raise SchemaAnalysisError(
                "normalization_unavailable",
                "A selected file has no validated normalized output.",
            )
        normalized_tables = normalized.get("tables")
        if not isinstance(normalized_tables, list):
            raise SchemaAnalysisError(
                "normalized_output_invalid",
                "Stored normalized table metadata is invalid.",
            )
        selected_tables = []
        for table_index in selection["table_indices"]:
            if (
                table_index < 0
                or table_index >= len(normalized_tables)
                or not isinstance(normalized_tables[table_index], dict)
            ):
                raise SchemaAnalysisError(
                    "invalid_selection",
                    "A selected normalized table index does not exist.",
                )
            table = normalized_tables[table_index]
            schema = table.get("schema")
            checksum = table.get("sha256")
            size = table.get("file_size_bytes")
            if (
                not isinstance(schema, list)
                or not isinstance(checksum, str)
                or not isinstance(size, int)
                or isinstance(size, bool)
            ):
                raise SchemaAnalysisError(
                    "normalized_output_invalid",
                    "Stored normalized table metadata is invalid.",
                )
            try:
                schema_sha256 = schema_fingerprint(schema)
            except (TypeError, ValueError):
                raise SchemaAnalysisError(
                    "normalized_output_invalid",
                    "Stored normalized table schema metadata is invalid.",
                ) from None
            object_key = table.get("object_key")
            selected_tables.append(
                {
                    "table_index": table_index,
                    "table_name": table.get("table_name"),
                    "object_identity_sha256": (
                        hashlib.sha256(object_key.encode("utf-8")).hexdigest()
                        if isinstance(object_key, str)
                        else None
                    ),
                    "sha256": checksum,
                    "size_bytes": size,
                    "schema_sha256": schema_sha256,
                    "row_count": table.get("row_count"),
                    "column_count": table.get("column_count"),
                    "warnings_sha256": canonical_sha256(
                        table.get("warnings", [])
                    ),
                }
            )
        manifest_files.append(
            {
                "file_id": dataset_file.id,
                "filename": dataset_file.filename,
                "status": dataset_file.status,
                "normalization_status": dataset_file.normalization_status,
                "source_sha256": dataset_file.checksum,
                "source_size_bytes": dataset_file.file_size_bytes,
                "parsing_result_sha256": (
                    canonical_sha256(dataset_file.parsing_result)
                    if dataset_file.parsing_result is not None
                    else None
                ),
                "profile_sha256": (
                    canonical_sha256(dataset_file.profile_result)
                    if dataset_file.profile_result is not None
                    else None
                ),
                "quality_report_sha256": (
                    canonical_sha256(dataset_file.quality_report)
                    if dataset_file.quality_report is not None
                    else None
                ),
                "normalization_configuration_sha256": (
                    normalized.get("configuration", {}).get(
                        "configuration_sha256"
                    )
                    if isinstance(normalized.get("configuration"), dict)
                    else None
                ),
                "tables": selected_tables,
            }
        )
    return {"files": manifest_files}


def _schema_analysis_is_stale(
    session: Session,
    record: SchemaAnalysis,
    current_files: list[DatasetFile] | None = None,
) -> bool:
    configuration = record.configuration
    if not isinstance(configuration, dict):
        return True
    selections = configuration.get("files")
    if not isinstance(selections, list):
        return True
    if current_files is None:
        current_files = (
            session.query(DatasetFile)
            .filter(DatasetFile.dataset_id == record.dataset_id)
            .order_by(DatasetFile.id.asc())
            .all()
        )
    if configuration.get("selection_mode") == "all":
        if [item.id for item in current_files] != configuration.get(
            "dataset_file_ids"
        ):
            return True
    try:
        current_manifest = _schema_input_manifest(current_files, selections)
    except (SchemaAnalysisError, TypeError, ValueError):
        return True
    stored_manifest = configuration.get("input_manifest")
    return not isinstance(stored_manifest, dict) or current_manifest != stored_manifest


def _schema_analysis_response(
    record: SchemaAnalysis,
    *,
    stale: bool,
    include_result: bool = True,
) -> dict:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "status": "Stale" if stale and record.status == "Completed" else record.status,
        "stored_status": record.status,
        "stale": stale,
        "analysis_key": record.analysis_key,
        "configuration": record.configuration,
        "configuration_sha256": record.configuration_sha256,
        "result": record.result if include_result else None,
        "provenance": record.provenance,
        "error": (
            {"code": record.error_code, "message": record.error_message}
            if record.error_code is not None
            else None
        ),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": (
            record.completed_at.isoformat() if record.completed_at else None
        ),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _schema_analysis_summary(record: SchemaAnalysis, *, stale: bool) -> dict:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "status": "Stale" if stale and record.status == "Completed" else record.status,
        "stored_status": record.status,
        "stale": stale,
        "analysis_key": record.analysis_key,
        "configuration": record.configuration,
        "error": (
            {"code": record.error_code, "message": record.error_message}
            if record.error_code is not None
            else None
        ),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": (
            record.completed_at.isoformat() if record.completed_at else None
        ),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _schema_quality_warnings(
    quality_report: dict | None,
    table_index: int,
) -> tuple[dict, ...]:
    if not isinstance(quality_report, dict):
        return ()
    findings = quality_report.get("findings")
    if not isinstance(findings, list):
        return ()
    selected = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        scope = finding.get("scope")
        if isinstance(scope, dict):
            finding_table_index = scope.get("table_index")
            if finding_table_index is not None and finding_table_index != table_index:
                continue
        selected.append(finding)
        if len(selected) >= 50:
            break
    return tuple(selected)


def _schema_input_tables(
    storage,
    bucket: str,
    dataset_files: list[DatasetFile],
    selections: list[dict],
    cleanup: ExitStack,
) -> tuple[list[SchemaTableInput], list[dict]]:
    files_by_id = {dataset_file.id: dataset_file for dataset_file in dataset_files}
    tables = []
    provenance_inputs = []
    for selection in selections:
        dataset_file = files_by_id[selection["file_id"]]
        try:
            normalized = validate_normalization_result(
                dataset_file.normalization_result,
                dataset_file.parsing_result,
                dataset_file.filename,
                dataset_file.file_size_bytes,
                dataset_file.checksum,
                dataset_file.dataset_id,
                dataset_file.id,
            )
        except NormalizationError:
            raise SchemaAnalysisError(
                "normalized_output_invalid",
                "Stored normalized output metadata failed validation.",
            ) from None

        provenance_inputs.append(
            {
                "kind": "source",
                "dataset_id": dataset_file.dataset_id,
                "file_id": dataset_file.id,
                "sha256": dataset_file.checksum,
                "size_bytes": dataset_file.file_size_bytes,
                "profile_sha256": (
                    canonical_sha256(dataset_file.profile_result)
                    if dataset_file.profile_result is not None
                    else None
                ),
                "quality_report_sha256": (
                    canonical_sha256(dataset_file.quality_report)
                    if dataset_file.quality_report is not None
                    else None
                ),
            }
        )
        for table_index in selection["table_indices"]:
            descriptor = normalized["tables"][table_index]
            output_size = descriptor["file_size_bytes"]
            if output_size > DEFAULT_MAX_OUTPUT_BYTES:
                raise SchemaAnalysisError(
                    "resource_limit",
                    "A normalized artifact exceeds the schema-analysis file-size limit.",
                )
            object_key = descriptor["object_key"]
            response = storage.get_object(bucket, object_key)
            digest = hashlib.sha256()
            actual_size = 0
            temporary = tempfile.NamedTemporaryFile(
                prefix="insightos-schema-",
                suffix=".parquet",
                delete=False,
            )
            path = Path(temporary.name)
            cleanup.callback(path.unlink, missing_ok=True)
            try:
                with temporary:
                    while chunk := response.read(1024 * 1024):
                        actual_size += len(chunk)
                        if actual_size > output_size or actual_size > DEFAULT_MAX_OUTPUT_BYTES:
                            raise SchemaAnalysisError(
                                "normalized_output_invalid",
                                "A normalized artifact exceeded its recorded size.",
                            )
                        digest.update(chunk)
                        temporary.write(chunk)
            finally:
                response.close()
                release_connection = getattr(response, "release_conn", None)
                if release_connection is not None:
                    release_connection()
            if (
                actual_size != output_size
                or digest.hexdigest() != descriptor["sha256"]
            ):
                raise SchemaAnalysisError(
                    "normalized_output_invalid",
                    "A normalized artifact checksum did not match its metadata.",
                )

            try:
                parquet = pq.ParquetFile(path)
                arrow_schema = parquet.schema_arrow
                if (
                    parquet.metadata.num_rows != descriptor["row_count"]
                    or len(arrow_schema) != descriptor["column_count"]
                    or schema_fingerprint(arrow_schema)
                    != schema_fingerprint(descriptor["schema"])
                ):
                    parquet.close()
                    raise SchemaAnalysisError(
                        "normalized_output_invalid",
                        "A normalized artifact schema or row count did not match its metadata.",
                    )
                parquet.close()
            except SchemaAnalysisError:
                raise
            except (OSError, ValueError, TypeError, pa.ArrowException):
                raise SchemaAnalysisError(
                    "normalized_output_invalid",
                    "A normalized artifact could not be read as Parquet.",
                ) from None

            def iter_batches(artifact_path=path):
                reader = pq.ParquetFile(artifact_path)
                try:
                    yield from reader.iter_batches(batch_size=1024)
                finally:
                    reader.close()

            identity = {
                "dataset_id": dataset_file.dataset_id,
                "file_id": dataset_file.id,
                "filename": dataset_file.filename,
                "table_index": table_index,
                "table_name": descriptor.get("table_name"),
            }
            source_profile = dataset_file.profile_result
            profile_tables = (
                source_profile.get("tables")
                if isinstance(source_profile, dict)
                else None
            )
            profile_table = None
            if isinstance(profile_tables, list):
                profile_table = next(
                    (
                        item
                        for item in profile_tables
                        if isinstance(item, dict)
                        and item.get("name") == descriptor.get("table_name")
                    ),
                    None,
                )
            tables.append(
                SchemaTableInput(
                    identity=identity,
                    schema=arrow_schema,
                    iter_batches=iter_batches,
                    source_profile=(
                        {"tables": [profile_table]}
                        if profile_table is not None
                        else None
                    ),
                    normalization_warnings=tuple(
                        value
                        for value in descriptor.get("warnings", [])
                        if isinstance(value, str)
                    ),
                    quality_warnings=_schema_quality_warnings(
                        dataset_file.quality_report,
                        table_index,
                    ),
                )
            )
            provenance_inputs.append(
                {
                    "kind": "normalized_parquet",
                    "dataset_id": dataset_file.dataset_id,
                    "file_id": dataset_file.id,
                    "table_index": table_index,
                    "object_identity_sha256": hashlib.sha256(
                        object_key.encode("utf-8")
                    ).hexdigest(),
                    "sha256": descriptor["sha256"],
                    "size_bytes": output_size,
                    "schema_sha256": schema_fingerprint(descriptor["schema"]),
                }
            )
    return tables, provenance_inputs


def _schema_http_status(code: str) -> int:
    if code in {"invalid_selection", "resource_limit"}:
        return 422
    if code in {
        "invalid_artifact",
        "normalization_unavailable",
        "normalized_output_invalid",
        "source_changed",
    }:
        return 409
    if code == "normalized_output_unverifiable":
        return 503
    return 500


def _run_schema_analysis(
    dataset_id: int,
    payload: SchemaAnalysisRequest,
) -> dict:
    if payload.business_context is not None and not payload.business_context.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_business_context",
                "message": "Business context cannot be blank.",
            },
        )
    with _pinned_upload_session() as (session, connection), ExitStack() as cleanup:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            if dataset.active_upload_key is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "upload_in_progress",
                        "message": "Schema analysis cannot run while a dataset upload is active.",
                    },
                )
            all_files = (
                session.query(DatasetFile)
                .filter(DatasetFile.dataset_id == dataset_id)
                .order_by(DatasetFile.id.asc())
                .all()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Schema-analysis dataset lookup failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset could not be checked for schema analysis.",
            ) from None

        if not all_files and payload.files is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalized_files_unavailable",
                    "message": "The dataset has no normalized files available for schema analysis.",
                },
            )
        if payload.files is None:
            selection_mode = "all"
            requested_file_ids = [item.id for item in all_files]
            requested_tables = {
                item.id: None for item in all_files
            }
        else:
            selection_mode = "explicit"
            requested_file_ids = [item.file_id for item in payload.files]
            if not requested_file_ids or len(set(requested_file_ids)) != len(requested_file_ids):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "invalid_selection",
                        "message": "Select one or more unique dataset files.",
                    },
                )
            requested_tables = {
                item.file_id: item.table_indices for item in payload.files
            }
            if any(indices == [] for indices in requested_tables.values()):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "invalid_selection",
                        "message": "A selected file must include at least one table.",
                    },
                )

        files_by_id = {item.id: item for item in all_files}
        missing_file_id = next(
            (file_id for file_id in requested_file_ids if file_id not in files_by_id),
            None,
        )
        if missing_file_id is not None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "dataset_file_not_found",
                    "message": "A selected dataset file was not found.",
                },
            )

        if len(requested_file_ids) > MAX_SCHEMA_TABLES:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "resource_limit",
                    "message": f"Schema analysis is limited to {MAX_SCHEMA_TABLES} selected files.",
                },
            )

        for file_id in requested_file_ids:
            dataset_file = files_by_id[file_id]
            if (
                dataset_file.status != "Ready"
                or dataset_file.normalization_status != "Ready"
                or not dataset_file.checksum
                or not isinstance(dataset_file.normalization_result, dict)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "normalization_unavailable",
                        "message": "Schema analysis requires ready normalized output for every selected file.",
                        "file_id": file_id,
                    },
                )

        try:
            for file_id in sorted(requested_file_ids):
                for operation in (
                    "schema-analysis",
                    "normalization",
                    "quality-analysis",
                    "eda",
                    "preprocessing",
                ):
                    lock_id = _try_acquire_upload_lock(
                        session,
                        connection,
                        dataset_id,
                        f"{operation}:{file_id}",
                    )
                    if lock_id is None:
                        code = {
                            "schema-analysis": "schema_analysis_in_progress",
                            "normalization": "normalization_in_progress",
                            "quality-analysis": "quality_analysis_in_progress",
                            "eda": "eda_in_progress",
                            "preprocessing": "preprocessing_in_progress",
                        }[operation]
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": code,
                                "message": "A conflicting operation is using a selected dataset file.",
                            },
                        )
                    cleanup.callback(
                        _release_upload_lock,
                        session,
                        connection,
                        lock_id,
                    )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Schema-analysis concurrency protection failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "concurrency_protection_unavailable",
                    "message": "Schema-analysis concurrency protection is temporarily unavailable.",
                },
            ) from None

        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            if dataset.active_upload_key is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "upload_in_progress",
                        "message": "Schema analysis cannot run while a dataset upload is active.",
                    },
                )
            current_files = (
                session.query(DatasetFile)
                .filter(DatasetFile.dataset_id == dataset_id)
                .order_by(DatasetFile.id.asc())
                .all()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Schema-analysis source recheck failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Schema-analysis inputs could not be checked.",
            ) from None

        if selection_mode == "all" and [item.id for item in current_files] != requested_file_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "source_changed",
                    "message": "Dataset files changed while schema analysis was being prepared; retry the request.",
                },
            )
        current_files_by_id = {item.id: item for item in current_files}
        selections = []
        selected_table_count = 0
        for file_id in requested_file_ids:
            dataset_file = current_files_by_id.get(file_id)
            if dataset_file is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "dataset_file_not_found",
                        "message": "A selected dataset file was not found.",
                    },
                )
            if (
                dataset_file.status != "Ready"
                or dataset_file.normalization_status != "Ready"
                or not dataset_file.checksum
                or not isinstance(dataset_file.parsing_result, dict)
                or not isinstance(dataset_file.normalization_result, dict)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "normalization_unavailable",
                        "message": "Schema analysis requires ready normalized output for every selected file.",
                        "file_id": file_id,
                    },
                )
            try:
                normalized = validate_normalization_result(
                    dataset_file.normalization_result,
                    dataset_file.parsing_result,
                    dataset_file.filename,
                    dataset_file.file_size_bytes,
                    dataset_file.checksum,
                    dataset_id,
                    file_id,
                )
            except NormalizationError:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "normalized_output_invalid",
                        "message": "Stored normalized output metadata failed validation.",
                    },
                ) from None
            table_count = len(normalized["tables"])
            requested_indices = requested_tables[file_id]
            if (
                requested_indices is None
                and table_count > MAX_SCHEMA_TABLES - selected_table_count
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "resource_limit",
                        "message": f"Schema analysis is limited to {MAX_SCHEMA_TABLES} selected tables.",
                    },
                )
            if (
                requested_indices is not None
                and len(requested_indices)
                > MAX_SCHEMA_TABLES - selected_table_count
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "resource_limit",
                        "message": f"Schema analysis is limited to {MAX_SCHEMA_TABLES} selected tables.",
                    },
                )
            table_indices = (
                list(range(table_count))
                if requested_indices is None
                else list(requested_indices)
            )
            if (
                not table_indices
                or any(
                    index < 0 or index >= table_count
                    for index in table_indices
                )
                or len(set(table_indices)) != len(table_indices)
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "invalid_selection",
                        "message": "A selected normalized table index does not exist or is duplicated.",
                        "file_id": file_id,
                    },
                )
            selections.append(
                {
                    "file_id": file_id,
                    "table_indices": sorted(table_indices),
                }
            )
            selected_table_count += len(table_indices)

        input_manifest = _schema_input_manifest(current_files, selections)
        total_artifact_bytes = sum(
            table["size_bytes"]
            for source in input_manifest["files"]
            for table in source["tables"]
        )
        selected_tables = [
            table
            for source in input_manifest["files"]
            for table in source["tables"]
        ]
        if any(
            not isinstance(table["row_count"], int)
            or isinstance(table["row_count"], bool)
            or table["row_count"] < 0
            or not isinstance(table["column_count"], int)
            or isinstance(table["column_count"], bool)
            or table["column_count"] < 0
            for table in selected_tables
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalized_output_invalid",
                    "message": "Stored normalized table dimensions are invalid.",
                },
            )
        if any(
            table["column_count"] > MAX_SCHEMA_COLUMNS
            for table in selected_tables
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "resource_limit",
                    "message": (
                        "A selected table exceeds the "
                        f"{MAX_SCHEMA_COLUMNS}-column schema-analysis limit."
                    ),
                },
            )
        if sum(table["row_count"] for table in selected_tables) > MAX_SCHEMA_ROWS:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "resource_limit",
                    "message": (
                        "Selected normalized tables exceed the "
                        f"{MAX_SCHEMA_ROWS}-row schema-analysis limit."
                    ),
                },
            )
        if total_artifact_bytes > MAX_TOTAL_ARTIFACT_BYTES:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "resource_limit",
                    "message": "Selected normalized artifacts exceed the 1 GiB schema-analysis byte budget.",
                },
            )
        configuration = {
            "engine_version": SCHEMA_ARCHITECT_VERSION,
            "selection_mode": selection_mode,
            "dataset_file_ids": (
                [item.id for item in current_files] if selection_mode == "all" else None
            ),
            "files": selections,
            "input_manifest": input_manifest,
            "business_context": (
                payload.business_context.strip()
                if payload.business_context is not None
                else None
            ),
        }
        configuration_sha256 = canonical_sha256(configuration)
        analysis_key = configuration_sha256

        try:
            record = (
                session.query(SchemaAnalysis)
                .filter(
                    SchemaAnalysis.dataset_id == dataset_id,
                    SchemaAnalysis.analysis_key == analysis_key,
                )
                .one_or_none()
            )
            if record is not None and record.status == "Running":
                record.status = "Failed"
                record.result = None
                record.provenance = None
                record.error_code = "execution_interrupted"
                record.error_message = (
                    "A previous schema-analysis request ended before completion."
                )
                record.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                session.commit()
                if not payload.recompute:
                    return _schema_analysis_response(record, stale=False)
            if record is not None and not payload.recompute:
                stale = _schema_analysis_is_stale(
                    session,
                    record,
                    current_files=current_files,
                )
                return _schema_analysis_response(record, stale=stale)
            if record is None:
                record = SchemaAnalysis(
                    dataset_id=dataset_id,
                    analysis_key=analysis_key,
                    status="Requested",
                    configuration=configuration,
                    configuration_sha256=configuration_sha256,
                )
                session.add(record)
            else:
                record.status = "Requested"
                record.configuration = configuration
                record.configuration_sha256 = configuration_sha256
                record.result = None
                record.provenance = None
                record.error_code = None
                record.error_message = None
                record.started_at = None
                record.completed_at = None
            session.commit()
            session.refresh(record)
            record.status = "Running"
            record.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Schema-analysis execution could not be persisted for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Schema analysis could not be started.",
            ) from None

        try:
            storage = get_storage_client()
            processed_bucket = getenv(
                "MINIO_PROCESSED_BUCKET",
                "insightos-processed",
            )
            input_tables, provenance_inputs = _schema_input_tables(
                storage,
                processed_bucket,
                current_files,
                selections,
                cleanup,
            )
            result = analyze_schema(input_tables)
            result["business_context"] = configuration["business_context"]
            if len(
                json.dumps(
                    result,
                    allow_nan=False,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ) > MAX_SCHEMA_RESULT_BYTES:
                raise SchemaAnalysisError(
                    "resource_limit",
                    "Schema-analysis results exceed the 10 MiB response-size limit.",
                )

            latest_files = (
                session.query(DatasetFile)
                .filter(DatasetFile.dataset_id == dataset_id)
                .order_by(DatasetFile.id.asc())
                .all()
            )
            if (
                selection_mode == "all"
                and [item.id for item in latest_files]
                != configuration["dataset_file_ids"]
            ):
                raise SchemaAnalysisError(
                    "source_changed",
                    "Dataset files changed while schema analysis was running.",
                )
            latest_manifest = _schema_input_manifest(latest_files, selections)
            if latest_manifest != input_manifest:
                raise SchemaAnalysisError(
                    "source_changed",
                    "Schema-analysis inputs changed while analysis was running.",
                )

            result_sha256 = canonical_sha256(result)
            record.result = result
            record.provenance = build_provenance(
                operation_type="schema_architect",
                operation_version=SCHEMA_ARCHITECT_VERSION,
                configuration_sha256=configuration_sha256,
                status="completed",
                inputs=provenance_inputs,
                outputs=[
                    {
                        "kind": "schema_analysis",
                        "analysis_key": analysis_key,
                        "result_sha256": result_sha256,
                    }
                ],
                verification_status="verified",
                checks=[
                    {"name": "normalized_artifact_checksums", "status": "verified"},
                    {"name": "source_manifest_stability", "status": "verified"},
                ],
                limitations=result["limitations"],
            )
            record.status = "Completed"
            record.error_code = None
            record.error_message = None
            record.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
            session.refresh(record)
            return _schema_analysis_response(record, stale=False)
        except SchemaAnalysisError as error:
            session.rollback()
            record.status = "Failed"
            record.result = None
            record.provenance = None
            record.error_code = error.code
            record.error_message = error.message
            record.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                session.commit()
            except SQLAlchemyError as persist_error:
                session.rollback()
                logger.error(
                    "Schema-analysis failure state could not be persisted (%s)",
                    type(persist_error).__name__,
                )
            raise HTTPException(
                status_code=_schema_http_status(error.code),
                detail={"code": error.code, "message": error.message},
            ) from None
        except (MinioException, HTTPError, OSError) as error:
            session.rollback()
            logger.error(
                "Schema-analysis artifact access failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            record.status = "Failed"
            record.result = None
            record.provenance = None
            record.error_code = "artifact_storage_failure"
            record.error_message = "A normalized artifact could not be read."
            record.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                session.commit()
            except SQLAlchemyError as persist_error:
                session.rollback()
                logger.error(
                    "Schema-analysis storage failure state could not be persisted (%s)",
                    type(persist_error).__name__,
                )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "artifact_storage_failure",
                    "message": "A normalized artifact could not be read.",
                },
            ) from None
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Schema-analysis result persistence failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            record.status = "Failed"
            record.result = None
            record.provenance = None
            record.error_code = "schema_analysis_persistence_failure"
            record.error_message = "Schema-analysis results could not be saved."
            record.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                session.commit()
            except SQLAlchemyError as persist_error:
                session.rollback()
                logger.error(
                    "Schema-analysis persistence failure state could not be saved (%s)",
                    type(persist_error).__name__,
                )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "schema_analysis_persistence_failure",
                    "message": "Schema-analysis results could not be saved.",
                },
            ) from None
        except Exception as error:
            session.rollback()
            logger.error(
                "Schema-analysis execution failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            record.status = "Failed"
            record.result = None
            record.provenance = None
            record.error_code = "schema_analysis_failed"
            record.error_message = "Schema analysis could not be completed."
            record.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            try:
                session.commit()
            except SQLAlchemyError as persist_error:
                session.rollback()
                logger.error(
                    "Schema-analysis failure state could not be persisted (%s)",
                    type(persist_error).__name__,
                )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "schema_analysis_failed",
                    "message": "Schema analysis could not be completed.",
                },
            ) from None


@app.post("/api/v1/datasets/{dataset_id}/schema-analyses")
def analyze_dataset_schema(
    dataset_id: int,
    payload: SchemaAnalysisRequest,
):
    return _run_schema_analysis(dataset_id, payload)


@app.get("/api/v1/datasets/{dataset_id}/schema-analyses")
def list_dataset_schema_analyses(
    dataset_id: int,
    limit: int = 50,
    offset: int = 0,
):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100.")
    if offset < 0:
        raise HTTPException(
            status_code=422,
            detail="offset must be greater than or equal to 0.",
        )
    with SessionLocal() as session:
        try:
            if session.get(Dataset, dataset_id) is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            query = session.query(SchemaAnalysis).filter(
                SchemaAnalysis.dataset_id == dataset_id
            )
            total = query.count()
            records = (
                query.order_by(SchemaAnalysis.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            files = (
                session.query(DatasetFile)
                .filter(DatasetFile.dataset_id == dataset_id)
                .order_by(DatasetFile.id.asc())
                .all()
            )
            return {
                "dataset_id": dataset_id,
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [
                    _schema_analysis_summary(
                        record,
                        stale=_schema_analysis_is_stale(
                            session,
                            record,
                            current_files=files,
                        ),
                    )
                    for record in records
                ],
            }
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Schema-analysis listing failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Schema analyses could not be retrieved.",
            ) from None


def _get_schema_analysis(
    session: Session,
    dataset_id: int,
    analysis_id: int,
) -> tuple[SchemaAnalysis, bool]:
    if session.get(Dataset, dataset_id) is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    record = (
        session.query(SchemaAnalysis)
        .filter(
            SchemaAnalysis.id == analysis_id,
            SchemaAnalysis.dataset_id == dataset_id,
        )
        .one_or_none()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Schema analysis not found.")
    files = (
        session.query(DatasetFile)
        .filter(DatasetFile.dataset_id == dataset_id)
        .order_by(DatasetFile.id.asc())
        .all()
    )
    return record, _schema_analysis_is_stale(session, record, current_files=files)


@app.get("/api/v1/datasets/{dataset_id}/schema-analyses/{analysis_id}")
def get_dataset_schema_analysis(dataset_id: int, analysis_id: int):
    with SessionLocal() as session:
        try:
            record, stale = _get_schema_analysis(session, dataset_id, analysis_id)
            return _schema_analysis_response(record, stale=stale)
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Schema-analysis lookup failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Schema analysis could not be retrieved.",
            ) from None


# =============================================================================
# Stage 3.8: Preprocessing Execution, Derived Artifacts, and Provenance
# =============================================================================

def _validate_preprocessing_step(step_dict: Any) -> PreprocessingStep:
    if not isinstance(step_dict, dict):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_plan", "message": "Each preprocessing step must be an object."},
        )
    allowed = {"step_id", "operation_id", "configuration", "fit_context"}
    if set(step_dict) - allowed:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_configuration", "message": "Preprocessing step has unsupported fields."},
        )
    step_id = step_dict.get("step_id")
    if not isinstance(step_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", step_id):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_step_id", "message": "Step ID must be 1-64 alphanumeric, dash, or underscore characters."},
        )
    operation_id = step_dict.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_operation_id", "message": "Operation ID must be a non-empty string."},
        )
    config = step_dict.get("configuration")
    if config is not None and not isinstance(config, dict):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_configuration", "message": "Step configuration must be an object."},
        )
    fit_context = step_dict.get("fit_context")
    if fit_context is not None and not isinstance(fit_context, dict):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_fit_context", "message": "Fitted context must be an object."},
        )
    return PreprocessingStep(
        step_id=step_id,
        operation_id=operation_id,
        configuration=config,
        fit_context=fit_context,
    )


def _validate_preprocessing_approval(approval_dict: Any) -> PreprocessingApproval:
    if not isinstance(approval_dict, dict):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_approval", "message": "Each approval record must be an object."},
        )
    allowed = {"step_id", "operation_id", "configuration", "approved_approvals"}
    if set(approval_dict) - allowed:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_configuration", "message": "Preprocessing approval has unsupported fields."},
        )
    step_id = approval_dict.get("step_id")
    if not isinstance(step_id, str) or not step_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_step_id", "message": "Approval step ID must be a non-empty string."},
        )
    operation_id = approval_dict.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_operation_id", "message": "Approval operation ID must be a non-empty string."},
        )
    config = approval_dict.get("configuration")
    if config is not None and not isinstance(config, dict):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_configuration", "message": "Approval configuration must be an object."},
        )
    tokens = approval_dict.get("approved_approvals", ())
    if not isinstance(tokens, (list, tuple)) or not all(isinstance(t, str) for t in tokens):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_approvals", "message": "Approved approvals must be a list of string tokens."},
        )
    return PreprocessingApproval(
        step_id=step_id,
        operation_id=operation_id,
        configuration=config,
        approved_approvals=tuple(tokens),
    )


def _preprocessing_plan_sha256(raw_steps: list[dict], raw_approvals: list[dict]) -> str:
    canonical = {
        "steps": [
            {
                "step_id": s["step_id"],
                "operation_id": s["operation_id"],
                "configuration": s.get("configuration") or {},
                "has_fit_context": bool(s.get("fit_context")),
            }
            for s in raw_steps
        ],
        "approvals": [
            {
                "step_id": a["step_id"],
                "operation_id": a["operation_id"],
                "configuration": a.get("configuration") or {},
                "approved_approvals": sorted(a.get("approved_approvals") or []),
            }
            for a in raw_approvals
        ],
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _preprocessing_execution_key(
    source_sha256: str,
    table_index: int,
    normalized_table_sha256: str,
    plan_sha256: str,
    engine_version: str = "1",
) -> str:
    raw = f"{source_sha256}:{table_index}:{normalized_table_sha256}:{plan_sha256}:{engine_version}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _preprocessing_execution_response(record: PreprocessingExecution) -> dict:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "file_id": record.file_id,
        "table_index": record.table_index,
        "execution_key": record.execution_key,
        "status": record.status,
        "plan": record.plan,
        "artifact": (
            {
                "storage_key": record.artifact_storage_key,
                "sha256": record.artifact_sha256,
                "size_bytes": record.artifact_size_bytes,
                "rows": record.artifact_rows,
                "columns": record.artifact_columns,
            }
            if record.artifact_storage_key is not None
            else None
        ),
        "result_summary": record.result_summary,
        "provenance": record.provenance,
        "error": (
            {
                "code": record.error_code,
                "message": record.error_message,
            }
            if record.error_code is not None
            else None
        ),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _preprocessing_summary_response(record: PreprocessingExecution) -> dict:
    return {
        "id": record.id,
        "dataset_id": record.dataset_id,
        "file_id": record.file_id,
        "table_index": record.table_index,
        "execution_key": record.execution_key,
        "status": record.status,
        "artifact": (
            {
                "storage_key": record.artifact_storage_key,
                "sha256": record.artifact_sha256,
                "size_bytes": record.artifact_size_bytes,
                "rows": record.artifact_rows,
                "columns": record.artifact_columns,
            }
            if record.artifact_storage_key is not None
            else None
        ),
        "error": (
            {
                "code": record.error_code,
                "message": record.error_message,
            }
            if record.error_code is not None
            else None
        ),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def _preprocessing_result_summary(result: PreprocessingResult) -> dict:
    return {
        "status": result.status,
        "execution_order": list(result.execution_order),
        "completed_steps": list(result.completed_steps),
        "unexecuted_steps": list(result.unexecuted_steps),
        "failure_step_id": result.failure_step_id,
        "error_description": result.error_description,
        "steps": [
            {
                "step_id": step.step_id,
                "operation_id": step.operation_id,
                "operation_version": step.operation_version,
                "configuration_sha256": step.configuration_sha256,
                "status": step.status,
                "rows_before": step.rows_before,
                "rows_after": step.rows_after,
                "columns_before": step.columns_before,
                "columns_after": step.columns_after,
                "affected_columns": list(step.affected_columns),
                "summary_metadata": step.summary_metadata,
            }
            for step in result.steps
        ],
    }


def _assess_verified_preprocessing_lineage(
    provenance: dict,
    *,
    dataset_id: int,
    file_id: int,
    source_sha256: str,
    source_size_bytes: int,
    table: dict,
    table_index: int,
    schema: list[dict[str, str]] | None,
) -> VerificationAssessment:
    verified_inputs = [
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
            "table_index": table_index,
            "object_key": table.get("object_key"),
            "sha256": table.get("sha256"),
            "size_bytes": table.get("file_size_bytes"),
            "schema_sha256": (
                schema_fingerprint(schema)
                if isinstance(schema, list)
                else None
            ),
        },
    ]
    claimed_inputs = provenance.get("inputs") if isinstance(provenance, dict) else None
    evidence = evidence_for(
        claimed_inputs,
        verified_inputs,
        reference="preprocessing-lineage:verified-object-readback",
    )
    budget = ExecutionBudget(
        {
            "jev_checks": 1,
            "jev_evidence_bytes": 16 * 1024,
        }
    )
    return verify_claim(
        claimed_inputs,
        evidence,
        policy_for("schema"),
        budget=budget,
    )


def _log_internal_preprocessing_lineage_assessment(
    provenance: dict,
    *,
    dataset_id: int,
    file_id: int,
    source_sha256: str,
    source_size_bytes: int,
    table: dict,
    table_index: int,
    schema: list[dict[str, str]] | None,
) -> None:
    try:
        assessment = _assess_verified_preprocessing_lineage(
            provenance,
            dataset_id=dataset_id,
            file_id=file_id,
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
            table=table,
            table_index=table_index,
            schema=schema,
        )
    except (TypeError, ValueError, OverflowError) as error:
        logger.warning(
            "Internal preprocessing lineage assessment unavailable for file %s (%s)",
            file_id,
            type(error).__name__,
        )
        return
    if assessment.status != "verified":
        logger.warning(
            "Internal preprocessing lineage assessment did not verify for file %s (%s)",
            file_id,
            assessment.reason_code,
        )


@app.get("/api/v1/preprocessing/operations")
def list_preprocessing_operations():
    operations = [
        {
            "operation_id": op.operation_id,
            "version": op.version,
            "category": op.category,
            "status": op.status,
            "input_requirements": list(op.input_requirements),
            "output_requirements": list(op.output_requirements),
            "prerequisites": list(op.prerequisites),
            "required_approvals": list(op.required_approvals),
            "requires_fit": op.requires_fit,
            "changes_rows": op.changes_rows,
            "changes_columns": op.changes_columns,
            "changes_values": op.changes_values,
            "changes_metadata": op.changes_metadata,
            "configuration_defaults": op.configuration_defaults,
            "resource_limits": dict(op.resource_limits) if isinstance(op.resource_limits, dict) else {},
        }
        for op in DEFAULT_TRANSFORMATION_REGISTRY.list_operations()
    ]
    return {"operations": operations, "total": len(operations)}


@app.post("/api/v1/datasets/{dataset_id}/files/{file_id}/preprocess")
def preprocess_dataset_file(
    dataset_id: int,
    file_id: int,
    request: dict = Body(default_factory=dict),
    recompute: bool = False,
):
    recompute = recompute or bool(request.get("recompute", False))
    allowed_keys = {"table_index", "steps", "approvals", "recompute"}
    if set(request) - allowed_keys:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_configuration",
                "message": "Preprocessing request has unsupported fields.",
            },
        )
    raw_table_index = request.get("table_index", 0)
    if not isinstance(raw_table_index, int) or isinstance(raw_table_index, bool) or raw_table_index < 0:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_table_index",
                "message": "table_index must be a non-negative integer.",
            },
        )
    table_index = raw_table_index

    raw_steps = request.get("steps")
    if not isinstance(raw_steps, list):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_plan",
                "message": "Preprocessing steps must be a list.",
            },
        )
    if len(raw_steps) > 32:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_plan",
                "message": "Preprocessing plan cannot exceed 32 steps.",
            },
        )

    raw_approvals = request.get("approvals", [])
    if not isinstance(raw_approvals, list):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_approvals",
                "message": "Preprocessing approvals must be a list.",
            },
        )
    if len(raw_approvals) > 32:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_approvals",
                "message": "Preprocessing approvals cannot exceed 32 items.",
            },
        )

    steps = [_validate_preprocessing_step(s) for s in raw_steps]
    approvals = [_validate_preprocessing_approval(a) for a in raw_approvals]
    plan_dict = {"table_index": table_index, "steps": raw_steps, "approvals": raw_approvals}
    plan_sha256 = _preprocessing_plan_sha256(raw_steps, raw_approvals)

    with _pinned_upload_session() as (session, connection), ExitStack() as cleanup:
        try:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
            dataset_file = (
                session.query(DatasetFile)
                .filter(
                    DatasetFile.id == file_id,
                    DatasetFile.dataset_id == dataset_id,
                )
                .one_or_none()
            )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Preprocessing file lookup failed for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset file could not be checked for preprocessing.",
            ) from None

        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")
        if dataset_file.status != "Ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "file_not_ready",
                    "message": "Preprocessing requires successful file ingestion.",
                    "status": dataset_file.status,
                },
            )
        if dataset_file.normalization_status != "Ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalization_unavailable",
                    "message": "Preprocessing requires ready normalized output.",
                    "status": dataset_file.normalization_status,
                },
            )
        if (
            not dataset_file.checksum
            or not dataset_file.parsing_result
            or not isinstance(dataset_file.normalization_result, dict)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "analysis_provenance_unavailable",
                    "message": "Validated source and normalized metadata are required.",
                },
            )

        try:
            prep_lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"preprocessing:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve preprocessing lock for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Preprocessing concurrency protection is unavailable.",
            ) from None
        if prep_lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preprocessing_in_progress",
                    "message": "Preprocessing is already in progress for this file.",
                },
            )
        cleanup.callback(_release_upload_lock, session, connection, prep_lock_id)

        try:
            norm_lock_id = _try_acquire_upload_lock(
                session,
                connection,
                dataset_id,
                f"normalization:{file_id}",
            )
        except SQLAlchemyError as error:
            logger.error(
                "Could not check normalization lock for file %s (%s)",
                file_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Normalization concurrency protection is unavailable.",
            ) from None
        if norm_lock_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalization_in_progress",
                    "message": "Normalization is using this file; retry preprocessing later.",
                },
            )
        cleanup.callback(_release_upload_lock, session, connection, norm_lock_id)

        try:
            normalized = validate_normalization_result(
                dataset_file.normalization_result,
                dataset_file.parsing_result,
                dataset_file.filename,
                dataset_file.file_size_bytes,
                dataset_file.checksum,
                dataset_id,
                file_id,
            )
        except NormalizationError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "normalized_output_invalid",
                    "message": "Stored normalized output metadata failed validation.",
                },
            ) from error

        tables = normalized["tables"]
        if table_index >= len(tables):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "unknown_table",
                    "message": "Selected normalized table does not exist.",
                },
            )
        selected_table = tables[table_index]
        if not selected_table.get("sha256"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "artifact_unavailable",
                    "message": "Selected normalized table has no verified checksum.",
                },
            )

        storage = get_storage_client()
        raw_bucket = getenv("MINIO_RAW_BUCKET", "insightos-raw")
        processed_bucket = getenv("MINIO_PROCESSED_BUCKET", "insightos-processed")

        if not _verify_stored_object(storage, raw_bucket, dataset_file):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "source_integrity_failed",
                    "message": "Stored source data failed integrity verification.",
                },
            )

        normalized_verification = verify_normalization_result_detailed(
            storage,
            processed_bucket,
            normalized,
        )
        if normalized_verification["status"] != "verified":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": f"normalized_output_{normalized_verification['status']}",
                    "message": "Stored normalized output could not be verified as intact.",
                },
            )

        execution_key = _preprocessing_execution_key(
            dataset_file.checksum,
            table_index,
            selected_table["sha256"],
            plan_sha256,
            engine_version="1",
        )

        existing_record = (
            session.query(PreprocessingExecution)
            .filter(
                PreprocessingExecution.file_id == file_id,
                PreprocessingExecution.execution_key == execution_key,
            )
            .one_or_none()
        )

        if (
            not recompute
            and existing_record is not None
            and existing_record.status == "Completed"
            and existing_record.artifact_storage_key
        ):
            try:
                stat = storage.stat_object(processed_bucket, existing_record.artifact_storage_key)
                if stat.size == existing_record.artifact_size_bytes:
                    _log_internal_preprocessing_lineage_assessment(
                        existing_record.provenance or {},
                        dataset_id=dataset_id,
                        file_id=file_id,
                        source_sha256=dataset_file.checksum,
                        source_size_bytes=dataset_file.file_size_bytes,
                        table=selected_table,
                        table_index=table_index,
                        schema=selected_table.get("schema"),
                    )
                    return _preprocessing_execution_response(existing_record)
            except Exception:
                pass

        started_at = datetime.now(timezone.utc)
        if existing_record is None:
            record = PreprocessingExecution(
                dataset_id=dataset_id,
                file_id=file_id,
                table_index=table_index,
                execution_key=execution_key,
                status="Running",
                plan=plan_dict,
                plan_sha256=plan_sha256,
                started_at=started_at,
            )
            session.add(record)
        else:
            record = existing_record
            record.status = "Running"
            record.started_at = started_at
            record.completed_at = None
            record.error_code = None
            record.error_message = None
            record.artifact_storage_key = None
            record.artifact_sha256 = None
            record.artifact_size_bytes = None
            record.artifact_rows = None
            record.artifact_columns = None
            record.result_summary = None
            record.provenance = None
        session.commit()

        object_key = selected_table["object_key"]
        expected_prefix = (
            f"{dataset_id}/{file_id}/{dataset_file.checksum}/"
            f"{normalized['configuration']['configuration_sha256']}/"
            f"table-{table_index:04d}-"
        )
        if not object_key.startswith(expected_prefix) or not object_key.endswith(".parquet"):
            record.status = "Failed"
            record.error_code = "invalid_storage_boundary"
            record.error_message = "Normalized artifact key is outside its managed prefix."
            record.completed_at = datetime.now(timezone.utc)
            session.commit()
            raise HTTPException(
                status_code=409,
                detail={"code": record.error_code, "message": record.error_message},
            )

        temp_in_path = None
        temp_out_path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="insightos-prep-in-",
                suffix=".parquet",
                delete=False,
            ) as in_file:
                temp_in_path = Path(in_file.name)
                download_stream = storage.get_object(processed_bucket, object_key)
                try:
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := download_stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > selected_table["file_size_bytes"] or size > DEFAULT_MAX_OUTPUT_BYTES:
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "code": "artifact_size_mismatch",
                                    "message": "Stored normalized artifact exceeded its verified size.",
                                },
                            )
                        digest.update(chunk)
                        in_file.write(chunk)
                    in_file.flush()
                finally:
                    download_stream.close()
                    if hasattr(download_stream, "release_conn"):
                        download_stream.release_conn()

            if size != selected_table["file_size_bytes"] or digest.hexdigest() != selected_table["sha256"]:
                record.status = "Failed"
                record.error_code = "artifact_integrity_failed"
                record.error_message = "Stored normalized artifact failed checksum verification."
                record.completed_at = datetime.now(timezone.utc)
                session.commit()
                raise HTTPException(
                    status_code=409,
                    detail={"code": record.error_code, "message": record.error_message},
                )

            try:
                table_arrow = pq.read_table(str(temp_in_path))
                _validate_orchestration_table(table_arrow)
            except PreprocessingOrchestrationError as error:
                record.status = "Failed"
                record.error_code = "table_validation_failed"
                record.error_message = str(error)
                record.completed_at = datetime.now(timezone.utc)
                session.commit()
                raise HTTPException(
                    status_code=400,
                    detail={"code": record.error_code, "message": record.error_message},
                ) from error

            try:
                result = orchestrate_preprocessing(
                    table_arrow,
                    steps,
                    approvals=approvals,
                    available_prerequisites=(
                        "explicit_column_selection",
                        "explicit_business_rule_definition",
                    ),
                )
            except PreprocessingOrchestrationError as error:
                record.status = "Failed"
                record.error_code = "invalid_plan"
                record.error_message = str(error)
                record.completed_at = datetime.now(timezone.utc)
                session.commit()
                raise HTTPException(
                    status_code=400,
                    detail={"code": record.error_code, "message": record.error_message},
                ) from error

            if result.status == "completed" and result.final_table is not None:
                with tempfile.NamedTemporaryFile(
                    prefix="insightos-prep-out-",
                    suffix=".parquet",
                    delete=False,
                ) as out_file:
                    temp_out_path = Path(out_file.name)
                    pq.write_table(result.final_table, str(temp_out_path), compression="snappy")

                out_size = temp_out_path.stat().st_size
                if out_size > DEFAULT_MAX_OUTPUT_BYTES:
                    record.status = "Failed"
                    record.error_code = "derived_artifact_size_exceeded"
                    record.error_message = "Derived artifact exceeds output size limits."
                    record.completed_at = datetime.now(timezone.utc)
                    session.commit()
                    raise HTTPException(
                        status_code=400,
                        detail={"code": record.error_code, "message": record.error_message},
                    )

                out_digest = hashlib.sha256()
                with open(temp_out_path, "rb") as f:
                    while chunk := f.read(1024 * 1024):
                        out_digest.update(chunk)
                out_sha256 = out_digest.hexdigest()

                artifact_key = f"{dataset_id}/{file_id}/preprocessing/{record.id}/table-{table_index:04d}-{out_sha256}.parquet"

                with open(temp_out_path, "rb") as f:
                    storage.put_object(
                        processed_bucket,
                        artifact_key,
                        f,
                        out_size,
                        content_type="application/vnd.apache.parquet",
                        metadata={"x-amz-meta-sha256": out_sha256},
                    )

                stat = storage.stat_object(processed_bucket, artifact_key)
                if stat.size != out_size:
                    raise OSError("Stored artifact size mismatch after upload.")

                provenance = copy.deepcopy(result.provenance) if isinstance(result.provenance, dict) else {}
                provenance["verification_status"] = "verified"
                inputs = provenance.get("inputs", [])
                inputs.insert(
                    0,
                    {
                        "kind": "normalized_parquet",
                        "dataset_id": dataset_id,
                        "file_id": file_id,
                        "table_index": table_index,
                        "object_key": selected_table.get("object_key"),
                        "sha256": selected_table.get("sha256"),
                        "size_bytes": selected_table.get("file_size_bytes"),
                    },
                )
                inputs.insert(
                    0,
                    {
                        "kind": "source",
                        "dataset_id": dataset_id,
                        "file_id": file_id,
                        "sha256": dataset_file.checksum,
                        "size_bytes": dataset_file.file_size_bytes,
                    },
                )
                provenance["inputs"] = inputs

                outputs = provenance.get("outputs", [])
                outputs.insert(
                    0,
                    {
                        "kind": "derived_parquet",
                        "dataset_id": dataset_id,
                        "file_id": file_id,
                        "table_index": table_index,
                        "object_key": artifact_key,
                        "sha256": out_sha256,
                        "size_bytes": out_size,
                        "row_count": result.final_table.num_rows,
                        "column_count": result.final_table.num_columns,
                    },
                )
                provenance["outputs"] = outputs

                norm_op_id = None
                if isinstance(normalized.get("provenance"), dict):
                    norm_op_id = normalized["provenance"].get("operation_id")
                parent_ops = provenance.get("parent_operation_ids", [])
                if norm_op_id and norm_op_id not in parent_ops:
                    parent_ops.insert(0, norm_op_id)
                provenance["parent_operation_ids"] = parent_ops

                checks = provenance.get("checks", [])
                checks.extend([
                    {"name": "input_normalized_parquet_checksum", "status": "verified"},
                    {"name": "derived_parquet_checksum", "status": "verified"},
                ])
                provenance["checks"] = checks

                record.status = "Completed"
                record.artifact_storage_key = artifact_key
                record.artifact_sha256 = out_sha256
                record.artifact_size_bytes = out_size
                record.artifact_rows = result.final_table.num_rows
                record.artifact_columns = result.final_table.num_columns
                record.result_summary = _preprocessing_result_summary(result)
                record.provenance = provenance
                record.completed_at = datetime.now(timezone.utc)
                session.commit()

                _log_internal_preprocessing_lineage_assessment(
                    provenance,
                    dataset_id=dataset_id,
                    file_id=file_id,
                    source_sha256=dataset_file.checksum,
                    source_size_bytes=dataset_file.file_size_bytes,
                    table=selected_table,
                    table_index=table_index,
                    schema=selected_table.get("schema"),
                )
                return _preprocessing_execution_response(record)

            else:
                record.status = "Failed"
                record.error_code = "step_execution_failed"
                record.error_message = result.error_description
                record.result_summary = _preprocessing_result_summary(result)
                record.provenance = result.provenance
                record.completed_at = datetime.now(timezone.utc)
                session.commit()
                return _preprocessing_execution_response(record)

        except (MinioException, HTTPError, OSError) as error:
            logger.error(
                "Storage failure during preprocessing of file %s (%s)",
                file_id,
                type(error).__name__,
            )
            record.status = "Failed"
            record.error_code = "storage_error"
            record.error_message = "Storage error during preprocessing artifact transfer."
            record.completed_at = datetime.now(timezone.utc)
            session.commit()
            raise HTTPException(
                status_code=502,
                detail="Preprocessing artifact could not be transferred in storage.",
            ) from None
        finally:
            if temp_in_path and temp_in_path.exists():
                try:
                    temp_in_path.unlink()
                except OSError:
                    pass
            if temp_out_path and temp_out_path.exists():
                try:
                    temp_out_path.unlink()
                except OSError:
                    pass


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}/preprocess")
def list_dataset_file_preprocess_executions(
    dataset_id: int,
    file_id: int,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
):
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_pagination", "message": "Limit must be between 1 and 100."},
        )
    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_pagination", "message": "Offset must be greater than or equal to 0."},
        )
    if status is not None and status not in ("Requested", "Running", "Completed", "Failed"):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_status", "message": "Invalid execution status filter."},
        )

    with SessionLocal() as session:
        dataset = session.get(Dataset, dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found.")
        dataset_file = (
            session.query(DatasetFile)
            .filter(
                DatasetFile.id == file_id,
                DatasetFile.dataset_id == dataset_id,
            )
            .one_or_none()
        )
        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")

        query = (
            session.query(PreprocessingExecution)
            .filter(
                PreprocessingExecution.dataset_id == dataset_id,
                PreprocessingExecution.file_id == file_id,
            )
        )
        if status is not None:
            query = query.filter(PreprocessingExecution.status == status)

        total = query.count()
        records = (
            query.order_by(PreprocessingExecution.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_preprocessing_summary_response(record) for record in records],
        }


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}/preprocess/{execution_id}")
def get_dataset_file_preprocess_execution(
    dataset_id: int,
    file_id: int,
    execution_id: int,
):
    with SessionLocal() as session:
        dataset = session.get(Dataset, dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found.")
        dataset_file = (
            session.query(DatasetFile)
            .filter(
                DatasetFile.id == file_id,
                DatasetFile.dataset_id == dataset_id,
            )
            .one_or_none()
        )
        if dataset_file is None:
            raise HTTPException(status_code=404, detail="Dataset file not found.")

        record = (
            session.query(PreprocessingExecution)
            .filter(
                PreprocessingExecution.id == execution_id,
                PreprocessingExecution.dataset_id == dataset_id,
                PreprocessingExecution.file_id == file_id,
            )
            .one_or_none()
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Preprocessing execution not found.")

        return _preprocessing_execution_response(record)


@app.get("/api/v1/datasets/{dataset_id}/files/{file_id}/preprocess/{execution_id}/artifact")
def download_preprocessed_artifact(
    dataset_id: int,
    file_id: int,
    execution_id: int,
):
    with _pinned_upload_session() as (session, connection):
        record = (
            session.query(PreprocessingExecution)
            .filter(
                PreprocessingExecution.id == execution_id,
                PreprocessingExecution.dataset_id == dataset_id,
                PreprocessingExecution.file_id == file_id,
            )
            .one_or_none()
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Preprocessing execution not found.")
        if record.status != "Completed" or not record.artifact_storage_key:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "artifact_unavailable",
                    "message": "Derived preprocessing artifact is not ready or failed.",
                },
            )
        storage = get_storage_client()
        processed_bucket = getenv("MINIO_PROCESSED_BUCKET", "insightos-processed")
        try:
            stored = storage.get_object(processed_bucket, record.artifact_storage_key)
        except (MinioException, HTTPError, OSError):
            raise HTTPException(
                status_code=502,
                detail="Could not retrieve derived artifact from storage.",
            ) from None

        def stream_content():
            try:
                while chunk := stored.read(64 * 1024):
                    yield chunk
            finally:
                stored.close()
                if hasattr(stored, "release_conn"):
                    stored.release_conn()

        filename = f"preprocessed-table-{record.table_index:04d}-{record.artifact_sha256[:8]}.parquet"
        return StreamingResponse(
            stream_content(),
            media_type="application/vnd.apache.parquet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(record.artifact_size_bytes or ""),
            },
        )



def _remove_dataset_objects(storage, bucket: str, dataset_id: int) -> list:
    if not storage.bucket_exists(bucket):
        return []
    return list(
        storage.remove_objects(
            bucket,
            (
                DeleteObject(obj.object_name)
                for obj in storage.list_objects(
                    bucket,
                    prefix=f"{dataset_id}/",
                    recursive=True,
                )
            ),
        )
    )


@app.delete("/api/v1/datasets/{dataset_id}")
def delete_dataset(dataset_id: int):
    with _pinned_upload_session() as (session, connection), ExitStack() as cleanup:
        try:
            dataset = (
                session.query(Dataset)
                .filter(Dataset.id == dataset_id)
                .with_for_update()
                .one_or_none()
            )
        except SQLAlchemyError as error:
            logger.error(
                "Dataset lookup failed for deletion of dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset could not be deleted.",
            ) from None

        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found.")

        if dataset.active_upload_key is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "upload_in_progress",
                    "message": (
                        "A file is being uploaded to this dataset; retry "
                        "dataset deletion after the upload finishes."
                    ),
                },
            )

        try:
            file_ids = [
                row[0]
                for row in (
                    session.query(DatasetFile.id)
                    .filter(DatasetFile.dataset_id == dataset_id)
                    .order_by(DatasetFile.id.asc())
                    .all()
                )
            ]
            for file_id in file_ids:
                for operation in ("normalization", "quality-analysis", "eda", "preprocessing"):
                    lock_id = _try_acquire_upload_lock(
                        session,
                        connection,
                        dataset_id,
                        f"{operation}:{file_id}",
                    )
                    if lock_id is None:
                        if operation == "quality-analysis":
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "code": "quality_analysis_in_progress",
                                    "message": (
                                        "A dataset file is being analyzed; retry "
                                        "dataset deletion after it finishes."
                                    ),
                                },
                            )
                        if operation == "eda":
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "code": "eda_in_progress",
                                    "message": (
                                        "A dataset file is undergoing EDA; retry "
                                        "dataset deletion after it finishes."
                                    ),
                                },
                            )
                        if operation == "preprocessing":
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "code": "preprocessing_in_progress",
                                    "message": (
                                        "A dataset file is undergoing preprocessing; retry "
                                        "dataset deletion after it finishes."
                                    ),
                                },
                            )
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "normalization_in_progress",
                                "message": (
                                    "A dataset file is being normalized; retry "
                                    "dataset deletion after it finishes."
                                ),
                            },
                        )
                    cleanup.callback(
                        _release_upload_lock,
                        session,
                        connection,
                        lock_id,
                    )
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Could not reserve dataset deletion for %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Dataset deletion concurrency protection is unavailable.",
            ) from None

        buckets = list(
            dict.fromkeys(
                (
                    getenv("MINIO_PROCESSED_BUCKET", "insightos-processed"),
                    getenv("MINIO_RAW_BUCKET", "insightos-raw"),
                )
            )
        )
        try:
            storage = get_storage_client()
            delete_errors = []
            for bucket in buckets:
                delete_errors.extend(
                    _remove_dataset_objects(storage, bucket, dataset_id)
                )
        except (MinioException, HTTPError, OSError) as error:
            session.rollback()
            logger.error(
                "Dataset object deletion failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=502,
                detail="Dataset objects could not be removed from storage.",
            ) from None

        if delete_errors:
            session.rollback()
            logger.error(
                "Object storage reported deletion errors for dataset %s",
                dataset_id,
            )
            raise HTTPException(
                status_code=502,
                detail="Dataset objects could not be removed from storage.",
            )

        try:
            session.query(DatasetFile).filter(
                DatasetFile.dataset_id == dataset_id
            ).delete(synchronize_session=False)
            session.delete(dataset)
            session.commit()
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Database deletion failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Dataset metadata could not be deleted.",
            ) from None

    return {"deleted": True, "dataset_id": dataset_id}


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "InsightOS",
        "version": "0.1.0",
    }


@app.get("/health/database")
def database_health_check():
    if not check_database():
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": "postgresql"},
        )

    return {"status": "healthy", "database": "postgresql"}


@app.get("/health/redis")
def redis_health_check():
    if not check_redis():
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "redis": "unavailable"},
        )

    return {"status": "healthy", "redis": "available"}


@app.get("/health/storage")
def storage_health_check():
    healthy, buckets = check_storage()
    if not healthy:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "storage": "minio",
                "buckets": buckets,
            },
        )

    return {
        "status": "healthy",
        "storage": "minio",
        "buckets": buckets,
    }


@app.get("/api/v1/system/info")
def system_info():
    database_healthy = check_database()
    redis_healthy = check_redis()
    storage_healthy, _ = check_storage()

    return {
        "application": {
            "name": getenv("APP_NAME", "InsightOS"),
            "version": getenv("APP_VERSION", "0.1.0"),
            "environment": getenv("ENVIRONMENT", "development"),
        },
        "services": {
            "backend": "healthy",
            "database": "healthy" if database_healthy else "unhealthy",
            "redis": "healthy" if redis_healthy else "unhealthy",
            "storage": "healthy" if storage_healthy else "unhealthy",
        },
    }