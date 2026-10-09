import hashlib
import logging
import math
from os import getenv
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from minio import Minio
from minio.error import MinioException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
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
    clean_filename = (filename or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    if (
        not clean_filename
        or len(clean_filename) > 255
        or "\x00" in clean_filename
    ):
        raise HTTPException(status_code=400, detail="A valid filename is required.")
    return clean_filename


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
def upload_dataset_file(dataset_id: int, file: UploadFile = File(...)):
    filename = get_upload_filename(file.filename)
    if file.content_type is not None and len(file.content_type) > 255:
        raise HTTPException(status_code=400, detail="A valid MIME type is required.")

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
            if object_upload_started:
                try:
                    storage.remove_object(bucket, storage_key)
                except (MinioException, HTTPError, OSError) as cleanup_error:
                    logger.error(
                        "Could not clean up failed upload for dataset %s (%s)",
                        dataset_id,
                        type(cleanup_error).__name__,
                    )
            mark_dataset_failed(dataset_id, session)
            logger.error(
                "Object storage upload failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=502,
                detail="File could not be stored.",
            ) from None
        except SQLAlchemyError as error:
            session.rollback()
            try:
                storage.remove_object(bucket, storage_key)
            except (MinioException, HTTPError, OSError) as cleanup_error:
                logger.error(
                    "Could not clean up upload for dataset %s (%s)",
                    dataset_id,
                    type(cleanup_error).__name__,
                )
            mark_dataset_failed(dataset_id, session)
            logger.error(
                "File metadata save failed for dataset %s (%s)",
                dataset_id,
                type(error).__name__,
            )
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