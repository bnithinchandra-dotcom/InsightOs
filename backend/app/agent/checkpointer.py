from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Any

from app.database import get_database_url

logger = logging.getLogger(__name__)

try:
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from langgraph.checkpoint.postgres import PostgresSaver
except ImportError as exc:  # pragma: no cover - dependency guard
    _CHECKPOINTER_IMPORT_ERROR: ImportError | None = exc
    ConnectionPool = None  # type: ignore[assignment,misc]
    PostgresSaver = None  # type: ignore[assignment,misc]
    dict_row = None
else:
    _CHECKPOINTER_IMPORT_ERROR = None


class CheckpointerInitializationError(RuntimeError):
    """Raised when required PostgreSQL checkpointing cannot be initialized."""


_POSTGRES_CHECKPOINTER: Any | None = None
_POSTGRES_POOL: Any | None = None
_CHECKPOINTER_LOCK = Lock()


def _normalize_database_url(conn_string: str | None) -> str | None:
    if not conn_string:
        return None
    if conn_string.startswith("postgresql+psycopg://"):
        return conn_string.replace("postgresql+psycopg://", "postgresql://", 1)
    return conn_string


def _is_checkpointing_required() -> bool:
    configured = os.getenv("INSIGHTOS_LANGGRAPH_CHECKPOINT_REQUIRED")
    if configured is not None:
        normalized = configured.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise CheckpointerInitializationError(
            "INSIGHTOS_LANGGRAPH_CHECKPOINT_REQUIRED must be a boolean value."
        )

    environment = (
        os.getenv("INSIGHTOS_ENV") or os.getenv("ENVIRONMENT") or "development"
    ).strip().lower()
    return environment in {"production", "prod"}


def initialize_postgres_checkpoint_saver(
    conn_string: str | None = None,
    *,
    required: bool | None = None,
) -> Any | None:
    """Create and retain a pooled PostgreSQL checkpointer for this process.

    PostgresSaver.setup() runs once per process during startup. The connection
    pool remains open until close_postgres_checkpoint_saver() is called.
    """
    global _POSTGRES_CHECKPOINTER, _POSTGRES_POOL

    is_required = _is_checkpointing_required() if required is None else required
    with _CHECKPOINTER_LOCK:
        if _POSTGRES_CHECKPOINTER is not None:
            return _POSTGRES_CHECKPOINTER

        if _CHECKPOINTER_IMPORT_ERROR is not None:
            _raise_or_log_unavailable(
                is_required,
                "LangGraph PostgreSQL checkpoint dependencies are unavailable "
                f"({_CHECKPOINTER_IMPORT_ERROR.name}).",
                _CHECKPOINTER_IMPORT_ERROR,
            )
            return None

        resolved = _normalize_database_url(conn_string or get_database_url())
        if resolved is None:
            _raise_or_log_unavailable(
                is_required,
                "No PostgreSQL connection string is configured for LangGraph checkpointing.",
            )
            return None

        pool = None
        try:
            pool = ConnectionPool(
                conninfo=resolved,
                min_size=1,
                max_size=10,
                timeout=10,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
                open=False,
            )
            pool.open(wait=True)
            saver = PostgresSaver(pool)
            saver.setup()
        except Exception as exc:
            if pool is not None:
                try:
                    pool.close()
                except Exception as close_error:
                    logger.error(
                        "Could not close the failed checkpoint pool (%s).",
                        type(close_error).__name__,
                    )
            _raise_or_log_unavailable(
                is_required,
                "PostgreSQL checkpoint saver initialization failed "
                f"({type(exc).__name__}).",
                exc,
            )
            return None

        _POSTGRES_POOL = pool
        _POSTGRES_CHECKPOINTER = saver
        logger.info("Initialized the PostgreSQL LangGraph checkpoint saver.")
        return saver


def _raise_or_log_unavailable(
    required: bool,
    message: str,
    cause: BaseException | None = None,
) -> None:
    if required:
        error = CheckpointerInitializationError(message)
        if cause is not None:
            raise error from cause
        raise error
    logger.warning("%s Checkpointing is disabled because it is not required.", message)


def get_postgres_checkpoint_saver(
    conn_string: str | None = None,
    *,
    required: bool | None = None,
) -> Any | None:
    return initialize_postgres_checkpoint_saver(conn_string, required=required)


def close_postgres_checkpoint_saver() -> None:
    global _POSTGRES_CHECKPOINTER, _POSTGRES_POOL

    with _CHECKPOINTER_LOCK:
        pool = _POSTGRES_POOL
        if pool is not None:
            pool.close()
        _POSTGRES_POOL = None
        _POSTGRES_CHECKPOINTER = None
