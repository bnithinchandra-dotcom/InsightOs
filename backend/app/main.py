import hashlib
import json
import logging
import math
import os
import re
import tempfile
from threading import Lock
import unicodedata
from contextlib import ExitStack, contextmanager
from email.message import Message
from os import getenv

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import MinioException, S3Error
from pydantic import BaseModel, Field
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send
from urllib3.exceptions import HTTPError

from app.database import SessionLocal
from app.models import Dataset, DatasetFile, Project
from app.normalization import (
    NormalizationError,
    get_normalization_configuration,
    normalize_file,
    validate_normalization_result,
    verify_normalization_result,
)
from app.parsing import (
    ParserConfigurationError,
    ParsingError,
    parse_dataset_file,
)
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
                lock_id = _try_acquire_upload_lock(
                    session,
                    connection,
                    dataset_id,
                    f"normalization:{file_id}",
                )
                if lock_id is None:
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