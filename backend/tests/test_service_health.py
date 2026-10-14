import unittest
from unittest.mock import Mock, patch

from app.service_health import check_storage


class StorageHealthTests(unittest.TestCase):
    def test_storage_health_check_reports_missing_buckets_without_creating_them(self):
        client = Mock()
        client.bucket_exists.side_effect = [True, False, True]
        with (
            patch("app.service_health.Minio", return_value=client),
            patch.dict(
                "os.environ",
                {
                    "MINIO_RAW_BUCKET": "raw-test",
                    "MINIO_PROCESSED_BUCKET": "processed-test",
                    "MINIO_EXPORTS_BUCKET": "exports-test",
                },
                clear=False,
            ),
        ):
            healthy, buckets = check_storage()

        self.assertFalse(healthy)
        self.assertEqual(
            buckets,
            {
                "raw-test": "available",
                "processed-test": "unavailable",
                "exports-test": "available",
            },
        )
        self.assertEqual(client.bucket_exists.call_count, 3)
        client.make_bucket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
