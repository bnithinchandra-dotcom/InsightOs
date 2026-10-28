import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pyarrow as pa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.agent import api as chat_api
from app.agent.contracts import ToolResult
from app.database import Base
from app.models import (
    Dashboard,
    Dataset,
    DatasetFile,
    EDAAnalysis,
    Project,
    User,
    WorkflowExecution,
    WorkflowTask,
)
from app.multi_dataset_analytics import AnalyticsSource
from app.provenance import build_provenance, canonical_sha256


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session_factory() as session:
            self.owner = User(email="dashboard-owner@example.invalid")
            self.other_owner = User(email="dashboard-other@example.invalid")
            session.add_all([self.owner, self.other_owner])
            session.flush()
            project = Project(name="Dashboard project", user_id=self.owner.id)
            session.add(project)
            session.flush()
            self.left_dataset = Dataset(name="Customers", project_id=project.id, status="Ready")
            self.right_dataset = Dataset(name="Sales", project_id=project.id, status="Ready")
            session.add_all([self.left_dataset, self.right_dataset])
            session.commit()
            self.owner_id = self.owner.id
            self.other_owner_id = self.other_owner.id
            self.left_dataset_id = self.left_dataset.id
            self.right_dataset_id = self.right_dataset.id
            eda_file = DatasetFile(
                dataset_id=self.left_dataset_id,
                filename="customers.parquet",
                storage_key="raw/customers",
                file_size_bytes=100,
                checksum="e" * 64,
                status="Ready",
                normalization_status="Ready",
                normalization_result={
                    "tables": [{"table_index": 0, "sha256": "c" * 64}],
                },
            )
            session.add(eda_file)
            session.flush()
            source = {
                "dataset_id": self.left_dataset_id,
                "file_id": eda_file.id,
                "table_index": 0,
                "table_name": "customers",
                "artifact_sha256": "c" * 64,
                "normalization_version": "1",
            }
            self.eda_record = EDAAnalysis(
                dataset_id=self.left_dataset_id,
                file_id=eda_file.id,
                table_index=0,
                analysis_type="univariate",
                analysis_key="f" * 64,
                status="Completed",
                configuration={"selected_columns": ["amount"]},
                configuration_sha256="d" * 64,
                result={
                    "schema_version": 1,
                    "analysis_type": "univariate",
                    "status": "completed",
                    "source": source,
                    "algorithm_version": "1",
                    "configuration": {"selected_columns": ["amount"]},
                    "configuration_sha256": "d" * 64,
                    "executed_at": "2026-10-26T00:00:00+00:00",
                    "requested_columns": ["amount"],
                    "analyzed_columns": ["amount"],
                    "rows_analyzed": 4,
                    "exact": True,
                    "sampling": None,
                    "columns": [
                        {
                            "name": "amount",
                            "data_type": "double",
                            "status": "completed",
                            "total_rows": 4,
                            "non_null_count": 3,
                            "null_count": 1,
                            "null_percentage": 25.0,
                            "statistics": {
                                "count": 3,
                                "finite_count": 3,
                                "mean": 4.0,
                                "median": 4.0,
                                "minimum": 1.0,
                                "maximum": 7.0,
                                "range": 6.0,
                                "histogram": {
                                    "edges": [1.0, 4.0, 7.0],
                                    "counts": [2, 1],
                                },
                            },
                            "warnings": [],
                        }
                    ],
                    "warnings": [],
                    "pair_results": [],
                    "multivariate_summary": {},
                },
                provenance=build_provenance(
                    operation_type="eda_analysis",
                    operation_version="1",
                    configuration_sha256="d" * 64,
                    status="completed",
                    inputs=[
                        {
                            "kind": "normalized_parquet",
                            "dataset_id": self.left_dataset_id,
                            "file_id": eda_file.id,
                            "table_index": 0,
                            "sha256": "c" * 64,
                        }
                    ],
                    outputs=[{"sha256": "e" * 64}],
                    verification_status="verified",
                    checks=[{"name": "stored_analysis", "status": "verified"}],
                ),
            )
            session.add(self.eda_record)
            session.commit()
            self.eda_file_id = eda_file.id
            self.eda_analysis_id = self.eda_record.id

        self.sources = [
            AnalyticsSource(
                alias="customers",
                dataset_id=self.left_dataset_id,
                file_id=11,
                table_index=0,
                table=pa.table({
                    "customer_id": [1, 2],
                    "region": ["west", "east"],
                }),
                checksum="a" * 64,
                artifact_sha256="c" * 64,
            ),
            AnalyticsSource(
                alias="sales",
                dataset_id=self.right_dataset_id,
                file_id=22,
                table_index=0,
                table=pa.table({
                    "customer_id": [1, 1, 2],
                    "amount": [2, 3, 7],
                }),
                checksum="b" * 64,
                artifact_sha256="d" * 64,
            ),
        ]
        self.session_patch = patch.object(main, "SessionLocal", self.session_factory)
        self.owner_patch = patch.object(
            main,
            "_require_workspace_user",
            return_value=self.owner,
        )
        self.source_patch = patch.object(
            main,
            "_load_multi_dataset_sources",
            return_value=self.sources,
        )
        self.session_patch.start()
        self.owner_patch.start()
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.addCleanup(self.owner_patch.stop)
        self.addCleanup(self.session_patch.stop)
        self.chat_session_patch = patch.object(
            chat_api,
            "SessionLocal",
            self.session_factory,
        )
        self.chat_owner_patch = patch.object(
            chat_api,
            "_require_workspace_owner",
            return_value=self.owner,
        )
        self.chat_session_patch.start()
        self.chat_owner_patch.start()
        self.addCleanup(self.chat_owner_patch.stop)
        self.addCleanup(self.chat_session_patch.stop)
        self.client = TestClient(main.app)
        self.definition = {
            "title": "Revenue by region",
            "description": "Explicitly joined customer and sales data.",
            "analysis_request": {
                "sources": [
                    {
                        "alias": "customers",
                        "dataset_id": self.left_dataset_id,
                        "file_id": 11,
                    },
                    {
                        "alias": "sales",
                        "dataset_id": self.right_dataset_id,
                        "file_id": 22,
                    },
                ],
                "joins": [
                    {
                        "left_key": {"alias": "customers", "column": "customer_id"},
                        "right_key": {"alias": "sales", "column": "customer_id"},
                        "join_type": "inner",
                    }
                ],
                "dimensions": [{"alias": "customers", "column": "region"}],
                "metrics": [
                    {
                        "name": "revenue",
                        "operation": "sum",
                        "column": {"alias": "sales", "column": "amount"},
                    }
                ],
            },
            "chart_requests": [
                {
                    "title": "Revenue by region",
                    "chart_type": "bar",
                    "x_field": "customers.region",
                    "y_field": "revenue",
                }
            ],
        }

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_report_contains_measured_values_and_validated_charts(self):
        response = self.client.post(
            "/api/v1/analytics/reports",
            json={**self.definition["analysis_request"], "charts": self.definition["chart_requests"]},
        )

        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["summary"]["joined_row_count"], 3)
        self.assertEqual(report["charts"][0]["chart_type"], "bar")
        self.assertEqual(
            [finding["supporting_metric"]["value"] for finding in report["findings"]],
            [7, 5],
        )
        self.assertEqual(report["provenance"]["verification"]["status"], "COMPUTED")

    def test_dashboard_crud_persists_definition_and_refreshes_only_on_demand(self):
        created = self.client.post("/api/v1/dashboards", json=self.definition)
        self.assertEqual(created.status_code, 201, created.text)
        dashboard = created.json()
        self.assertEqual(dashboard["refresh_policy"], "on_demand")
        self.assertNotIn("results", dashboard["definition"])
        self.assertEqual(len(self.client.get("/api/v1/dashboards").json()), 1)

        updated_definition = {**self.definition, "title": "Updated revenue"}
        updated = self.client.put(
            f"/api/v1/dashboards/{dashboard['id']}",
            json=updated_definition,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["title"], "Updated revenue")
        retrieved = self.client.get(f"/api/v1/dashboards/{dashboard['id']}")
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(retrieved.json()["definition"]["analysis_request"]["metrics"][0]["name"], "revenue")

        deleted = self.client.delete(f"/api/v1/dashboards/{dashboard['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted": True})
        self.assertEqual(self.client.get("/api/v1/dashboards").json(), [])

    def test_dashboard_retrieval_is_owner_scoped(self):
        created = self.client.post("/api/v1/dashboards", json=self.definition)
        dashboard_id = created.json()["id"]
        with patch.object(main, "_require_workspace_user", return_value=self.other_owner):
            response = self.client.get(f"/api/v1/dashboards/{dashboard_id}")
        self.assertEqual(response.status_code, 404)

    def _create_completed_analytics_workflow(self):
        request_payload = {
            **self.definition["analysis_request"],
            "charts": self.definition["chart_requests"],
        }
        report_response = self.client.post(
            "/api/v1/analytics/reports",
            json=request_payload,
        )
        self.assertEqual(report_response.status_code, 200, report_response.text)
        report = report_response.json()
        workflow_id = "9" * 32
        task_key = "tool:generate_evidence_report:verified-report"
        context = {"project_id": None, "dataset_id": None}
        with self.session_factory() as session:
            workflow = WorkflowExecution(
                id=workflow_id,
                owner_user_id=self.owner_id,
                idempotency_key="a" * 64,
                objective="Create a verified analytics report",
                status="completed",
                current_step=1,
                source_scope=context,
            )
            session.add(workflow)
            session.flush()
            source_identity = chat_api._tool_source_state_identity(
                session,
                request_payload,
                self.left_dataset_id,
                self.owner_id,
            )
            source_fingerprint = canonical_sha256(source_identity)
            identity = canonical_sha256({
                "tool": "generate_evidence_report",
                "arguments": request_payload,
                **context,
                "source_identity": source_identity,
            })
            task = WorkflowTask(
                id="8" * 32,
                workflow_id=workflow_id,
                task_key=task_key,
                sequence=1,
                agent="assistant",
                task_type="generate_evidence_report",
                status="completed",
                idempotency_key="b" * 64,
                idempotent=True,
                max_retries=0,
                timeout_seconds=60,
                dataset_id=self.left_dataset_id,
                source_fingerprint=source_fingerprint,
                input_references=[
                    {"type": "input_identity", "sha256": identity},
                    {
                        "type": "tool_arguments",
                        "tool": "generate_evidence_report",
                        "arguments": request_payload,
                    },
                    {"type": "input_context", "context": context},
                ],
                output_references=[],
                result_summary={
                    "status": "succeeded",
                    "summary": "Generated a verified report.",
                    "data": report,
                    "warnings": report["warnings"],
                    "references": [],
                    "limitations": [],
                    "error_code": None,
                },
            )
            session.add(task)
            session.commit()
        return workflow_id, task_key, report

    def test_completed_workflow_report_saves_and_reloads_from_dashboard(self):
        dashboard_response = self.client.post(
            "/api/v1/dashboards",
            json={
                "title": "Pinned workflow report",
                "description": None,
                "analysis_request": None,
                "chart_requests": [],
            },
        )
        self.assertEqual(dashboard_response.status_code, 201, dashboard_response.text)
        workflow_id, task_key, report = self._create_completed_analytics_workflow()

        saved = self.client.post(
            f"/api/v1/dashboards/{dashboard_response.json()['id']}/analytics-results",
            json={"workflow_id": workflow_id, "task_id": task_key},
        )

        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse(saved.json()["duplicate"])
        component = saved.json()["analytics_component"]
        self.assertEqual(component["result_sha256"], canonical_sha256(report))
        self.assertEqual(len(saved.json()["dashboard"]["definition"]["analytics_components"]), 1)

        reloaded = self.client.get(
            f"/api/v1/chat/workflows/{workflow_id}/tasks/{task_key}/analytics-result"
        )
        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        self.assertEqual(reloaded.json()["result"], report)
        self.assertEqual(reloaded.json()["task_status"], "completed")

        duplicate = self.client.post(
            f"/api/v1/dashboards/{dashboard_response.json()['id']}/analytics-results",
            json={"workflow_id": workflow_id, "task_id": task_key},
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(
            len(duplicate.json()["dashboard"]["definition"]["analytics_components"]),
            1,
        )

        update = self.client.put(
            f"/api/v1/dashboards/{dashboard_response.json()['id']}",
            json={
                "title": "Renamed pinned report",
                "description": None,
                "analysis_request": None,
                "chart_requests": [],
            },
        )
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(
            len(update.json()["definition"]["analytics_components"]),
            1,
        )

    def test_dashboard_save_rejects_pending_and_foreign_workflow_reports(self):
        dashboard_response = self.client.post(
            "/api/v1/dashboards",
            json={
                "title": "Workflow report",
                "description": None,
                "analysis_request": None,
                "chart_requests": [],
            },
        )
        with patch.object(
            main,
            "_require_workspace_user",
            return_value=self.other_owner,
        ):
            foreign_dashboard = self.client.post(
                f"/api/v1/dashboards/{dashboard_response.json()['id']}/analytics-results",
                json={"workflow_id": "0" * 32, "task_id": "missing"},
            )
        self.assertEqual(foreign_dashboard.status_code, 404)

        workflow_id, task_key, _report = self._create_completed_analytics_workflow()
        with self.session_factory() as session:
            task = session.query(WorkflowTask).filter_by(
                workflow_id=workflow_id,
                task_key=task_key,
            ).one()
            task.status = "pending"
            session.commit()
        pending = self.client.post(
            f"/api/v1/dashboards/{dashboard_response.json()['id']}/analytics-results",
            json={"workflow_id": workflow_id, "task_id": task_key},
        )
        self.assertEqual(pending.status_code, 409)
        self.assertEqual(pending.json()["detail"]["code"], "analytics_output_unavailable")

        with self.session_factory() as session:
            foreign_owner = User(email="foreign-workflow-owner@example.invalid")
            session.add(foreign_owner)
            session.flush()
            workflow = session.get(WorkflowExecution, workflow_id)
            workflow.owner_user_id = foreign_owner.id
            session.commit()
        unauthorized = self.client.post(
            f"/api/v1/dashboards/{dashboard_response.json()['id']}/analytics-results",
            json={"workflow_id": workflow_id, "task_id": task_key},
        )
        self.assertEqual(unauthorized.status_code, 404)

    def test_dashboard_save_rejects_a_changed_normalized_artifact(self):
        dashboard_response = self.client.post(
            "/api/v1/dashboards",
            json={
                "title": "Workflow report",
                "description": None,
                "analysis_request": None,
                "chart_requests": [],
            },
        )
        workflow_id, task_key, _report = self._create_completed_analytics_workflow()
        changed_sources = [
            replace(self.sources[0], artifact_sha256="e" * 64),
            self.sources[1],
        ]
        with patch.object(
            main,
            "_load_multi_dataset_sources",
            return_value=changed_sources,
        ):
            response = self.client.post(
                f"/api/v1/dashboards/{dashboard_response.json()['id']}/analytics-results",
                json={"workflow_id": workflow_id, "task_id": task_key},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "analytics_output_stale")

    def test_capability_catalog_matches_registered_workflow_report_operations(self):
        response = self.client.get("/api/v1/chat/analytics/capabilities")

        self.assertEqual(response.status_code, 200, response.text)
        capabilities = {
            item["operation"]: item
            for item in response.json()["operations"]
        }
        registry = chat_api.ToolRegistry()
        registered = {
            item["operation"]: item
            for item in registry.analytics_capabilities()
        }
        self.assertEqual(set(capabilities), set(registered))
        self.assertEqual(
            {
                name
                for name, item in capabilities.items()
                if item["workflow_report_result"]
            },
            {
                "generate_evidence_report",
                "generate_eda_evidence_report",
            },
        )
        self.assertIn("properties", capabilities["generate_evidence_report"]["parameter_schema"])
        self.assertTrue(capabilities["generate_evidence_report"]["plan_eligible"])
        self.assertEqual(
            capabilities["generate_evidence_report"]["supported_result_references"],
            [],
        )
        self.assertEqual(response.json()["plan_limits"]["maximum_operations"], 5)
        self.assertEqual(
            response.json()["plan_limits"]["maximum_dependency_depth"],
            4,
        )

    def test_structured_analytics_execution_persists_and_reuses_verified_workflow(self):
        payload = {
            "operation": "generate_evidence_report",
            "parameters": {
                **self.definition["analysis_request"],
                "charts": self.definition["chart_requests"],
            },
        }

        executed = self.client.post(
            "/api/v1/chat/analytics/executions",
            json=payload,
        )

        self.assertEqual(executed.status_code, 200, executed.text)
        result = executed.json()
        self.assertEqual(result["operation"], payload["operation"])
        self.assertEqual(result["workflow_status"], "completed")
        self.assertEqual(result["task_status"], "completed")
        self.assertEqual(result["result"]["summary"]["joined_row_count"], 3)
        self.assertTrue(result["provenance"]["operation_id"])
        self.assertEqual(result["result"]["source_references"][0]["source_fingerprint"], "a" * 64)
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, result["workflow_id"])
            task = (
                session.query(WorkflowTask)
                .filter_by(workflow_id=result["workflow_id"])
                .one()
            )
            self.assertEqual(workflow.status, "completed")
            self.assertEqual(task.status, "completed")

        duplicate = self.client.post(
            "/api/v1/chat/analytics/executions",
            json=payload,
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["workflow_id"], result["workflow_id"])
        self.assertEqual(duplicate.json()["result_sha256"], result["result_sha256"])

    def test_structured_analytics_execution_rejects_unknown_and_invalid_operations(self):
        unknown = self.client.post(
            "/api/v1/chat/analytics/executions",
            json={"operation": "execute_sql", "parameters": {}},
        )
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(
            unknown.json()["detail"]["code"],
            "analytics_operation_unsupported",
        )

        invalid = self.client.post(
            "/api/v1/chat/analytics/executions",
            json={
                "operation": "generate_evidence_report",
                "parameters": {"sources": []},
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(
            invalid.json()["detail"]["code"],
            "analytics_parameters_invalid",
        )

    def test_structured_analytics_execution_rechecks_source_ownership(self):
        payload = {
            "operation": "generate_evidence_report",
            "parameters": {
                **self.definition["analysis_request"],
                "charts": self.definition["chart_requests"],
            },
        }
        with patch.object(
            chat_api,
            "_require_workspace_owner",
            return_value=self.other_owner,
        ):
            response = self.client.post(
                "/api/v1/chat/analytics/executions",
                json=payload,
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "analytics_source_unavailable")
        with self.session_factory() as session:
            self.assertEqual(session.query(WorkflowExecution).count(), 0)

    def _plan_operation(self, operation_id, *, depends_on=None, title=None):
        parameters = {
            **self.definition["analysis_request"],
            "charts": [
                {
                    **self.definition["chart_requests"][0],
                    "title": title or f"Revenue {operation_id}",
                }
            ],
        }
        return {
            "operation_id": operation_id,
            "operation": "generate_evidence_report",
            "parameters": parameters,
            "depends_on": depends_on or [],
        }

    def test_analytics_plan_rejects_invalid_graphs_and_handoffs_before_persistence(self):
        operation = self._plan_operation("first")
        cases = [
            (
                [operation, operation],
                "analytics_plan_duplicate_operation_id",
            ),
            (
                [self._plan_operation("first", depends_on=["missing"])],
                "analytics_plan_unknown_dependency",
            ),
            (
                [self._plan_operation("first", depends_on=["first"])],
                "analytics_plan_self_dependency",
            ),
            (
                [
                    self._plan_operation("first", depends_on=["second"]),
                    self._plan_operation("second", depends_on=["first"]),
                ],
                "analytics_plan_dependency_cycle",
            ),
            (
                [
                    self._plan_operation("one"),
                    self._plan_operation("two", depends_on=["one"]),
                    self._plan_operation("three", depends_on=["two"]),
                    self._plan_operation("four", depends_on=["three"]),
                    self._plan_operation("five", depends_on=["four"]),
                ],
                "analytics_plan_dependency_depth_exceeded",
            ),
            (
                [
                    {
                        **self._plan_operation("first"),
                        "operation": "run_monthly_forecast",
                    }
                ],
                "analytics_operation_unsupported",
            ),
            (
                [
                    {
                        **self._plan_operation("first"),
                        "parameters": {"sources": []},
                    }
                ],
                "analytics_parameters_invalid",
            ),
            (
                [
                    self._plan_operation("first"),
                    {
                        **self._plan_operation("second", depends_on=["first"]),
                        "result_references": [
                            {"operation_id": "missing", "output": "findings"}
                        ],
                    },
                ],
                "analytics_result_reference_invalid",
            ),
            (
                [
                    self._plan_operation("first"),
                    {
                        **self._plan_operation("second", depends_on=["first"]),
                        "result_references": [
                            {"operation_id": "first", "output": "unknown_metric"}
                        ],
                    },
                ],
                "analytics_result_reference_invalid",
            ),
            (
                [
                    self._plan_operation("first"),
                    {
                        **self._plan_operation("second", depends_on=["first"]),
                        "result_references": [
                            {"operation_id": "first", "output": "findings"}
                        ],
                    },
                ],
                "analytics_result_reference_unsupported",
            ),
            (
                [self._plan_operation(str(index)) for index in range(6)],
                None,
            ),
        ]

        for operations, expected_code in cases:
            with self.subTest(expected_code=expected_code, count=len(operations)):
                response = self.client.post(
                    "/api/v1/chat/analytics/plans",
                    json={"operations": operations},
                )
                self.assertEqual(response.status_code, 422, response.text)
                if expected_code is not None:
                    self.assertEqual(
                        response.json()["detail"]["code"],
                        expected_code,
                    )
        with self.session_factory() as session:
            self.assertEqual(session.query(WorkflowExecution).count(), 0)

    def test_analytics_plan_executes_dependencies_first_and_duplicate_is_idempotent(self):
        plan = {
            "operations": [
                self._plan_operation("child", depends_on=["root"], title="Child result"),
                self._plan_operation("root", title="Root result"),
            ]
        }
        observed_order = []
        original_execute = chat_api.ToolRegistry.execute

        def record_execute(registry, name, arguments, *, context=None):
            observed_order.append(arguments["charts"][0]["title"])
            return original_execute(registry, name, arguments, context=context)

        with patch.object(
            chat_api.ToolRegistry,
            "execute",
            autospec=True,
            side_effect=record_execute,
        ) as execute:
            response = self.client.post(
                "/api/v1/chat/analytics/plans",
                json=plan,
            )
            duplicate = self.client.post(
                "/api/v1/chat/analytics/plans",
                json=plan,
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(duplicate.json()["plan_id"], response.json()["plan_id"])
        self.assertEqual(observed_order, ["Root result", "Child result"])
        self.assertEqual(execute.call_count, 2)
        by_id = {
            item["operation_id"]: item
            for item in response.json()["operations"]
        }
        self.assertEqual(by_id["root"]["status"], "completed")
        self.assertEqual(by_id["child"]["status"], "completed")
        self.assertEqual(by_id["child"]["depends_on"], ["root"])
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, response.json()["workflow_id"])
            tasks = {
                task.task_key: task
                for task in session.query(WorkflowTask)
                .filter_by(workflow_id=workflow.id)
                .all()
            }
            self.assertEqual(workflow.status, "completed")
            self.assertEqual(
                tasks["child"].input_references[-1]["type"],
                "dependency_order",
            )

    def test_analytics_plan_supports_mixed_independent_report_contracts(self):
        eda_source = replace(
            self.sources[0],
            file_id=self.eda_file_id,
            checksum="e" * 64,
            artifact_sha256="c" * 64,
        )

        def load_selected_sources(_session, source_requests, _owner, _cleanup):
            if source_requests[0].file_id == self.eda_file_id:
                return [eda_source]
            return self.sources

        with patch.object(
            main,
            "_load_multi_dataset_sources",
            side_effect=load_selected_sources,
        ):
            response = self.client.post(
                "/api/v1/chat/analytics/plans",
                json={
                    "operations": [
                        self._plan_operation("multi", title="Multi-dataset report"),
                        {
                            "operation_id": "eda",
                            "operation": "generate_eda_evidence_report",
                            "parameters": {
                                "dataset_id": self.left_dataset_id,
                                "file_id": self.eda_file_id,
                                "analysis_id": self.eda_analysis_id,
                                "chart_columns": [],
                            },
                            "depends_on": ["multi"],
                        },
                    ]
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, result["workflow_id"])
            eda_task = (
                session.query(WorkflowTask)
                .filter_by(workflow_id=workflow.id, task_key="eda")
                .one()
            )
            self.assertTrue(
                chat_api._workflow_task_input_is_current(
                    session,
                    workflow,
                    eda_task,
                ),
                {
                    "inputs": eda_task.input_references,
                    "source_fingerprint": eda_task.source_fingerprint,
                    "task_status": eda_task.status,
                },
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [item["status"] for item in result["operations"]],
            ["completed", "completed"],
        )
        self.assertEqual(result["operations"][1]["depends_on"], ["multi"])
        self.assertEqual(
            result["operations"][1]["operation"],
            "generate_eda_evidence_report",
        )

    def test_analytics_plan_uses_explicit_existing_workflow_recovery(self):
        response = self.client.post(
            "/api/v1/chat/analytics/plans",
            json={
                "operations": [
                    self._plan_operation("root", title="Recovery root"),
                    self._plan_operation(
                        "child",
                        depends_on=["root"],
                        title="Recovery child",
                    ),
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        workflow_id = response.json()["workflow_id"]
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, workflow_id)
            task = (
                session.query(WorkflowTask)
                .filter_by(workflow_id=workflow_id, task_key="child")
                .one()
            )
            task.status = "pending"
            task.result_summary = None
            task.output_references = []
            task.completed_at = None
            task.input_references = [
                item
                for item in task.input_references
                if item.get("type") != "dependency_outputs"
            ]
            workflow.status = "running"
            workflow.completed_at = None
            workflow.updated_at = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                - timedelta(seconds=chat_api.MAX_RECOVERY_AGE_SECONDS + 1)
            )
            session.commit()

        recovered = self.client.post(
            f"/api/v1/chat/workflows/{workflow_id}/recover",
        )
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["status"], "completed")
        result = self.client.get(
            f"/api/v1/chat/workflows/{workflow_id}/tasks/child/analytics-result"
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["dependency_task_ids"], ["root"])

    def test_analytics_plan_reports_partial_failure_and_saves_successful_output(self):
        dashboard_response = self.client.post(
            "/api/v1/dashboards",
            json={
                "title": "Plan outputs",
                "description": None,
                "analysis_request": None,
                "chart_requests": [],
            },
        )
        self.assertEqual(dashboard_response.status_code, 201)
        plan = {
            "operations": [
                self._plan_operation("failed", title="Fail this operation"),
                self._plan_operation("blocked", depends_on=["failed"]),
                self._plan_operation("independent", title="Keep this output"),
            ]
        }
        original_execute = chat_api.ToolRegistry.execute
        execution_order = []

        def fail_selected(registry, name, arguments, *, context=None):
            title = arguments["charts"][0]["title"]
            execution_order.append(title)
            if title == "Fail this operation":
                return (
                    "",
                    ToolResult(
                        status="failed",
                        summary="Synthetic deterministic failure.",
                        error_code="synthetic_failure",
                    ),
                    "Evidence report",
                )
            return original_execute(registry, name, arguments, context=context)

        with patch.object(
            chat_api.ToolRegistry,
            "execute",
            autospec=True,
            side_effect=fail_selected,
        ):
            response = self.client.post(
                "/api/v1/chat/analytics/plans",
                json=plan,
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "partial")
        outcomes = {item["operation_id"]: item for item in result["operations"]}
        self.assertEqual(outcomes["failed"]["status"], "failed")
        self.assertEqual(outcomes["blocked"]["status"], "blocked")
        self.assertEqual(outcomes["independent"]["status"], "completed")
        self.assertEqual(
            execution_order,
            ["Fail this operation", "Fail this operation", "Keep this output"],
        )
        saved = self.client.post(
            f"/api/v1/dashboards/{dashboard_response.json()['id']}/analytics-results",
            json={
                "workflow_id": result["workflow_id"],
                "task_id": outcomes["independent"]["result_reference"]["task_id"],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        reloaded = self.client.get(
            f"/api/v1/chat/workflows/{result['workflow_id']}/tasks/"
            "independent/analytics-result"
        )
        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        self.assertEqual(reloaded.json()["workflow_status"], "partial")

    def test_analytics_plan_total_failure_and_stale_saved_output_are_truthful(self):
        plan = {
            "operations": [
                self._plan_operation("root", title="Stale after source change"),
                self._plan_operation("blocked", depends_on=["root"]),
            ]
        }
        original_execute = chat_api.ToolRegistry.execute

        def fail_root(registry, name, arguments, *, context=None):
            if arguments["charts"][0]["title"] == "Stale after source change":
                return (
                    "",
                    ToolResult(
                        status="failed",
                        summary="Synthetic failure.",
                        error_code="synthetic_failure",
                    ),
                    "Evidence report",
                )
            return original_execute(registry, name, arguments, context=context)

        with patch.object(
            chat_api.ToolRegistry,
            "execute",
            autospec=True,
            side_effect=fail_root,
        ):
            failed = self.client.post(
                "/api/v1/chat/analytics/plans",
                json=plan,
            )
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["status"], "failed")
        self.assertEqual(
            [item["status"] for item in failed.json()["operations"]],
            ["failed", "blocked"],
        )

        successful_plan = {
            "operations": [self._plan_operation("saved", title="Saved plan output")]
        }
        successful = self.client.post(
            "/api/v1/chat/analytics/plans",
            json=successful_plan,
        )
        self.assertEqual(successful.status_code, 200, successful.text)
        with self.session_factory() as session:
            dataset = session.get(Dataset, self.left_dataset_id)
            dataset.updated_at = dataset.updated_at.replace(year=dataset.updated_at.year - 1)
            session.commit()
        stale = self.client.get(
            f"/api/v1/chat/workflows/{successful.json()['workflow_id']}/tasks/"
            "saved/analytics-result"
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "analytics_output_stale")

    def test_analytics_plan_rechecks_source_ownership_before_persisting(self):
        with patch.object(
            chat_api,
            "_require_workspace_owner",
            return_value=self.other_owner,
        ):
            response = self.client.post(
                "/api/v1/chat/analytics/plans",
                json={"operations": [self._plan_operation("unauthorized")]},
            )
        self.assertEqual(response.status_code, 404)
        with self.session_factory() as session:
            self.assertEqual(session.query(WorkflowExecution).count(), 0)

    def test_completed_eda_report_can_be_saved_and_reloaded(self):
        dashboard_response = self.client.post(
            "/api/v1/dashboards",
            json={
                "title": "Pinned EDA report",
                "description": None,
                "analysis_request": None,
                "chart_requests": [],
            },
        )
        self.assertEqual(dashboard_response.status_code, 201)
        arguments = {
            "dataset_id": self.left_dataset_id,
            "file_id": self.eda_file_id,
            "analysis_id": self.eda_analysis_id,
            "chart_columns": [],
        }
        source = replace(
            self.sources[0],
            file_id=self.eda_file_id,
            checksum="e" * 64,
            artifact_sha256="c" * 64,
        )
        with patch.object(
            main,
            "_load_multi_dataset_sources",
            return_value=[source],
        ):
            executed = self.client.post(
                "/api/v1/chat/analytics/executions",
                json={
                    "operation": "generate_eda_evidence_report",
                    "parameters": arguments,
                },
            )
            self.assertEqual(executed.status_code, 200, executed.text)
            workflow_id = executed.json()["workflow_id"]
            task_key = executed.json()["task_id"]
            report = executed.json()["result"]
            saved = self.client.post(
                f"/api/v1/dashboards/{dashboard_response.json()['id']}/analytics-results",
                json={"workflow_id": workflow_id, "task_id": task_key},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            reloaded = self.client.get(
                f"/api/v1/chat/workflows/{workflow_id}/tasks/{task_key}/analytics-result"
            )

        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        self.assertEqual(reloaded.json()["operation"], "generate_eda_evidence_report")
        self.assertEqual(reloaded.json()["result"], report)

    def test_chart_request_cannot_reference_a_fabricated_metric(self):
        payload = {
            **self.definition["analysis_request"],
            "charts": [
                {
                    **self.definition["chart_requests"][0],
                    "y_field": "invented_value",
                }
            ],
        }
        response = self.client.post("/api/v1/analytics/reports", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "chart_field_unavailable")

    def test_create_requires_valid_report_and_does_not_persist_failed_definition(self):
        payload = {
            **self.definition,
            "chart_requests": [
                {
                    **self.definition["chart_requests"][0],
                    "y_field": "unknown",
                }
            ],
        }
        response = self.client.post("/api/v1/dashboards", json=payload)
        self.assertEqual(response.status_code, 422)
        with self.session_factory() as session:
            self.assertEqual(session.query(Dashboard).count(), 0)

    def test_stored_eda_report_is_grounded_and_checks_current_source_fingerprint(self):
        request = {
            "dataset_id": self.left_dataset_id,
            "file_id": self.eda_file_id,
            "analysis_id": self.eda_analysis_id,
        }
        response = self.client.post("/api/v1/analytics/eda-reports", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["summary"]["rows_analyzed"], 4)
        self.assertTrue(any(finding["supporting_metric"]["name"] == "mean" for finding in report["findings"]))
        self.assertEqual(report["charts"][0]["aggregation"], "histogram_count")
        self.assertEqual(report["refresh_policy"], "stored_analysis")

        with self.session_factory() as session:
            dataset_file = session.get(DatasetFile, self.eda_file_id)
            dataset_file.normalization_result = {
                "tables": [{"table_index": 0, "sha256": "f" * 64}],
            }
            session.commit()
        stale = self.client.post("/api/v1/analytics/eda-reports", json=request)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "eda_result_stale")

    def test_stored_eda_report_enforces_source_ownership(self):
        with patch.object(main, "_require_workspace_user", return_value=self.other_owner):
            response = self.client.post(
                "/api/v1/analytics/eda-reports",
                json={
                    "dataset_id": self.left_dataset_id,
                    "file_id": self.eda_file_id,
                    "analysis_id": self.eda_analysis_id,
                },
            )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
