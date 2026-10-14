from os import getenv

from minio import Minio


def initialize_storage_buckets() -> None:
    client = Minio(
        getenv("MINIO_ENDPOINT", "minio:9000"),
        access_key=getenv("MINIO_ACCESS_KEY"),
        secret_key=getenv("MINIO_SECRET_KEY"),
        secure=getenv("MINIO_SECURE", "false").lower() in {"1", "true", "yes"},
    )
    buckets = (
        getenv("MINIO_RAW_BUCKET", "insightos-raw"),
        getenv("MINIO_PROCESSED_BUCKET", "insightos-processed"),
        getenv("MINIO_EXPORTS_BUCKET", "insightos-exports"),
    )
    for bucket in buckets:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)


if __name__ == "__main__":
    initialize_storage_buckets()
