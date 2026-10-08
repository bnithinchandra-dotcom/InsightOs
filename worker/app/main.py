import logging
import os
import signal
import sys
import time
from typing import Optional

import redis

logger = logging.getLogger("insightos.worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

shutdown_requested = False
redis_client: Optional[redis.Redis] = None


def get_redis_url() -> str:
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis_url

    host = os.getenv("REDIS_HOST", "redis")
    port = os.getenv("REDIS_PORT", "6379")
    return f"redis://{host}:{port}/0"


def handle_shutdown(signum: int, _frame) -> None:
    global shutdown_requested
    shutdown_requested = True
    logger.info("Worker shutdown requested (%s)", signal.Signals(signum).name)

    if redis_client is not None:
        try:
            redis_client.close()
        except Exception:  # pragma: no cover - defensive cleanup
            pass


def wait_for_redis(client: redis.Redis, attempts: int = 10, delay_seconds: int = 5) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            client.ping()
            logger.info("Redis connection successful")
            return True
        except redis.RedisError as exc:
            logger.warning(
                "Redis connection failed (attempt %s/%s): %s",
                attempt,
                attempts,
                exc,
            )
            if attempt == attempts:
                return False
            time.sleep(delay_seconds)

    return False


def main() -> int:
    global redis_client

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.info("Worker starting")
    logger.info("Connecting to Redis")

    redis_client = redis.Redis.from_url(
        get_redis_url(),
        socket_connect_timeout=3,
        socket_timeout=3,
        decode_responses=True,
    )

    if not wait_for_redis(redis_client):
        logger.error("Redis connectivity check failed. Worker exiting.")
        return 1

    logger.info("Worker ready")

    try:
        while not shutdown_requested:
            time.sleep(5)
    finally:
        logger.info("Worker shutting down")
        if redis_client is not None:
            try:
                redis_client.close()
            except Exception:  # pragma: no cover - defensive cleanup
                pass

    logger.info("Worker exited cleanly")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
        raise SystemExit(0)
    except Exception as exc:
        logger.exception("Worker failed unexpectedly: %s", exc)
        raise SystemExit(1)
