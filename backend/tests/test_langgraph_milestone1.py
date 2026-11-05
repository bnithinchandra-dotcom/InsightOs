import asyncio

import pytest

from app.agent import checkpointer
from app.agent.checkpointer import CheckpointerInitializationError
from app.agent.langgraph_orchestrator import (
    build_request_orchestration_graph,
    build_specialist_graph,
    workflow_checkpoint_config,
)


@pytest.fixture(autouse=True)
def reset_checkpointer():
    checkpointer._POSTGRES_CHECKPOINTER = None
    checkpointer._POSTGRES_POOL = None
    yield
    checkpointer.close_postgres_checkpoint_saver()


class FakePool:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.opened = False
        self.closed = False
        self.instances.append(self)

    def open(self, *, wait):
        assert wait is True
        self.opened = True

    def close(self):
        self.closed = True


class FakeSaver:
    setup_calls = 0
    fail_setup = False

    def __init__(self, pool):
        self.pool = pool

    def setup(self):
        type(self).setup_calls += 1
        if type(self).fail_setup:
            raise RuntimeError("database unavailable")


@pytest.fixture
def fake_checkpoint_dependencies(monkeypatch):
    FakePool.instances.clear()
    FakeSaver.setup_calls = 0
    FakeSaver.fail_setup = False
    monkeypatch.setattr(checkpointer, "_CHECKPOINTER_IMPORT_ERROR", None)
    monkeypatch.setattr(checkpointer, "ConnectionPool", FakePool)
    monkeypatch.setattr(checkpointer, "PostgresSaver", FakeSaver)
    monkeypatch.setattr(checkpointer, "dict_row", object())
    monkeypatch.setattr(checkpointer, "get_database_url", lambda: "postgresql+psycopg://user:pass@db/app")


def test_checkpointer_uses_reusable_pool_and_runs_setup_once(fake_checkpoint_dependencies):
    saver = checkpointer.initialize_postgres_checkpoint_saver(required=True)
    same_saver = checkpointer.get_postgres_checkpoint_saver(required=True)

    assert saver is same_saver
    assert saver.pool.opened
    assert saver.pool.kwargs["conninfo"] == "postgresql://user:pass@db/app"
    assert saver.pool.kwargs["kwargs"]["autocommit"] is True
    assert FakeSaver.setup_calls == 1

    checkpointer.close_postgres_checkpoint_saver()
    assert saver.pool.closed


def test_required_setup_failure_closes_pool_and_raises(fake_checkpoint_dependencies):
    FakeSaver.fail_setup = True

    with pytest.raises(CheckpointerInitializationError, match="initialization failed"):
        checkpointer.initialize_postgres_checkpoint_saver(required=True)

    assert FakePool.instances[0].closed
    assert checkpointer._POSTGRES_CHECKPOINTER is None


def test_production_setup_failure_is_not_silently_disabled(
    fake_checkpoint_dependencies,
    monkeypatch,
):
    FakeSaver.fail_setup = True
    monkeypatch.delenv("INSIGHTOS_LANGGRAPH_CHECKPOINT_REQUIRED", raising=False)
    monkeypatch.setenv("INSIGHTOS_ENV", "production")

    with pytest.raises(CheckpointerInitializationError, match="initialization failed"):
        checkpointer.initialize_postgres_checkpoint_saver()

    assert FakePool.instances[0].closed


def test_nonproduction_setup_failure_is_explicitly_disabled(
    fake_checkpoint_dependencies,
    caplog,
):
    FakeSaver.fail_setup = True

    assert checkpointer.initialize_postgres_checkpoint_saver(required=False) is None

    assert FakePool.instances[0].closed
    assert "Checkpointing is disabled because it is not required." in caplog.text


def test_production_requires_checkpointing_by_default(monkeypatch):
    monkeypatch.delenv("INSIGHTOS_LANGGRAPH_CHECKPOINT_REQUIRED", raising=False)
    monkeypatch.setenv("INSIGHTOS_ENV", "production")

    with pytest.raises(CheckpointerInitializationError, match="required"):
        build_specialist_graph()


def test_specialist_graph_never_fabricates_completed_results():
    result = build_specialist_graph().invoke({"user_request": "analyze this dataset"})

    assert result["execution_status"] == "blocked"
    assert result["error_code"] == "langgraph_specialist_pipeline_not_integrated"
    assert "analytics_decision" not in result
    assert "validation_result" not in result
    assert "presentation_result" not in result


def test_request_graph_compiles_and_returns_safe_non_success_result():
    graph = build_request_orchestration_graph()
    result = graph.invoke(
        {"workflow_id": "workflow-1", "user_request": "analyze data", "operation_type": "analytics"}
    )

    assert result["execution_status"] == "blocked"
    assert result["error_code"] == "langgraph_specialist_pipeline_not_integrated"
    assert result["final_response"]["status"] == "blocked"


def test_request_graph_rejects_empty_requests():
    result = build_request_orchestration_graph().invoke({"user_request": "  "})

    assert result["execution_status"] == "blocked"
    assert result["error_code"] == "missing_user_request"


def test_workflow_id_is_used_as_checkpoint_thread_id():
    assert workflow_checkpoint_config("workflow-123") == {
        "configurable": {"thread_id": "workflow-123"}
    }

    with pytest.raises(ValueError, match="workflow_id"):
        workflow_checkpoint_config("")


def test_fastapi_lifespan_uses_and_closes_the_single_saver(monkeypatch):
    from app import main

    saver = object()
    calls = []
    monkeypatch.setattr(
        main,
        "initialize_postgres_checkpoint_saver",
        lambda: calls.append("initialize") or saver,
    )
    monkeypatch.setattr(
        main,
        "close_postgres_checkpoint_saver",
        lambda: calls.append("close"),
    )

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            assert main.app.state.langgraph_checkpointer is saver

    asyncio.run(exercise_lifespan())
    assert calls == ["initialize", "close"]


def test_fastapi_startup_propagates_required_checkpoint_failure(monkeypatch):
    from app import main

    def fail_initialization():
        raise CheckpointerInitializationError("required checkpointing failed")

    monkeypatch.setattr(main, "initialize_postgres_checkpoint_saver", fail_initialization)

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            pytest.fail("Lifespan should not start without required checkpointing.")

    with pytest.raises(CheckpointerInitializationError, match="required checkpointing failed"):
        asyncio.run(exercise_lifespan())
