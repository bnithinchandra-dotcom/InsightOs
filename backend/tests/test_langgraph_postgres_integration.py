import os
import subprocess
import sys
from typing import TypedDict
from uuid import uuid4

import pytest


POSTGRES_DSN_ENV = "INSIGHTOS_TEST_POSTGRES_DSN"
_CHILD_PHASE = "--postgres-checkpoint-phase"


class _CheckpointState(TypedDict, total=False):
    prompt: str
    answer: str


def _checkpoint_graph(checkpointer):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    def wait_for_resume(state: _CheckpointState) -> _CheckpointState:
        answer = interrupt({"prompt": state["prompt"]})
        return {"answer": answer}

    builder = StateGraph(_CheckpointState)
    builder.add_node("wait_for_resume", wait_for_resume)
    builder.add_edge(START, "wait_for_resume")
    builder.add_edge("wait_for_resume", END)
    return builder.compile(checkpointer=checkpointer)


def _run_postgres_phase(phase: str, workflow_id: str) -> None:
    from pathlib import Path

    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    from app.agent.checkpointer import (
        close_postgres_checkpoint_saver,
        initialize_postgres_checkpoint_saver,
    )
    from langgraph.types import Command

    dsn = os.environ[POSTGRES_DSN_ENV]
    saver = initialize_postgres_checkpoint_saver(dsn, required=True)
    if saver is None:
        raise AssertionError("Required PostgreSQL saver initialization returned None.")
    try:
        assert initialize_postgres_checkpoint_saver(dsn, required=True) is saver
        graph = _checkpoint_graph(saver)
        config = {"configurable": {"thread_id": workflow_id}}

        if phase == "write":
            result = graph.invoke({"prompt": "integration checkpoint"}, config)
            assert result["__interrupt__"]
            checkpoint = saver.get_tuple(config)
            assert checkpoint is not None
            assert checkpoint.checkpoint["channel_values"]["prompt"] == (
                "integration checkpoint"
            )
        elif phase == "resume":
            checkpoint = saver.get_tuple(config)
            assert checkpoint is not None
            assert checkpoint.checkpoint["channel_values"]["prompt"] == (
                "integration checkpoint"
            )
            result = graph.invoke(Command(resume="resumed after process restart"), config)
            assert result["answer"] == "resumed after process restart"
        else:
            raise ValueError(f"Unknown integration phase: {phase}")
    finally:
        close_postgres_checkpoint_saver()


def _run_child_phase(phase: str, workflow_id: str) -> None:
    process = subprocess.run(
        [sys.executable, __file__, _CHILD_PHASE, phase, workflow_id],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        pytest.fail(
            f"Checkpoint subprocess phase {phase!r} failed with exit "
            f"{process.returncode}.\n{process.stderr[-4000:]}"
        )


def test_postgres_checkpoint_survives_process_restart_and_resumes():
    dsn = os.getenv(POSTGRES_DSN_ENV)
    if not dsn:
        pytest.skip(f"Set {POSTGRES_DSN_ENV} to run live PostgreSQL integration.")

    workflow_id = f"insightos-m1-checkpoint-{uuid4().hex}"
    _run_child_phase("write", workflow_id)
    _run_child_phase("resume", workflow_id)


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == _CHILD_PHASE:
    try:
        _run_postgres_phase(sys.argv[2], sys.argv[3])
    except Exception as exc:
        print(f"PostgreSQL checkpoint phase failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(0)
