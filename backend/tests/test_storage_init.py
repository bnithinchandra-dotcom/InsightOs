import unittest
from unittest.mock import Mock, patch

from app.storage_init import initialize_storage_buckets


class StorageInitializationTests(unittest.TestCase):
    def test_creates_only_missing_configured_buckets(self):
        client = Mock()
        client.bucket_exists.side_effect = [True, False, False]
        with (
            patch("app.storage_init.Minio", return_value=client),
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
            initialize_storage_buckets()

        client.make_bucket.assert_has_calls(
            [unittest.mock.call("processed-test"), unittest.mock.call("exports-test")]
        )


if __name__ == "__main__":
    unittest.main()
