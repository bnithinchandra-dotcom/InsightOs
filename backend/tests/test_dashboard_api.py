import unittest
from unittest.mock import patch

import pyarrow as pa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base
from app.models import Dashboard, Dataset, DatasetFile, EDAAnalysis, Project, User
from app.multi_dataset_analytics import AnalyticsSource
from app.provenance import build_provenance


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
