import hashlib
import json
import math
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base
from app.models import (
    Dashboard,
    Dataset,
    DatasetFile,
    Project,
    User,
    WorkflowExecution,
    WorkflowTask,
)
from app.multi_dataset_analytics import AnalyticsSource
from app.normalization import normalize_file
from app.parsing import parse_dataset_file


class ForecastObjectStorage:
    def __init__(self):
        self.objects = {}
        self.metadata = {}

    def bucket_exists(self, bucket):
        return any(object_bucket == bucket for object_bucket, _key in self.objects)

    def make_bucket(self, _bucket):
        return None

    def put_object(
        self,
        bucket,
        key,
        stream,
        _size,
        content_type=None,
        metadata=None,
    ):
        self.objects[(bucket, key)] = stream.read()
        self.metadata[(bucket, key)] = metadata or {}

    def stat_object(self, bucket, key):
        content = self.objects.get((bucket, key))
        return SimpleNamespace(
            size=len(content) if content is not None else -1,
            metadata=self.metadata.get((bucket, key), {}),
        )

    def get_object(self, bucket, key):
        return BytesIO(self.objects[(bucket, key)])


class MonthlyForecastApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session_factory() as session:
            owner = User(email="forecast-owner@example.invalid")
            other_owner = User(email="forecast-other@example.invalid")
            session.add_all([owner, other_owner])
            session.flush()
            project = Project(name="Forecast project", user_id=owner.id)
            session.add(project)
            session.flush()
            dataset = Dataset(
                name="Monthly sales",
                project_id=project.id,
                status="Ready",
            )
            session.add(dataset)
            session.commit()
            self.owner = owner
            self.other_owner = other_owner
            self.dataset_id = dataset.id

        self.dates = [
            datetime(2020 + index // 12, index % 12 + 1, 15)
            for index in range(36)
        ]
        self.targets = [
            100 + 4 * index + 18 * math.sin(2 * math.pi * index / 12)
            for index in range(36)
        ]
        self.source = self._source(self.targets)
        self.session_patch = patch.object(main, "SessionLocal", self.session_factory)
        self.owner_patch = patch.object(
            main,
            "_require_workspace_user",
            return_value=self.owner,
        )
        self.source_patch = patch.object(
            main,
            "_load_multi_dataset_sources",
            return_value=[self.source],
        )
        self.session_patch.start()
        self.owner_mock = self.owner_patch.start()
        self.source_loader_mock = self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.addCleanup(self.owner_patch.stop)
        self.addCleanup(self.session_patch.stop)
        self.client = TestClient(main.app)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _source(self, targets, artifact_sha256="b" * 64, dates=None):
        return AnalyticsSource(
            alias="forecast",
            dataset_id=self.dataset_id,
            file_id=27,
            table_index=0,
            table=pa.table(
                {
                    "order_date": dates if dates is not None else self.dates,
                    "net_sales": targets,
                }
            ),
            checksum="a" * 64,
            artifact_sha256=artifact_sha256,
            normalization_configuration_sha256="c" * 64,
        )

    def _request(self):
        return {
            "dataset_id": self.dataset_id,
            "file_id": 27,
            "table_index": 0,
            "date_column": "order_date",
            "target_column": "net_sales",
            "horizon": 3,
        }

    def test_forecast_uses_verified_source_and_persists_task_before_training(self):
        train = main.train_monthly_forecast

        def verify_task_was_persisted(**kwargs):
            with self.session_factory() as session:
                task = session.query(WorkflowTask).one()
                self.assertEqual(task.status, "running")
                self.assertEqual(task.source_fingerprint, kwargs["source_fingerprint"])
                self.assertEqual(task.dataset_id, self.dataset_id)
            return train(**kwargs)

        with patch.object(
            main,
            "train_monthly_forecast",
            side_effect=verify_task_was_persisted,
        ) as trainer:
            response = self.client.post(
                "/api/v1/analytics/forecasts/monthly",
                json=self._request(),
            )

        self.assertEqual(response.status_code, 200, response.text)
        trainer.assert_called_once()
        self.assertEqual(self.source_loader_mock.call_count, 2)
        selected_sources = self.source_loader_mock.call_args.args[1]
        self.assertEqual(selected_sources[0].dataset_id, self.dataset_id)
        self.assertEqual(selected_sources[0].file_id, 27)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(len(body["result"]["forecast"]), 3)
        self.assertNotIn("model_artifact", body["result"])
        self.assertEqual(
            body["result"]["model_artifact_reference"]["sha256"],
            body["result"]["model_artifact_sha256"],
        )
        self.assertEqual(
            body["result"]["chart"]["data"]["series"][0]["name"],
            "Observed history",
        )
        self.assertEqual(
            body["result"]["chart"]["data"]["series"][1]["name"],
            "Forecast",
        )
        self.assertEqual(len(body["result"]["historical"]), 36)
        self.assertEqual(len(body["result"]["forecast"]), 3)
        self.assertEqual(body["result"]["result_schema_version"], 2)
        self.assertEqual(
            body["result"]["evaluation"]["method"],
            "chronological_holdout",
        )
        self.assertEqual(body["result"]["evaluation"]["sample_count"], 8)
        self.assertIn(
            "seasonal_naive_12",
            body["result"]["evaluation"]["baselines"],
        )
        self.assertIn(
            body["result"]["comparison"]["outcome"],
            {"beats_baseline", "within_tolerance", "worse_than_baseline"},
        )
        self.assertIn(
            body["result"]["uncertainty"]["status"],
            {"available", "unavailable"},
        )
        self.assertIn(body["result"]["suitability"]["status"], {"suitable", "warning"})
        self.assertEqual(
            body["result"]["references"][0]["source_fingerprint"],
            body["result"]["source_fingerprint"],
        )
        retrieved = self.client.get(
            f"/api/v1/analytics/forecasts/monthly/{body['workflow_id']}"
        )
        self.assertEqual(retrieved.status_code, 200, retrieved.text)
        self.assertEqual(retrieved.json()["task"]["result"], body["result"])
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, body["workflow_id"])
            task = session.get(WorkflowTask, body["task_id"])
            self.assertEqual(workflow.status, "completed")
            self.assertEqual(task.status, "completed")
            self.assertEqual(task.result_summary["forecast"], body["result"]["forecast"])
            self.assertEqual(
                task.result_summary["model_artifact_sha256"],
                body["result"]["model_artifact_sha256"],
            )
            self.assertEqual(
                task.input_references[0]["artifact_sha256"],
                self.source.artifact_sha256,
            )

    def test_forecast_result_retrieval_is_owner_scoped(self):
        created = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.owner_mock.return_value = self.other_owner

        response = self.client.get(
            f"/api/v1/analytics/forecasts/monthly/{created.json()['workflow_id']}"
        )

        self.assertEqual(response.status_code, 404)

    def _create_forecast_dashboard(self):
        response = self.client.post(
            "/api/v1/dashboards",
            json={"title": "Forecast dashboard"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_completed_forecast_is_saved_as_a_durable_dashboard_reference(self):
        forecast = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )
        self.assertEqual(forecast.status_code, 200, forecast.text)
        dashboard = self._create_forecast_dashboard()

        saved = self.client.post(
            f"/api/v1/dashboards/{dashboard['id']}/forecasts",
            json={"workflow_id": forecast.json()["workflow_id"]},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        component = saved.json()["forecast_component"]
        self.assertFalse(saved.json()["duplicate"])
        self.assertEqual(component["workflow_id"], forecast.json()["workflow_id"])
        self.assertEqual(component["dataset_id"], self.dataset_id)
        self.assertEqual(component["file_id"], 27)
        self.assertEqual(component["date_column"], "order_date")
        self.assertEqual(component["target_column"], "net_sales")
        self.assertEqual(component["aggregation"], "sum")
        self.assertEqual(component["horizon"], 3)
        self.assertEqual(
            component["source_fingerprint"],
            forecast.json()["result"]["source_fingerprint"],
        )
        self.assertEqual(
            component["chart_specification"]["chart_type"],
            "line",
        )
        self.assertNotIn("historical", component)
        self.assertNotIn("model_artifact", component)

        reloaded = self.client.get(f"/api/v1/dashboards/{dashboard['id']}")
        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        persisted_component = reloaded.json()["definition"]["forecast_components"][0]
        self.assertEqual(persisted_component, component)

        updated = self.client.put(
            f"/api/v1/dashboards/{dashboard['id']}",
            json={
                "title": "Forecast dashboard renamed",
                "analysis_request": None,
                "chart_requests": [],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            updated.json()["definition"]["forecast_components"],
            [component],
        )

        duplicate = self.client.post(
            f"/api/v1/dashboards/{dashboard['id']}/forecasts",
            json={"workflow_id": forecast.json()["workflow_id"]},
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(duplicate.json()["forecast_component"], component)
        with self.session_factory() as session:
            stored = session.get(Dashboard, dashboard["id"])
            self.assertEqual(len(stored.definition["forecast_components"]), 1)

    def test_saving_forecast_checks_dashboard_and_forecast_ownership(self):
        forecast = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )
        self.assertEqual(forecast.status_code, 200, forecast.text)
        dashboard = self._create_forecast_dashboard()
        payload = {"workflow_id": forecast.json()["workflow_id"]}

        self.owner_mock.return_value = self.other_owner
        unauthorized_dashboard = self.client.post(
            f"/api/v1/dashboards/{dashboard['id']}/forecasts",
            json=payload,
        )
        self.assertEqual(unauthorized_dashboard.status_code, 404)

        self.owner_mock.return_value = self.owner
        another_dashboard = self._create_forecast_dashboard()
        self.owner_mock.return_value = self.other_owner
        unauthorized_forecast = self.client.post(
            f"/api/v1/dashboards/{another_dashboard['id']}/forecasts",
            json=payload,
        )
        self.assertEqual(unauthorized_forecast.status_code, 404)

    def test_saving_incomplete_or_missing_forecasts_is_rejected(self):
        self.source = self._source([None, *self.targets[1:]])
        self.source_patch.stop()
        self.source_patch = patch.object(
            main,
            "_load_multi_dataset_sources",
            return_value=[self.source],
        )
        self.source_loader_mock = self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        failed = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )
        self.assertEqual(failed.status_code, 422, failed.text)
        dashboard = self._create_forecast_dashboard()

        incomplete = self.client.post(
            f"/api/v1/dashboards/{dashboard['id']}/forecasts",
            json={"workflow_id": failed.json()["detail"]["workflow_id"]},
        )
        self.assertEqual(incomplete.status_code, 409)
        self.assertEqual(
            incomplete.json()["detail"]["code"],
            "forecast_not_completed",
        )
        missing = self.client.post(
            f"/api/v1/dashboards/{dashboard['id']}/forecasts",
            json={"workflow_id": "f" * 32},
        )
        self.assertEqual(missing.status_code, 404)

    def test_saving_forecast_rejects_a_stale_normalized_source(self):
        forecast = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )
        self.assertEqual(forecast.status_code, 200, forecast.text)
        dashboard = self._create_forecast_dashboard()
        self.source_loader_mock.return_value = [
            self._source(self.targets, artifact_sha256="d" * 64)
        ]

        response = self.client.post(
            f"/api/v1/dashboards/{dashboard['id']}/forecasts",
            json={"workflow_id": forecast.json()["workflow_id"]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "forecast_source_stale",
        )

    def test_duplicate_forecast_request_reuses_the_persisted_execution(self):
        with patch.object(main, "train_monthly_forecast", wraps=main.train_monthly_forecast) as trainer:
            first = self.client.post(
                "/api/v1/analytics/forecasts/monthly",
                json=self._request(),
            )
            second = self.client.post(
                "/api/v1/analytics/forecasts/monthly",
                json=self._request(),
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["workflow_id"], second.json()["workflow_id"])
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertEqual(first.json()["result"], second.json()["result"])
        trainer.assert_called_once()
        with self.session_factory() as session:
            self.assertEqual(session.query(WorkflowExecution).count(), 1)
            self.assertEqual(session.query(WorkflowTask).count(), 1)

    def test_retrieval_rejects_a_forecast_after_the_normalized_source_changes(self):
        created = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.source_loader_mock.return_value = [
            self._source(self.targets, artifact_sha256="d" * 64)
        ]

        response = self.client.get(
            f"/api/v1/analytics/forecasts/monthly/{created.json()['workflow_id']}"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "forecast_source_stale")

    def test_unsupported_aggregation_is_structured_and_does_not_create_a_workflow(self):
        response = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json={**self._request(), "aggregation": "average"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_aggregation")
        with self.session_factory() as session:
            self.assertEqual(session.query(WorkflowExecution).count(), 0)

    def test_forecast_retrieval_detects_a_corrupt_persisted_artifact(self):
        created = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )
        self.assertEqual(created.status_code, 200, created.text)
        with self.session_factory() as session:
            task = session.get(WorkflowTask, created.json()["task_id"])
            task.result_summary = {
                **task.result_summary,
                "model_artifact": {
                    **task.result_summary["model_artifact"],
                    "algorithm": "tampered",
                },
            }
            session.commit()

        response = self.client.get(
            f"/api/v1/analytics/forecasts/monthly/{created.json()['workflow_id']}"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "forecast_artifact_integrity_failed",
        )

    def test_legacy_forecast_artifacts_use_explicit_compatibility_contract(self):
        created = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )
        self.assertEqual(created.status_code, 200, created.text)
        workflow_id = created.json()["workflow_id"]
        with self.session_factory() as session:
            task = session.get(WorkflowTask, created.json()["task_id"])
            legacy = dict(task.result_summary)
            legacy.pop("result_schema_version", None)
            legacy.pop("result_sha256", None)
            artifact = dict(legacy["model_artifact"])
            artifact.pop("result_schema_version", None)
            artifact["artifact_version"] = 1
            artifact["model_version"] = 1
            artifact["parameters"] = {
                "random_seed": 0,
                "split": "chronological_last_20_percent_minimum_3_months",
                "estimator": "sklearn.linear_model.LinearRegression",
            }
            validation = dict(artifact["validation"])
            for key in (
                "method",
                "period_start",
                "period_end",
                "baselines",
                "comparison",
                "mae_tolerance_relative",
                "backtesting",
                "uncertainty",
                "suitability",
            ):
                validation.pop(key, None)
            for metric_name in ("candidate", "baseline"):
                metric = dict(validation[metric_name])
                metric.pop("wape_status", None)
                metric.pop("wape_reason", None)
                validation[metric_name] = metric
            artifact["validation"] = validation
            legacy["model_artifact"] = artifact
            artifact_bytes = json.dumps(
                artifact,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
            legacy["model_artifact_sha256"] = artifact_sha256
            legacy["references"] = [
                dict(reference)
                for reference in legacy["references"]
            ]
            legacy["references"][1]["sha256"] = artifact_sha256
            for point in legacy["forecast"]:
                point.pop("lower_bound", None)
                point.pop("upper_bound", None)
            legacy["result_sha256"] = main._monthly_forecast_result_sha256(legacy)
            task.result_summary = legacy
            session.commit()

        response = self.client.get(
            f"/api/v1/analytics/forecasts/monthly/{workflow_id}"
        )

        self.assertEqual(response.status_code, 200, response.text)
        public = response.json()["task"]["result"]
        self.assertEqual(public["result_schema_version"], 1)
        self.assertEqual(public["uncertainty"]["status"], "unavailable")
        self.assertIn("predates", public["uncertainty"]["reason"])
        self.assertEqual(public["suitability"]["status"], "unavailable")

    def test_invalid_selected_column_is_rejected_before_creating_workflow(self):
        response = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json={**self._request(), "date_column": "missing_date"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["code"],
            "forecast_column_not_found",
        )
        with self.session_factory() as session:
            self.assertEqual(session.query(WorkflowExecution).count(), 0)

    def test_missing_normalized_artifact_fingerprint_is_rejected(self):
        self.source = self._source(self.targets, artifact_sha256=None)
        self.source_loader_mock.return_value = [self.source]

        response = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "forecast_source_version_unavailable",
        )
        with self.session_factory() as session:
            self.assertEqual(session.query(WorkflowExecution).count(), 0)

    def test_invalid_values_leave_a_failed_durable_task(self):
        self.source = self._source([None, *self.targets[1:]])
        self.source_patch.stop()
        self.source_patch = patch.object(
            main,
            "_load_multi_dataset_sources",
            return_value=[self.source],
        )
        self.source_loader_mock = self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

        response = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json=self._request(),
        )

        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "invalid_targets")
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, detail["workflow_id"])
            task = session.get(WorkflowTask, detail["task_id"])
            self.assertEqual(workflow.status, "failed")
            self.assertEqual(task.status, "failed")
            self.assertEqual(task.error_code, "invalid_targets")
            self.assertEqual(task.source_fingerprint, workflow.source_scope["source_fingerprint"])

    def test_maximum_supported_history_and_horizon_fit_persisted_result_limit(self):
        dates = [
            datetime(2000 + index // 12, index % 12 + 1, 15)
            for index in range(240)
        ]
        targets = [
            100 + index + 10 * math.sin(2 * math.pi * index / 12)
            for index in range(240)
        ]
        self.source_loader_mock.return_value = [
            self._source(targets, dates=dates)
        ]

        response = self.client.post(
            "/api/v1/analytics/forecasts/monthly",
            json={**self._request(), "horizon": 24},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["result"]["forecast"]), 24)
        with self.session_factory() as session:
            task = session.get(WorkflowTask, response.json()["task_id"])
            self.assertEqual(task.status, "completed")
            self.assertTrue(task.result_summary["model_artifact"])

    def test_api_reads_and_verifies_a_real_normalized_parquet_artifact(self):
        self.source_patch.stop()
        input_parquet = BytesIO()
        pq.write_table(
            pa.table(
                {
                    "order_date": pa.array(self.dates, type=pa.date32()),
                    "net_sales": pa.array(self.targets, type=pa.float64()),
                }
            ),
            input_parquet,
        )
        raw_bytes = input_parquet.getvalue()
        raw_checksum = hashlib.sha256(raw_bytes).hexdigest()
        parsing_result = parse_dataset_file(
            "monthly.parquet",
            BytesIO(raw_bytes),
            len(raw_bytes),
        ).as_dict()
        storage = ForecastObjectStorage()
        raw_key = f"{self.dataset_id}/monthly.parquet"
        storage.objects[("insightos-raw", raw_key)] = raw_bytes
        storage.metadata[("insightos-raw", raw_key)] = {"sha256": raw_checksum}
        with self.session_factory() as session:
            dataset_file = DatasetFile(
                dataset_id=self.dataset_id,
                filename="monthly.parquet",
                storage_key=raw_key,
                file_size_bytes=len(raw_bytes),
                checksum=raw_checksum,
                status="Ready",
                parsing_result=parsing_result,
                normalization_status="Processing",
            )
            session.add(dataset_file)
            session.commit()
            file_id = dataset_file.id

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "monthly.parquet"
            source_path.write_bytes(raw_bytes)
            normalization_result = normalize_file(
                source_path,
                "monthly.parquet",
                parsing_result,
                dataset_id=self.dataset_id,
                file_id=file_id,
                source_checksum=raw_checksum,
                storage=storage,
                processed_bucket="insightos-processed",
            )
        with self.session_factory() as session:
            dataset_file = session.get(DatasetFile, file_id)
            dataset_file.normalization_result = normalization_result
            dataset_file.normalization_status = "Ready"
            session.commit()

        with (
            patch.object(main, "get_storage_client", return_value=storage),
            patch.object(main, "getenv", side_effect=lambda _name, default=None: default),
        ):
            response = self.client.post(
                "/api/v1/analytics/forecasts/monthly",
                json={**self._request(), "file_id": file_id},
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertEqual(len(result["forecast"]), 3)
        self.assertEqual(
            result["references"][0]["artifact_sha256"],
            normalization_result["tables"][0]["sha256"],
        )


if __name__ == "__main__":
    unittest.main()
