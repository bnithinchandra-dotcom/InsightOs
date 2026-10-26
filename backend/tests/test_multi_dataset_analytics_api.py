import unittest
from contextlib import ExitStack
from unittest.mock import patch

import pyarrow as pa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base
from app.models import Dataset, DatasetFile, Project, User
from app.multi_dataset_analytics import AnalyticsSource


class MultiDatasetAnalyticsApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session_factory() as session:
            self.owner = User(email="analytics-owner@example.invalid")
            other_owner = User(email="analytics-other@example.invalid")
            session.add_all([self.owner, other_owner])
            session.flush()
            project = Project(name="Analytics project", user_id=self.owner.id)
            other_project = Project(name="Private project", user_id=other_owner.id)
            session.add_all([project, other_project])
            session.flush()
            self.dataset_one = Dataset(
                name="Customer data",
                project_id=project.id,
                status="Ready",
            )
            self.dataset_two = Dataset(
                name="Sales data",
                project_id=project.id,
                status="Ready",
            )
            self.private_dataset = Dataset(
                name="Private data",
                project_id=other_project.id,
                status="Ready",
            )
            session.add_all([self.dataset_one, self.dataset_two, self.private_dataset])
            session.commit()
            self.owner_id = self.owner.id
            self.dataset_one_id = self.dataset_one.id
            self.dataset_two_id = self.dataset_two.id
            self.private_dataset_id = self.private_dataset.id

        self.sources = [
            AnalyticsSource(
                alias="customers",
                dataset_id=self.dataset_one_id,
                file_id=11,
                table_index=0,
                table=pa.table(
                    {
                        "customer_id": [1, 2],
                        "region": ["west", "east"],
                    }
                ),
                checksum="a" * 64,
            ),
            AnalyticsSource(
                alias="sales",
                dataset_id=self.dataset_two_id,
                file_id=22,
                table_index=0,
                table=pa.table(
                    {
                        "customer_id": [1, 1, 2],
                        "amount": [2, 3, 7],
                    }
                ),
                checksum="b" * 64,
            ),
        ]
        self.session_patch = patch.object(main, "SessionLocal", self.session_factory)
        self.owner_patch = patch.object(
            main,
            "_require_workspace_user",
            return_value=self.owner,
        )
        self.session_patch.start()
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        self.addCleanup(self.session_patch.stop)
        self.client = TestClient(main.app)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_relationship_and_analysis_endpoints_return_deterministic_results(self):
        with patch.object(main, "_load_multi_dataset_sources", return_value=self.sources):
            recommendations = self.client.post(
                "/api/v1/analytics/multi-dataset/relationships",
                json={
                    "sources": [
                        {
                            "alias": "customers",
                            "dataset_id": self.dataset_one_id,
                            "file_id": 11,
                        },
                        {
                            "alias": "sales",
                            "dataset_id": self.dataset_two_id,
                            "file_id": 22,
                        },
                    ]
                },
            )
            analysis = self.client.post(
                "/api/v1/analytics/multi-dataset/analyze",
                json={
                    "sources": [
                        {
                            "alias": "customers",
                            "dataset_id": self.dataset_one_id,
                            "file_id": 11,
                        },
                        {
                            "alias": "sales",
                            "dataset_id": self.dataset_two_id,
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
                            "name": "sales_total",
                            "operation": "sum",
                            "column": {"alias": "sales", "column": "amount"},
                        }
                    ],
                },
            )

        self.assertEqual(recommendations.status_code, 200, recommendations.text)
        self.assertTrue(recommendations.json()["recommendations"])
        self.assertEqual(analysis.status_code, 200, analysis.text)
        self.assertEqual(analysis.json()["joined_row_count"], 3)
        self.assertEqual(
            analysis.json()["results"],
            [
                {"customers.region": "east", "sales_total": 7},
                {"customers.region": "west", "sales_total": 5},
            ],
        )

    def test_invalid_join_request_is_rejected_by_api_validation(self):
        response = self.client.post(
            "/api/v1/analytics/multi-dataset/analyze",
            json={
                "sources": [
                    {"alias": "left", "dataset_id": self.dataset_one_id, "file_id": 1},
                    {"alias": "right", "dataset_id": self.dataset_two_id, "file_id": 2},
                ],
                "joins": [],
                "metrics": [{"name": "rows", "operation": "count"}],
                "unexpected_sql": "SELECT *",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_invalid_join_order_is_rejected_before_loading_artifacts(self):
        with patch.object(main, "_load_multi_dataset_sources") as load_sources:
            response = self.client.post(
                "/api/v1/analytics/multi-dataset/analyze",
                json={
                    "sources": [
                        {"alias": "left", "dataset_id": self.dataset_one_id, "file_id": 1},
                        {"alias": "right", "dataset_id": self.dataset_two_id, "file_id": 2},
                    ],
                    "joins": [
                        {
                            "left_key": {"alias": "left", "column": "id"},
                            "right_key": {"alias": "left", "column": "id"},
                            "join_type": "inner",
                        }
                    ],
                    "metrics": [{"name": "rows", "operation": "count"}],
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_join_order")
        load_sources.assert_not_called()

    def test_each_dataset_is_owner_checked_before_source_loading(self):
        with self.session_factory() as session:
            private_owner = session.query(User).filter_by(
                email="analytics-other@example.invalid"
            ).one()
            owned_file = DatasetFile(
                dataset_id=self.dataset_one_id,
                filename="owned-source.csv",
                storage_key=f"{self.dataset_one_id}/owned-source.csv",
                file_size_bytes=10,
                checksum="a" * 64,
                status="Ready",
            )
            session.add(owned_file)
            session.commit()
            request = main.MultiDatasetRecommendationRequest(
                sources=[
                    main.MultiDatasetSourceRequest(
                        alias="owned",
                        dataset_id=self.dataset_one_id,
                        file_id=owned_file.id,
                    ),
                    main.MultiDatasetSourceRequest(
                        alias="private",
                        dataset_id=self.private_dataset_id,
                        file_id=91,
                    ),
                ]
            )
            with ExitStack() as cleanup, patch.object(
                main,
                "get_storage_client",
            ) as storage:
                with self.assertRaises(main.HTTPException) as raised:
                    main._load_multi_dataset_sources(
                        session,
                        request.sources,
                        self.owner,
                        cleanup,
                    )
            self.assertEqual(raised.exception.status_code, 404)
            storage.assert_not_called()
            self.assertNotEqual(private_owner.id, self.owner_id)


if __name__ == "__main__":
    unittest.main()
