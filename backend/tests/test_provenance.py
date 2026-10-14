import unittest

import pyarrow as pa

from app.provenance import (
    arrow_table_fingerprint,
    build_provenance,
    schema_fingerprint,
    validate_provenance,
)


class ProvenanceTests(unittest.TestCase):
    def build(self, **overrides):
        values = {
            "operation_type": "fixture",
            "operation_version": "1",
            "configuration_sha256": "a" * 64,
            "status": "completed",
            "inputs": [{"kind": "source", "sha256": "b" * 64}],
            "outputs": [{"kind": "result", "sha256": "c" * 64}],
            "verification_status": "verified",
            "checks": [{"name": "checksum", "status": "verified"}],
            "completed_at": "2026-10-10T00:00:00+00:00",
        }
        values.update(overrides)
        return build_provenance(**values)

    def test_operation_identity_binds_inputs_outputs_and_configuration(self):
        first = self.build()
        second = self.build(
            completed_at="2026-10-11T00:00:00+00:00",
            status="partial",
            verification_status="unverifiable",
            checks=[{"name": "checksum", "status": "unverifiable"}],
        )
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertEqual(validate_provenance(first), first)

        changed = self.build(
            inputs=[{"kind": "source", "sha256": "d" * 64}]
        )
        self.assertNotEqual(first["operation_id"], changed["operation_id"])

    def test_malformed_nested_status_and_timestamp_are_rejected(self):
        with self.assertRaises(ValueError):
            self.build(checks=[{"name": "checksum", "status": []}])

        invalid = self.build()
        invalid["completed_at"] = "not-a-timestamp"
        with self.assertRaises(ValueError):
            validate_provenance(invalid)

    def test_arrow_fingerprint_is_deterministic_and_schema_bound(self):
        table = pa.table({"value": [1, 2], "label": ["a", "b"]})
        fingerprint = arrow_table_fingerprint(table)
        self.assertEqual(fingerprint, arrow_table_fingerprint(table))
        self.assertEqual(fingerprint["schema_sha256"], schema_fingerprint(table.schema))
        self.assertGreater(fingerprint["size_bytes"], 0)
        self.assertNotEqual(
            fingerprint["sha256"],
            arrow_table_fingerprint(table.select(["label", "value"]))["sha256"],
        )


if __name__ == "__main__":
    unittest.main()
