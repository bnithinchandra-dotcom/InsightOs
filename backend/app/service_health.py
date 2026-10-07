import logging
from os import getenv

from minio import Minio
from redis import Redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine


logger = logging.getLogger(__name__)


def check_database() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return True
    except SQLAlchemyError as error:
        logger.warning("PostgreSQL health check failed (%s)", type(error).__name__)
        return False


def check_redis() -> bool:
    redis_url = getenv("REDIS_URL")
    if not redis_url:
        redis_url = (
            f"redis://{getenv('REDIS_HOST', 'redis')}:"
            f"{getenv('REDIS_PORT', '6379')}/0"
        )

    try:
        with Redis.from_url(
            redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        ) as client:
            return bool(client.ping())
    except Exception as error:
        logger.warning("Redis health check failed (%s)", type(error).__name__)
        return False


def check_storage() -> tuple[bool, dict[str, str]]:
    buckets = {
        getenv("MINIO_RAW_BUCKET", "insightos-raw"): "unavailable",
        getenv("MINIO_PROCESSED_BUCKET", "insightos-processed"): "unavailable",
        getenv("MINIO_EXPORTS_BUCKET", "insightos-exports"): "unavailable",
    }

    try:
        client = Minio(
            getenv("MINIO_ENDPOINT", "minio:9000"),
            access_key=getenv("MINIO_ACCESS_KEY"),
            secret_key=getenv("MINIO_SECRET_KEY"),
            secure=getenv("MINIO_SECURE", "false").lower() in {"1", "true", "yes"},
        )

        for bucket in buckets:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            buckets[bucket] = "available"

        return True, buckets
    except Exception as error:
        logger.warning("Object storage health check failed (%s)", type(error).__name__)
        return False, buckets
