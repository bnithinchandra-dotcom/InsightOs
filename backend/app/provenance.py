"""Small deterministic provenance helpers shared by pipeline services."""

import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
import pyarrow.ipc as ipc


PROVENANCE_SCHEMA_VERSION = 1
VERIFICATION_STATUSES = frozenset(
    {"verified", "failed", "stale", "unverifiable"}
)
OPERATION_STATUSES = frozenset({"completed", "partial", "failed"})


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def schema_description(schema: pa.Schema | list[dict[str, str]]) -> list[list[str]]:
    if isinstance(schema, pa.Schema):
        return [[field.name, str(field.type)] for field in schema]
    if not isinstance(schema, list) or any(
        not isinstance(field, dict)
        or not isinstance(field.get("name"), str)
        or not isinstance(field.get("type"), str)
        for field in schema
    ):
        raise ValueError("Schema description is invalid.")
    return [[field["name"], field["type"]] for field in schema]


def schema_fingerprint(schema: pa.Schema | list[dict[str, str]]) -> str:
    return canonical_sha256(schema_description(schema))


class _DigestSink(io.RawIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.digest = hashlib.sha256()
        self.length = 0

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        self.length += len(data)
        return len(data)

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self.length

def arrow_table_fingerprint(table: pa.Table) -> dict[str, Any]:
    """Hash an Arrow IPC stream without buffering a second full table in memory."""
    if not isinstance(table, pa.Table):
        raise TypeError("An Arrow table is required.")
    sink = _DigestSink()
    with ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return {
        "sha256": sink.digest.hexdigest(),
        "size_bytes": sink.length,
        "schema_sha256": schema_fingerprint(table.schema),
    }


def build_provenance(
    *,
    operation_type: str,
    operation_version: str,
    configuration_sha256: str,
    status: str,
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    verification_status: str,
    checks: list[dict[str, str]],
    limitations: list[str] | None = None,
    parent_operation_ids: list[str] | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(operation_type, str)
        or not operation_type
        or not isinstance(operation_version, str)
        or not operation_version
        or not _is_sha256(configuration_sha256)
        or not isinstance(status, str)
        or status not in OPERATION_STATUSES
        or not isinstance(verification_status, str)
        or verification_status not in VERIFICATION_STATUSES
        or not isinstance(inputs, list)
        or any(not isinstance(item, dict) for item in inputs)
        or not isinstance(outputs, list)
        or any(not isinstance(item, dict) for item in outputs)
        or not isinstance(checks, list)
        or any(
            not isinstance(check, dict)
            or set(check) != {"name", "status"}
            or not isinstance(check["name"], str)
            or not isinstance(check["status"], str)
            or check["status"] not in VERIFICATION_STATUSES
            for check in checks
        )
        or (limitations is not None and (
            not isinstance(limitations, list)
            or any(not isinstance(item, str) for item in limitations)
        ))
        or (parent_operation_ids is not None and (
            not isinstance(parent_operation_ids, list)
            or any(not _is_sha256(item) for item in parent_operation_ids)
        ))
    ):
        raise ValueError("Provenance fields are invalid.")

    stable_identity = {
        "configuration_sha256": configuration_sha256,
        "inputs": inputs,
        "operation_type": operation_type,
        "operation_version": operation_version,
        "outputs": outputs,
        "parent_operation_ids": parent_operation_ids or [],
        "schema_version": PROVENANCE_SCHEMA_VERSION,
    }
    return {
        **stable_identity,
        "operation_id": canonical_sha256(stable_identity),
        "status": status,
        "completed_at": completed_at or datetime.now(timezone.utc).isoformat(),
        "verification": {
            "status": verification_status,
            "checks": checks,
            "limitations": limitations or [],
        },
    }


def validate_provenance(value: Any) -> dict[str, Any]:
    required = {
        "schema_version",
        "operation_id",
        "operation_type",
        "operation_version",
        "configuration_sha256",
        "inputs",
        "outputs",
        "parent_operation_ids",
        "status",
        "completed_at",
        "verification",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Provenance record has invalid fields.")
    if (
        value["schema_version"] != PROVENANCE_SCHEMA_VERSION
        or isinstance(value["schema_version"], bool)
        or not isinstance(value["operation_type"], str)
        or not value["operation_type"]
        or not isinstance(value["operation_version"], str)
        or not value["operation_version"]
        or not _is_sha256(value["configuration_sha256"])
        or not _is_sha256(value["operation_id"])
        or not isinstance(value["status"], str)
        or value["status"] not in OPERATION_STATUSES
        or not isinstance(value["inputs"], list)
        or any(not isinstance(item, dict) for item in value["inputs"])
        or not isinstance(value["outputs"], list)
        or any(not isinstance(item, dict) for item in value["outputs"])
        or not isinstance(value["parent_operation_ids"], list)
        or any(not _is_sha256(item) for item in value["parent_operation_ids"])
    ):
        raise ValueError("Provenance record is inconsistent.")
    verification = value["verification"]
    if (
        not isinstance(verification, dict)
        or set(verification) != {"status", "checks", "limitations"}
        or not isinstance(verification.get("status"), str)
        or verification["status"] not in VERIFICATION_STATUSES
        or not isinstance(verification["checks"], list)
        or not isinstance(verification["limitations"], list)
        or any(not isinstance(item, str) for item in verification["limitations"])
    ):
        raise ValueError("Provenance verification details are invalid.")
    if not isinstance(value["completed_at"], str):
        raise ValueError("Provenance completion timestamp is invalid.")
    completed_at = datetime.fromisoformat(
        value["completed_at"].replace("Z", "+00:00")
    )
    if completed_at.tzinfo is None or completed_at.utcoffset() != timezone.utc.utcoffset(completed_at):
        raise ValueError("Provenance completion timestamp must be UTC.")
    rebuilt = build_provenance(
        operation_type=value["operation_type"],
        operation_version=value["operation_version"],
        configuration_sha256=value["configuration_sha256"],
        status=value["status"],
        inputs=value["inputs"],
        outputs=value["outputs"],
        verification_status=verification["status"],
        checks=verification["checks"],
        limitations=verification["limitations"],
        parent_operation_ids=value["parent_operation_ids"],
        completed_at=value["completed_at"],
    )
    if rebuilt["operation_id"] != value["operation_id"]:
        raise ValueError("Provenance operation identity does not match its contents.")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
