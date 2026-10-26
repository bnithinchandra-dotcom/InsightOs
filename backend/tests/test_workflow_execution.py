import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import Base
from app.migrations.versions import e4a2d7c91b60_add_durable_workflows as workflow_migration
from app.models import User, WorkflowExecution, WorkflowTask
from app.workflow_execution import (
    WorkflowPlanError,
    persist_trace_plan,
    recover_persisted_workflow,
    run_persisted_workflow,
    validate_task_plan,
)


class WorkflowExecutionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session_factory() as session:
            user = User(email="workflow-test@example.invalid")
            session.add(user)
            session.flush()
            self.workflow = WorkflowExecution(
                id=uuid4().hex,
                owner_user_id=user.id,
                idempotency_key=uuid4().hex,
                objective="Run a bounded workflow",
                status="running",
                current_step=0,
            )
            session.add(self.workflow)
            session.commit()
            self.workflow_id = self.workflow.id

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _plan():
        return [
            {
                "task_id": "plan",
                "agent": "coordinator",
                "operation": "create_plan",
                "depends_on": [],
                "status": "pending",
                "dataset_id": None,
                "file_id": None,
                "input_references": [],
                "output_references": [],
            },
            {
                "task_id": "inspect",
                "agent": "data_intelligence",
                "operation": "inspect",
                "depends_on": ["plan"],
                "status": "pending",
                "dataset_id": None,
                "file_id": None,
                "input_references": [],
                "output_references": [],
            },
            {
                "task_id": "independent",
                "agent": "analytics_modelling",
                "operation": "independent_summary",
                "depends_on": [],
                "status": "pending",
                "dataset_id": None,
                "file_id": None,
                "input_references": [],
                "output_references": [],
            },
        ]

    def _persist_plan(self, plan=None):
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, self.workflow_id)
            persist_trace_plan(session, workflow, plan or self._plan())
            session.commit()

    def test_plan_validation_rejects_cycles_and_unknown_dependencies(self):
        cyclic = self._plan()
        cyclic[0]["depends_on"] = ["inspect"]
        with self.assertRaises(WorkflowPlanError):
            validate_task_plan(cyclic)
        missing = self._plan()
        missing[1]["depends_on"] = ["not-present"]
        with self.assertRaises(WorkflowPlanError):
            validate_task_plan(missing)

    def test_additive_workflow_migration_upgrades_and_downgrades(self):
        with self.engine.begin() as connection:
            for table in (
                "workflow_task_dependencies",
                "workflow_tasks",
                "workflow_executions",
            ):
                connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(workflow_migration, "op", operations):
                workflow_migration.upgrade()
                tables = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue(
                    {
                        "workflow_executions",
                        "workflow_tasks",
                        "workflow_task_dependencies",
                    }.issubset(tables)
                )
                workflow_migration.downgrade()
                tables = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertFalse(
                    {
                        "workflow_executions",
                        "workflow_tasks",
                        "workflow_task_dependencies",
                    }.intersection(tables)
                )

    def test_scheduler_runs_ready_tasks_and_preserves_independent_results(self):
        self._persist_plan()
        execution_order = []

        def plan(_task):
            execution_order.append("plan")
            return {"plan_version": 1}

        def inspect(_task):
            execution_order.append("inspect")
            raise RuntimeError("private error details must not persist")

        def independent(_task):
            execution_order.append("independent")
            return {"rows": 12}

        with self.session_factory() as session:
            workflow = run_persisted_workflow(
                session,
                self.workflow_id,
                {
                    "create_plan": plan,
                    "inspect": inspect,
                    "independent_summary": independent,
                },
            )
            self.assertEqual(workflow.status, "partial")
            tasks = {task.task_key: task for task in workflow.tasks}
            self.assertEqual(tasks["plan"].status, "completed")
            self.assertEqual(tasks["inspect"].status, "failed")
            self.assertEqual(tasks["independent"].status, "completed")
            self.assertEqual(tasks["inspect"].error_code, "task_execution_failed")
            self.assertNotIn("private", str(tasks["inspect"].result_summary))
            self.assertEqual(tasks["independent"].result_summary, {"rows": 12})
        self.assertEqual(execution_order[0], "plan")
        self.assertLess(execution_order.index("plan"), execution_order.index("inspect"))
        self.assertEqual(set(execution_order), {"plan", "inspect", "independent"})

    def test_scheduler_retries_only_idempotent_tasks_within_bound(self):
        self._persist_plan([self._plan()[0]])
        with self.session_factory() as session:
            task = session.query(WorkflowTask).one()
            task.idempotent = True
            task.max_retries = 2
            session.commit()

        attempts = []

        def flaky(_task):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("retry")
            return {"ok": True}

        with self.session_factory() as session:
            workflow = run_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": flaky},
            )
            task = workflow.tasks[0]
            self.assertEqual(workflow.status, "completed")
            self.assertEqual(task.retry_count, 2)
            self.assertEqual(task.result_summary, {"ok": True})
        self.assertEqual(len(attempts), 3)

    def test_scheduler_does_not_implicitly_replay_running_tasks(self):
        self._persist_plan(self._plan()[:2])
        with self.session_factory() as session:
            replayable, non_replayable = session.query(WorkflowTask).order_by(
                WorkflowTask.sequence
            ).all()
            replayable.status = "running"
            replayable.idempotent = True
            replayable.max_retries = 1
            non_replayable.status = "running"
            non_replayable.idempotent = False
            session.commit()

        executed = []
        with self.session_factory() as session:
            workflow = run_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": lambda _task: executed.append("replayed") or {"ok": True}},
                max_steps=2,
            )
            tasks = {task.task_key: task for task in workflow.tasks}
            self.assertEqual(tasks["plan"].status, "running")
            self.assertEqual(tasks["inspect"].status, "running")
            self.assertEqual(workflow.status, "running")
        self.assertEqual(executed, [])

    def test_recovery_replays_stale_idempotent_task_with_current_inputs(self):
        self._persist_plan([self._plan()[0]])
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            task = session.query(WorkflowTask).one()
            task.status = "running"
            task.idempotent = True
            task.max_retries = 1
            task.updated_at = stale
            session.commit()

        executed = []
        with self.session_factory() as session:
            workflow, report = recover_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": lambda _task: executed.append("read") or {"ok": True}},
                lambda _session, _workflow, _task: True,
                stale_before=stale + timedelta(minutes=1),
            )
            task = workflow.tasks[0]
            self.assertEqual(report["recovered"], ["plan"])
            self.assertEqual(task.status, "completed")
            self.assertEqual(task.retry_count, 1)
            self.assertEqual(workflow.status, "completed")
        self.assertEqual(executed, ["read"])

    def test_recovery_reuses_valid_completed_prerequisite_and_orders_dependents(self):
        self._persist_plan(self._plan()[:2])
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            plan, inspect = session.query(WorkflowTask).order_by(
                WorkflowTask.sequence
            ).all()
            plan.status = "completed"
            plan.result_summary = {"cached": "valid"}
            plan.output_references = [{"type": "profile", "id": 9}]
            plan.idempotent = True
            inspect.status = "running"
            inspect.idempotent = True
            inspect.max_retries = 1
            inspect.updated_at = stale
            session.commit()

        execution_order = []

        def inspect_handler(_task):
            execution_order.append("inspect")
            with self.session_factory() as observer:
                prerequisite = observer.query(WorkflowTask).filter_by(
                    task_key="plan"
                ).one()
                self.assertEqual(prerequisite.result_summary, {"cached": "valid"})
            return {"inspected": True}

        with self.session_factory() as session:
            workflow, report = recover_persisted_workflow(
                session,
                self.workflow_id,
                {"inspect": inspect_handler},
                lambda _session, _workflow, _task: True,
                stale_before=stale + timedelta(minutes=1),
            )
            tasks = {task.task_key: task for task in workflow.tasks}
            self.assertEqual(report["reused"], ["plan"])
            self.assertEqual(tasks["plan"].result_summary, {"cached": "valid"})
            self.assertEqual(tasks["inspect"].status, "completed")
        self.assertEqual(execution_order, ["inspect"])

    def test_recovery_refuses_to_replay_ambiguous_non_idempotent_work(self):
        self._persist_plan([self._plan()[0]])
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            task = session.query(WorkflowTask).one()
            task.status = "running"
            task.idempotent = False
            task.updated_at = stale
            session.commit()

        executed = []
        with self.session_factory() as session:
            workflow, report = recover_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": lambda _task: executed.append("unsafe") or {"ok": True}},
                lambda _session, _workflow, _task: True,
                stale_before=stale + timedelta(minutes=1),
            )
            task = workflow.tasks[0]
            self.assertEqual(task.status, "blocked")
            self.assertEqual(
                task.error_code,
                "interrupted_requires_reconciliation",
            )
            self.assertEqual(report["not_replayed"], ["plan"])
        self.assertEqual(executed, [])

    def test_recovery_invalidates_completed_output_when_source_changes(self):
        self._persist_plan([self._plan()[0]])
        with self.session_factory() as session:
            task = session.query(WorkflowTask).one()
            task.status = "completed"
            task.idempotent = True
            task.result_summary = {"rows": 3}
            task.output_references = [{"type": "profile"}]
            session.commit()

        with self.session_factory() as session:
            workflow, report = recover_persisted_workflow(
                session,
                self.workflow_id,
                {},
                lambda _session, _workflow, _task: False,
                stale_before=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            task = workflow.tasks[0]
            self.assertEqual(report["invalidated"], ["plan"])
            self.assertEqual(task.status, "blocked")
            self.assertEqual(task.result_summary, None)
            self.assertEqual(task.output_references, [])
            self.assertEqual(task.error_code, "recovery_source_changed")

    def test_recovery_executes_pending_tasks_in_dependency_order(self):
        self._persist_plan()
        execution_order = []
        with self.session_factory() as session:
            for task in session.query(WorkflowTask).all():
                task.idempotent = True
            session.commit()
        handlers = {
            "create_plan": lambda _task: execution_order.append("plan") or {"ok": 1},
            "inspect": lambda _task: execution_order.append("inspect") or {"ok": 2},
            "independent_summary": lambda _task: execution_order.append("independent") or {"ok": 3},
        }
        with self.session_factory() as session:
            workflow, _report = recover_persisted_workflow(
                session,
                self.workflow_id,
                handlers,
                lambda _session, _workflow, _task: True,
                stale_before=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            self.assertEqual(workflow.status, "completed")
        self.assertLess(execution_order.index("plan"), execution_order.index("inspect"))

    def test_repeated_recovery_does_not_repeat_completed_handler(self):
        self._persist_plan([self._plan()[0]])
        executed = []
        handler = {"create_plan": lambda _task: executed.append("once") or {"ok": True}}
        stale_before = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.session_factory() as session:
            task = session.query(WorkflowTask).one()
            task.idempotent = True
            session.commit()
        with self.session_factory() as session:
            recover_persisted_workflow(
                session,
                self.workflow_id,
                handler,
                lambda _session, _workflow, _task: True,
                stale_before=stale_before,
            )
        with self.session_factory() as session:
            workflow, report = recover_persisted_workflow(
                session,
                self.workflow_id,
                handler,
                lambda _session, _workflow, _task: True,
                stale_before=stale_before,
            )
            self.assertEqual(workflow.status, "completed")
            self.assertEqual(report["reused"], ["plan"])
        self.assertEqual(executed, ["once"])

    def test_recovery_failure_preserves_valid_completed_output(self):
        self._persist_plan(self._plan()[:2])
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            prerequisite, interrupted = session.query(WorkflowTask).order_by(
                WorkflowTask.sequence
            ).all()
            prerequisite.status = "completed"
            prerequisite.idempotent = True
            prerequisite.result_summary = {"preserved": True}
            interrupted.status = "running"
            interrupted.idempotent = True
            interrupted.max_retries = 1
            interrupted.updated_at = stale
            session.commit()

        with self.session_factory() as session:
            workflow, _report = recover_persisted_workflow(
                session,
                self.workflow_id,
                {"inspect": lambda _task: (_ for _ in ()).throw(RuntimeError("private"))},
                lambda _session, _workflow, _task: True,
                stale_before=stale + timedelta(minutes=1),
            )
            tasks = {task.task_key: task for task in workflow.tasks}
            self.assertEqual(workflow.status, "partial")
            self.assertEqual(tasks["plan"].result_summary, {"preserved": True})
            self.assertEqual(tasks["inspect"].status, "failed")

    def test_concurrent_scheduler_call_does_not_claim_running_task_twice(self):
        self._persist_plan([self._plan()[0]])
        executions = []

        def handler(_task):
            with self.session_factory() as concurrent_session:
                concurrent_workflow = run_persisted_workflow(
                    concurrent_session,
                    self.workflow_id,
                    {"create_plan": lambda _other: executions.append("duplicate") or {"ok": True}},
                )
                self.assertEqual(concurrent_workflow.status, "running")
            executions.append("original")
            return {"ok": True}

        with self.session_factory() as session:
            workflow = run_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": handler},
            )
            self.assertEqual(workflow.status, "completed")
        self.assertEqual(executions, ["original"])

    def test_concurrent_recovery_does_not_execute_the_same_task_twice(self):
        self._persist_plan([self._plan()[0]])
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, self.workflow_id)
            task = session.query(WorkflowTask).one()
            workflow.updated_at = stale
            task.status = "running"
            task.idempotent = True
            task.max_retries = 1
            task.updated_at = stale
            session.commit()

        executions = []

        def handler(_task):
            with self.session_factory() as concurrent_session:
                concurrent_workflow, _report = recover_persisted_workflow(
                    concurrent_session,
                    self.workflow_id,
                    {"create_plan": lambda _other: executions.append("duplicate") or {"ok": True}},
                    lambda _session, _workflow, _task: True,
                    stale_before=stale + timedelta(minutes=1),
                )
                self.assertEqual(concurrent_workflow.status, "running")
            executions.append("original")
            return {"ok": True}

        with self.session_factory() as session:
            workflow, _report = recover_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": handler},
                lambda _session, _workflow, _task: True,
                stale_before=stale + timedelta(minutes=1),
            )
            self.assertEqual(workflow.status, "completed")
        self.assertEqual(executions, ["original"])

    def test_ambiguous_handler_outcome_is_not_marked_successful(self):
        self._persist_plan([self._plan()[0]])
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            task = session.query(WorkflowTask).one()
            task.status = "running"
            task.idempotent = False
            task.updated_at = stale
            session.commit()

        with self.session_factory() as session:
            workflow, _report = recover_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": lambda _task: {"must_not": "run"}},
                lambda _session, _workflow, _task: True,
                stale_before=stale + timedelta(minutes=1),
            )
            self.assertEqual(workflow.status, "blocked")
            self.assertEqual(workflow.tasks[0].status, "blocked")
            self.assertIsNone(workflow.tasks[0].result_summary)

    def test_completed_tasks_are_not_repeated(self):
        self._persist_plan([self._plan()[0]])
        with self.session_factory() as session:
            task = session.query(WorkflowTask).one()
            task.status = "completed"
            task.result_summary = {"reused": True}
            session.commit()

        with self.session_factory() as session:
            workflow = run_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": lambda _task: self.fail("completed task reran")},
            )
            self.assertEqual(workflow.tasks[0].result_summary, {"reused": True})
            self.assertEqual(workflow.status, "completed")

    def test_missing_handler_is_persisted_as_unsupported(self):
        self._persist_plan([self._plan()[0]])
        with self.session_factory() as session:
            workflow = run_persisted_workflow(
                session,
                self.workflow_id,
                {},
            )
            self.assertEqual(workflow.status, "failed")
            self.assertEqual(workflow.tasks[0].status, "unsupported")
            self.assertEqual(
                workflow.tasks[0].error_code,
                "unsupported_task_type",
            )

    def test_running_state_is_committed_before_handler_execution(self):
        self._persist_plan([self._plan()[0]])

        def handler(task):
            with self.session_factory() as observer:
                persisted = observer.get(WorkflowTask, task.id)
                self.assertEqual(persisted.status, "running")
            return {"executed": True}

        with self.session_factory() as session:
            workflow = run_persisted_workflow(
                session,
                self.workflow_id,
                {"create_plan": handler},
            )
            self.assertEqual(workflow.tasks[0].status, "completed")
