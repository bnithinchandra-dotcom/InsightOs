import hashlib
import logging
import math
import re
import unicodedata
from email.message import Message
from os import getenv
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import MinioException, S3Error
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send
from urllib3.exceptions import HTTPError

from app.database import SessionLocal
from app.models import Dataset, DatasetFile, Project
from app.service_health import check_database, check_redis, check_storage


logger = logging.getLogger(__name__)

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


def mark_dataset_failed(dataset_id: int, session: Session) -> None:
    try:
        dataset = session.get(Dataset, dataset_id)
        if dataset is not None:
            dataset.status = "Failed"
            session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.error(
            "Could not mark dataset %s as failed (%s)",
            dataset_id,
            type(error).__name__,
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
            detail="Unsupported file extension. Accepted formats are CSV, TSV, XLSX, JSON, and Parquet.",
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
):
    if getattr(request.state, "unsafe_upload_filename", False):
        raise HTTPException(
            status_code=400,
            detail="Path separators are not allowed in filenames.",
        )

    filename = get_upload_filename(file.filename)
    validate_upload_type(filename, file.content_type)

    max_size_bytes = get_max_dataset_size_bytes()
    checksum = hashlib.sha256()
    file_size = 0

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

        dataset.status = "Uploading"
        try:
            session.commit()
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Could not update dataset %s upload status (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Upload could not be started.",
            ) from None

        bucket = getenv("MINIO_RAW_BUCKET", "insightos-raw")
        storage_key = f"{dataset_id}/{uuid4().hex}/{filename}"
        object_upload_started = False
        try:
            storage = get_storage_client()
            if not storage.bucket_exists(bucket):
                storage.make_bucket(bucket)

            for _ in range(5):
                storage_key = f"{dataset_id}/{uuid4().hex}/{filename}"
                try:
                    storage.stat_object(bucket, storage_key)
                except S3Error as error:
                    if error.code in {"NoSuchKey", "NoSuchObject"}:
                        break
                    raise
            else:
                raise ValueError("Could not generate a unique storage key.")

            object_upload_started = True
            storage.put_object(
                bucket,
                storage_key,
                file.file,
                file_size,
                content_type=file.content_type or "application/octet-stream",
            )

            dataset_file = DatasetFile(
                dataset_id=dataset_id,
                filename=filename,
                storage_key=storage_key,
                file_size_bytes=file_size,
                checksum=checksum.hexdigest(),
                mime_type=file.content_type,
            )
            session.add(dataset_file)
            dataset.status = "Uploaded"
            session.commit()
        except (MinioException, HTTPError, OSError, ValueError) as error:
            session.rollback()
            cleanup_succeeded = True
            if object_upload_started:
                try:
                    storage.remove_object(bucket, storage_key)
                except (MinioException, HTTPError, OSError) as cleanup_error:
                    cleanup_succeeded = False
                    logger.error(
                        "Could not clean up failed upload for dataset %s at %s (%s)",
                        dataset_id,
                        storage_key,
                        type(cleanup_error).__name__,
                    )
            mark_dataset_failed(dataset_id, session)
            logger.error(
                "Object storage upload failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            if not cleanup_succeeded:
                raise HTTPException(
                    status_code=502,
                    detail="File upload failed and object cleanup could not be confirmed.",
                ) from None
            raise HTTPException(
                status_code=502,
                detail="File could not be stored.",
            ) from None
        except SQLAlchemyError as error:
            session.rollback()
            cleanup_succeeded = True
            try:
                storage.remove_object(bucket, storage_key)
            except (MinioException, HTTPError, OSError) as cleanup_error:
                cleanup_succeeded = False
                logger.error(
                    "Could not clean up upload for dataset %s at %s (%s)",
                    dataset_id,
                    storage_key,
                    type(cleanup_error).__name__,
                )
            mark_dataset_failed(dataset_id, session)
            logger.error(
                "File metadata save failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            if not cleanup_succeeded:
                raise HTTPException(
                    status_code=500,
                    detail="File metadata could not be saved and object cleanup could not be confirmed.",
                ) from None
            raise HTTPException(
                status_code=500,
                detail="File metadata could not be saved.",
            ) from None

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
            },
        }


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


@app.delete("/api/v1/datasets/{dataset_id}")
def delete_dataset(dataset_id: int):
    with SessionLocal() as session:
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

        bucket = getenv("MINIO_RAW_BUCKET", "insightos-raw")
        try:
            storage = get_storage_client()
            if storage.bucket_exists(bucket):
                delete_errors = list(
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
            else:
                delete_errors = []
        except (MinioException, HTTPError, OSError) as error:
            session.rollback()
            logger.error(
                "Object storage deletion failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=502,
                detail="Dataset files could not be removed from storage.",
            ) from None

        if delete_errors:
            session.rollback()
            logger.error(
                "Object storage reported deletion errors for dataset %s",
                dataset_id,
            )
            raise HTTPException(
                status_code=502,
                detail="Dataset files could not be removed from storage.",
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