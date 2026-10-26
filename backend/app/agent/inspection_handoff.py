from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

from app.provenance import (
    canonical_sha256,
    normalize_verification_status,
    validate_provenance,
)


class HandoffModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceReference(HandoffModel):
    kind: Literal["profile", "quality_report", "schema_analysis"]
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)
    source_sha256: StrictStr | None
    record_id: StrictInt | None = None
    record_key: StrictStr | None = None
    resolution: Literal["resolved_persisted_record", "stale", "unavailable"]
    provenance_operation_id: StrictStr | None = None
    verification_status: Literal[
        "verified",
        "failed",
        "stale",
        "unverifiable",
        "OBSERVED",
        "COMPUTED",
        "INFERRED",
        "ASSUMED",
        "UNKNOWN",
        "FAILED",
    ]
    jev_resolution: Literal["unresolved"]


class ColumnInspection(HandoffModel):
    name: StrictStr
    position: StrictInt = Field(ge=0)
    physical_types: list[StrictStr]
    missing_value_count: StrictInt = Field(ge=0)
    empty_value_count: StrictInt = Field(ge=0)
    distinct_value_count: StrictInt | None = Field(ge=0)
    distinct_count_exact: StrictBool
    identity_evidence_classification: Literal["OBSERVED"]
    measurements_evidence_classification: Literal["COMPUTED"]
    evidence_reference: EvidenceReference


class TableInspection(HandoffModel):
    table_index: StrictInt = Field(ge=0)
    name: StrictStr | None
    row_count: StrictInt = Field(ge=0)
    column_count: StrictInt = Field(ge=0)
    duplicate_row_count: StrictInt | None = Field(ge=0)
    duplicate_row_count_exact: StrictBool
    columns: list[ColumnInspection]
    evidence_classification: Literal["OBSERVED", "COMPUTED"]
    evidence_reference: EvidenceReference


class QualityFinding(HandoffModel):
    finding_id: StrictStr
    rule_id: StrictStr
    classification: StrictStr
    title: StrictStr
    scope: dict[str, StrictInt | StrictStr | None] | None
    measurements: dict[str, StrictInt | StrictFloat | StrictBool | StrictStr | None]
    confidence: StrictFloat = Field(ge=0, le=1)
    evidence_classification: Literal["COMPUTED"]
    evidence_reference: EvidenceReference


class CandidateKey(HandoffModel):
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)
    table_index: StrictInt = Field(ge=0)
    column_name: StrictStr
    status: Literal["candidate", "not_candidate", "not_evaluated"]
    evidence_classification: Literal["COMPUTED"]
    evidence_reference: EvidenceReference


class CandidateIdentifier(HandoffModel):
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)
    table_index: StrictInt = Field(ge=0)
    column_name: StrictStr
    status: Literal["heuristic_candidate"]
    evidence_classification: Literal["INFERRED"]
    evidence_reference: EvidenceReference


class RelationshipEndpoint(HandoffModel):
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)
    table_index: StrictInt = Field(ge=0)
    table_name: StrictStr | None
    column_name: StrictStr
    position: StrictInt = Field(ge=0)


class RelationshipHypothesis(HandoffModel):
    source: RelationshipEndpoint
    target: RelationshipEndpoint
    status: Literal["inferred", "ambiguous", "rejected"]
    cardinality_hypothesis: StrictStr
    measurements: dict[str, StrictInt | StrictFloat | StrictBool | StrictStr | None]
    risks: list[StrictStr]
    evidence_classification: Literal["INFERRED"]
    evidence_reference: EvidenceReference


class PreprocessingRecommendation(HandoffModel):
    observed_issue: StrictStr
    affected_columns: list[dict[str, StrictInt | StrictStr]]
    operation_id: StrictStr
    operation_status: StrictStr
    expected_consequences: list[StrictStr]
    required_approvals: list[StrictStr]
    execution_capability: StrictStr
    follow_up_validation: list[StrictStr]
    evidence_reference: EvidenceReference


class InspectionHandoff(HandoffModel):
    schema_version: Literal[1]
    handoff_id: StrictStr = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    status: Literal["ready", "partial", "failed"]
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)
    filename: StrictStr
    source_sha256: StrictStr | None
    source_updated_at: StrictStr | None
    inspection_execution_id: None
    tables: list[TableInspection]
    quality_status: StrictStr
    warnings: list[StrictStr]
    limitations: list[StrictStr]
    quality_findings: list[QualityFinding]
    candidate_keys: list[CandidateKey]
    candidate_identifiers: list[CandidateIdentifier]
    relationships: list[RelationshipHypothesis]
    preprocessing_recommendations: list[PreprocessingRecommendation]
    evidence_references: list[EvidenceReference]
    unresolved: list[StrictStr]


def _require_dict(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _reference(
    kind: str,
    dataset_id: int,
    file_id: int,
    source_sha256: str | None,
    *,
    record_id: int | None = None,
    record_key: str | None = None,
    resolution: str = "resolved_persisted_record",
    provenance_operation_id: str | None = None,
    verification_status: str = "UNKNOWN",
) -> dict:
    return {
        "kind": kind,
        "dataset_id": dataset_id,
        "file_id": file_id,
        "source_sha256": source_sha256,
        "record_id": record_id,
        "record_key": record_key,
        "resolution": resolution,
        "provenance_operation_id": provenance_operation_id,
        "verification_status": verification_status,
        "jev_resolution": "unresolved",
    }


def _scalar_measurements(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str)
        and (
            item is None
            or isinstance(item, (str, int, bool))
            or isinstance(item, float)
            and math.isfinite(item)
        )
    }


def _validate_schema_provenance(schema_analysis: dict) -> bool:
    provenance = schema_analysis.get("provenance")
    try:
        validated = validate_provenance(provenance)
    except (KeyError, TypeError, ValueError):
        return False
    result = schema_analysis.get("result")
    if not isinstance(result, dict):
        return False
    expected_output = {
        "kind": "schema_analysis",
        "analysis_key": schema_analysis.get("analysis_key"),
        "result_sha256": canonical_sha256(result),
    }
    return (
        validated.get("operation_type") == "schema_architect"
        and validated.get("status") == "completed"
        and validated.get("verification", {}).get("status") == "verified"
        and expected_output in validated.get("outputs", [])
    )


def _provenance_verification_status(record: Any) -> str:
    if not isinstance(record, dict):
        return "UNKNOWN"
    verification = record.get("verification")
    if not isinstance(verification, dict):
        return "UNKNOWN"
    try:
        return normalize_verification_status(verification.get("status"))
    except ValueError:
        return "UNKNOWN"


def _schema_tables(
    schema_analysis: dict | None,
    dataset_id: int,
    file_id: int,
    profile_tables: list[dict],
) -> tuple[list[dict], dict[tuple[int, int, int], dict], list[str]]:
    if not isinstance(schema_analysis, dict):
        return [], {}, ["A current persisted schema analysis is unavailable."]
    if (
        schema_analysis.get("status") != "Completed"
        or schema_analysis.get("stale") is not False
        or not _validate_schema_provenance(schema_analysis)
    ):
        return [], {}, ["The stored schema analysis is stale, incomplete, or unverified."]
    result = schema_analysis.get("result")
    schema = result.get("schema") if isinstance(result, dict) else None
    raw_tables = schema.get("tables") if isinstance(schema, dict) else None
    if not isinstance(raw_tables, list):
        return [], {}, ["The stored schema analysis has no valid table results."]
    configuration = schema_analysis.get("configuration")
    selections = configuration.get("files") if isinstance(configuration, dict) else None
    if not isinstance(selections, list):
        raise ValueError("Schema analysis has no valid input file selection.")
    selected_tables: dict[int, set[int] | None] = {}
    for selection in selections:
        if (
            not isinstance(selection, dict)
            or not isinstance(selection.get("file_id"), int)
            or isinstance(selection.get("file_id"), bool)
            or not isinstance(selection.get("table_indices"), list)
            and selection.get("table_indices") is not None
        ):
            raise ValueError("Schema analysis has an invalid input file selection.")
        selected_file_id = selection["file_id"]
        table_indices = selection.get("table_indices")
        if table_indices is not None and any(
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            for index in table_indices
        ):
            raise ValueError("Schema analysis has invalid selected table indices.")
        selected_tables[selected_file_id] = (
            set(table_indices) if table_indices is not None else None
        )

    known_tables: dict[tuple[int, int, int], dict] = {}
    target_tables = []
    profile_by_index = {
        index: table for index, table in enumerate(profile_tables)
    }
    for table in raw_tables:
        if not isinstance(table, dict):
            raise ValueError("Schema analysis contains an invalid table.")
        identity = table.get("identity")
        columns = table.get("columns")
        if (
            not isinstance(identity, dict)
            or not isinstance(identity.get("dataset_id"), int)
            or isinstance(identity.get("dataset_id"), bool)
            or not isinstance(identity.get("file_id"), int)
            or isinstance(identity.get("file_id"), bool)
            or not isinstance(identity.get("table_index"), int)
            or isinstance(identity.get("table_index"), bool)
            or not isinstance(columns, list)
        ):
            raise ValueError("Schema analysis contains an invalid table identity.")
        table_dataset_id = identity["dataset_id"]
        table_file_id = identity["file_id"]
        table_index = identity["table_index"]
        if (
            table_dataset_id != dataset_id
            or table_file_id not in selected_tables
            or selected_tables[table_file_id] is not None
            and table_index not in selected_tables[table_file_id]
        ):
            raise ValueError("Schema analysis table is outside its persisted input selection.")
        if (table_dataset_id, table_file_id, table_index) in known_tables:
            raise ValueError("Schema analysis contains duplicate table identities.")
        profile_table = (
            profile_by_index.get(table_index)
            if table_dataset_id == dataset_id and table_file_id == file_id
            else None
        )
        profile_columns = (
            profile_table.get("columns") if isinstance(profile_table, dict) else None
        )
        column_names: dict[int, str] = {}
        for column in columns:
            if (
                not isinstance(column, dict)
                or not isinstance(column.get("position"), int)
                or isinstance(column.get("position"), bool)
                or not isinstance(column.get("name"), str)
            ):
                raise ValueError("Schema analysis contains an invalid column reference.")
            position = column["position"]
            name = column["name"]
            if position in column_names:
                raise ValueError("Schema analysis contains duplicate column positions.")
            column_names[position] = name
            if profile_columns is not None and (
                position >= len(profile_columns)
                or profile_columns[position].get("position") != position
                or profile_columns[position].get("name") != name
            ):
                raise ValueError("Schema analysis references a column absent from the profile.")
        record = {
            "identity": identity,
            "columns": columns,
            "column_names": column_names,
            "name": identity.get("table_name"),
        }
        known_tables[(table_dataset_id, table_file_id, table_index)] = record
        if profile_table is not None:
            if len(columns) != len(profile_columns):
                raise ValueError("Schema analysis column count does not match the profile.")
            target_tables.append(record)
    if not target_tables:
        return [], known_tables, [
            "The current schema analysis does not include this file."
        ]
    return target_tables, known_tables, []


def build_inspection_handoff(
    *,
    file_record: dict,
    profile_response: dict,
    quality_response: dict,
    schema_analysis: dict | None = None,
    preprocessing_operations: list[dict] | None = None,
) -> dict:
    file_record = _require_dict(file_record, "File record")
    profile_response = _require_dict(profile_response, "Profile response")
    quality_response = _require_dict(quality_response, "Quality response")
    dataset_id = file_record.get("dataset_id")
    file_id = file_record.get("id")
    checksum = file_record.get("checksum")
    if (
        not isinstance(dataset_id, int)
        or isinstance(dataset_id, bool)
        or not isinstance(file_id, int)
        or isinstance(file_id, bool)
        or profile_response.get("dataset_id") != dataset_id
        or profile_response.get("file_id") != file_id
        or quality_response.get("dataset_id") != dataset_id
        or quality_response.get("file_id") != file_id
    ):
        raise ValueError("Inspection inputs do not identify the same dataset file.")
    if file_record.get("status") != "Ready":
        raise ValueError("A successful inspection handoff requires a ready source file.")
    if checksum is not None and not isinstance(checksum, str):
        raise ValueError("The source checksum is invalid.")
    filename = file_record.get("filename")
    if not isinstance(filename, str):
        raise ValueError("The source filename is invalid.")

    profile = profile_response.get("profile_result")
    if not isinstance(profile, dict) or not isinstance(profile.get("tables"), list):
        raise ValueError("A stored profile is required to build an inspection handoff.")
    profile_tables = profile["tables"]
    profile_ref = _reference(
        "profile",
        dataset_id,
        file_id,
        checksum,
        record_key=f"profile-v{profile.get('version')}",
        verification_status="verified",
    )
    quality_report = quality_response.get("quality_report")
    quality_is_current = (
        quality_response.get("report_is_current") is True
        and quality_response.get("status")
        in {"Completed", "PartiallyCompleted"}
        and isinstance(quality_report, dict)
        and isinstance(quality_report.get("analysis"), dict)
        and quality_report["analysis"].get("source_sha256") == checksum
        and isinstance(checksum, str)
    )
    quality_ref = _reference(
        "quality_report",
        dataset_id,
        file_id,
        checksum,
        record_key=(
            quality_report.get("analysis", {}).get("analysis_key")
            if quality_is_current
            else None
        ),
        resolution=(
            "resolved_persisted_record"
            if quality_is_current
            else "stale"
            if quality_report is not None
            else "unavailable"
        ),
        provenance_operation_id=(
            quality_report.get("provenance", {}).get("operation_id")
            if quality_is_current
            and isinstance(quality_report.get("provenance"), dict)
            else None
        ),
        verification_status=_provenance_verification_status(
            quality_report.get("provenance")
            if quality_is_current
            else None
        ),
    )
    schema_ref = _reference(
        "schema_analysis",
        dataset_id,
        file_id,
        checksum,
        record_id=(
            schema_analysis.get("id")
            if isinstance(schema_analysis, dict)
            and isinstance(schema_analysis.get("id"), int)
            and not isinstance(schema_analysis.get("id"), bool)
            else None
        ),
        record_key=(
            schema_analysis.get("analysis_key")
            if isinstance(schema_analysis, dict)
            and isinstance(schema_analysis.get("analysis_key"), str)
            else None
        ),
        resolution=(
            "resolved_persisted_record"
            if isinstance(schema_analysis, dict)
            and schema_analysis.get("stale") is False
            and schema_analysis.get("status") == "Completed"
            and _validate_schema_provenance(schema_analysis)
            else "stale"
            if isinstance(schema_analysis, dict)
            and (
                schema_analysis.get("stale") is True
                or schema_analysis.get("status") == "Stale"
            )
            else "unavailable"
        ),
        provenance_operation_id=(
            schema_analysis.get("provenance", {}).get("operation_id")
            if isinstance(schema_analysis, dict)
            and isinstance(schema_analysis.get("provenance"), dict)
            else None
        ),
        verification_status=_provenance_verification_status(
            schema_analysis.get("provenance")
            if isinstance(schema_analysis, dict)
            else None
        ),
    )

    tables = []
    for table_index, table in enumerate(profile_tables):
        if not isinstance(table, dict):
            raise ValueError("Stored profile contains an invalid table.")
        columns = table.get("columns")
        if not isinstance(columns, list):
            raise ValueError("Stored profile contains invalid columns.")
        inspected_columns = []
        for position, column in enumerate(columns):
            if (
                not isinstance(column, dict)
                or column.get("position") != position
                or not isinstance(column.get("name"), str)
                or not isinstance(column.get("physical_types"), list)
                or any(not isinstance(value, str) for value in column["physical_types"])
                or not isinstance(column.get("missing_value_count"), int)
                or isinstance(column.get("missing_value_count"), bool)
                or column["missing_value_count"] < 0
                or not isinstance(column.get("empty_value_count"), int)
                or isinstance(column.get("empty_value_count"), bool)
                or column["empty_value_count"] < 0
                or column.get("distinct_value_count") is not None
                and (
                    not isinstance(column["distinct_value_count"], int)
                    or isinstance(column["distinct_value_count"], bool)
                    or column["distinct_value_count"] < 0
                )
                or not isinstance(column.get("distinct_count_exact"), bool)
                or column["distinct_count_exact"]
                != (column.get("distinct_value_count") is not None)
            ):
                raise ValueError("Stored profile contains invalid column metadata.")
            inspected_columns.append(
                {
                    "name": column["name"],
                    "position": position,
                    "physical_types": column["physical_types"],
                    "missing_value_count": column["missing_value_count"],
                    "empty_value_count": column["empty_value_count"],
                    "distinct_value_count": column.get("distinct_value_count"),
                    "distinct_count_exact": column.get("distinct_count_exact") is True,
                    "identity_evidence_classification": "OBSERVED",
                    "measurements_evidence_classification": "COMPUTED",
                    "evidence_reference": profile_ref,
                }
            )
        if (
            not isinstance(table.get("row_count"), int)
            or isinstance(table.get("row_count"), bool)
            or not isinstance(table.get("column_count"), int)
            or isinstance(table.get("column_count"), bool)
            or table["column_count"] < 0
            or table["column_count"] != len(columns)
            or not isinstance(table.get("duplicate_row_count_exact"), bool)
            or table["duplicate_row_count"] is not None
            and (
                not isinstance(table["duplicate_row_count"], int)
                or isinstance(table["duplicate_row_count"], bool)
                or table["duplicate_row_count"] < 0
            )
            or table["duplicate_row_count_exact"]
            != (table.get("duplicate_row_count") is not None)
        ):
            raise ValueError("Stored profile contains invalid table counts.")
        tables.append(
            {
                "table_index": table_index,
                "name": table.get("name"),
                "row_count": table["row_count"],
                "column_count": table["column_count"],
                "duplicate_row_count": table.get("duplicate_row_count"),
                "duplicate_row_count_exact": table["duplicate_row_count_exact"],
                "columns": inspected_columns,
                "evidence_classification": "COMPUTED",
                "evidence_reference": profile_ref,
            }
        )

    unresolved = []
    warnings = [
        warning
        for warning in profile.get("warnings", [])
        if isinstance(warning, str)
    ]
    limitations = [
        limitation
        for limitation in profile.get("unsupported_analyses", [])
        if isinstance(limitation, str)
    ]
    if checksum is None:
        unresolved.append("The source file has no persisted checksum.")
    if not quality_is_current:
        unresolved.append("A current completed quality report is unavailable.")
    elif quality_response.get("status") != "Completed":
        unresolved.append("The current quality report did not complete every configured check.")
    elif quality_ref["verification_status"] != "verified":
        unresolved.append("The current quality report's provenance is not verified.")
    if quality_is_current:
        provenance = quality_report.get("provenance")
        verification = (
            provenance.get("verification")
            if isinstance(provenance, dict)
            else None
        )
        if isinstance(verification, dict):
            limitations.extend(
                item
                for item in verification.get("limitations", [])
                if isinstance(item, str)
            )
        coverage = quality_report.get("coverage")
        checks = coverage.get("checks") if isinstance(coverage, dict) else None
        if isinstance(checks, list):
            for check in checks:
                if (
                    isinstance(check, dict)
                    and check.get("status") not in {"Completed", "NotApplicable"}
                ):
                    warnings.append(
                        f"Quality check {check.get('check_id')} status: "
                        f"{check.get('status')}."
                    )
    target_schema_tables, schema_table_map, schema_unresolved = _schema_tables(
        schema_analysis,
        dataset_id,
        file_id,
        profile_tables,
    )
    unresolved.extend(schema_unresolved)
    if (
        isinstance(schema_analysis, dict)
        and schema_ref["resolution"] == "resolved_persisted_record"
    ):
        schema_result = schema_analysis.get("result")
        if isinstance(schema_result, dict):
            warnings.extend(
                warning
                for warning in schema_result.get("warnings", [])
                if isinstance(warning, str)
            )
            limitations.extend(
                limitation
                for limitation in schema_result.get("limitations", [])
                if isinstance(limitation, str)
            )

    candidate_keys = []
    candidate_identifiers = []
    relationships = []
    if target_schema_tables:
        for table in target_schema_tables:
            identity = table["identity"]
            for column in table["columns"]:
                if column.get("candidate_key_status") not in {
                    "candidate",
                    "not_candidate",
                    "not_evaluated",
                }:
                    raise ValueError("Schema analysis returned an unknown key status.")
                candidate_keys.append(
                    {
                        "dataset_id": dataset_id,
                        "file_id": file_id,
                        "table_index": identity["table_index"],
                        "column_name": column["name"],
                        "status": column["candidate_key_status"],
                        "evidence_classification": "COMPUTED",
                        "evidence_reference": schema_ref,
                    }
                )
                if column.get("identifier_like") is True:
                    candidate_identifiers.append(
                        {
                            "dataset_id": dataset_id,
                            "file_id": file_id,
                            "table_index": identity["table_index"],
                            "column_name": column["name"],
                            "status": "heuristic_candidate",
                            "evidence_classification": "INFERRED",
                            "evidence_reference": schema_ref,
                        }
                    )
        schema_result = schema_analysis["result"]
        for relation in schema_result.get("relationships", []):
            if not isinstance(relation, dict):
                raise ValueError("Schema analysis contains an invalid relationship.")
            raw_endpoints = (relation.get("source"), relation.get("target"))
            endpoints = []
            for endpoint in raw_endpoints:
                if not isinstance(endpoint, dict) or not isinstance(endpoint.get("table"), dict):
                    raise ValueError("Schema analysis contains an invalid relationship endpoint.")
                identity = endpoint["table"]
                key = (
                    identity.get("dataset_id"),
                    identity.get("file_id"),
                    identity.get("table_index"),
                )
                known = schema_table_map.get(key)
                position = endpoint.get("position")
                if (
                    known is None
                    or not isinstance(position, int)
                    or isinstance(position, bool)
                    or known["column_names"].get(position) is None
                    or known["column_names"].get(position) != endpoint.get("column")
                ):
                    raise ValueError("Schema analysis references a nonexistent relationship column.")
                endpoints.append(
                    {
                        "dataset_id": key[0],
                        "file_id": key[1],
                        "table_index": key[2],
                        "table_name": known["name"],
                        "column_name": endpoint["column"],
                        "position": position,
                    }
                )
            if not any(endpoint["file_id"] == file_id for endpoint in endpoints):
                continue
            status = relation.get("status")
            if status not in {"inferred", "ambiguous", "rejected"}:
                raise ValueError("Schema analysis returned an unknown relationship status.")
            evidence = relation.get("evidence")
            if not isinstance(evidence, dict):
                raise ValueError("Schema analysis contains invalid relationship evidence.")
            relationships.append(
                {
                    "source": endpoints[0],
                    "target": endpoints[1],
                    "status": status,
                    "cardinality_hypothesis": relation.get(
                        "cardinality_hypothesis",
                    ),
                    "measurements": _scalar_measurements(evidence),
                    "risks": relation.get("risks", []),
                    "evidence_classification": "INFERRED",
                    "evidence_reference": schema_ref,
                }
            )

    quality_findings = []
    if quality_is_current:
        raw_findings = quality_report.get("findings", [])
        if not isinstance(raw_findings, list):
            raise ValueError("Stored quality report contains invalid findings.")
        for finding in raw_findings:
            if not isinstance(finding, dict):
                raise ValueError("Stored quality report contains an invalid finding.")
            if (
                not isinstance(finding.get("confidence"), (int, float))
                or isinstance(finding.get("confidence"), bool)
                or not math.isfinite(finding["confidence"])
                or not 0 <= finding["confidence"] <= 1
            ):
                raise ValueError("Stored quality report contains invalid confidence.")
            scope = finding.get("scope")
            if scope is not None and not isinstance(scope, dict):
                raise ValueError("Stored quality report contains an invalid finding scope.")
            if scope is not None:
                table_index = scope.get("table_index")
                if table_index is not None and (
                    not isinstance(table_index, int)
                    or isinstance(table_index, bool)
                    or not 0 <= table_index < len(profile_tables)
                ):
                    raise ValueError("Quality finding references a nonexistent table.")
                column_position = scope.get("column_position")
                if column_position is not None:
                    if (
                        table_index is None
                        or not isinstance(column_position, int)
                        or isinstance(column_position, bool)
                        or not 0 <= column_position < len(profile_tables[table_index]["columns"])
                    ):
                        raise ValueError("Quality finding references a nonexistent column.")
                    actual_name = profile_tables[table_index]["columns"][column_position]["name"]
                    if scope.get("column_name") != actual_name:
                        raise ValueError("Quality finding column name does not match the profile.")
            evidence = finding.get("evidence")
            measurements = (
                _scalar_measurements(evidence.get("observed"))
                if isinstance(evidence, dict)
                else {}
            )
            quality_findings.append(
                {
                    "finding_id": finding["finding_id"],
                    "rule_id": finding["rule_id"],
                    "classification": finding["classification"],
                    "title": finding["title"],
                    "scope": scope,
                    "measurements": measurements,
                    "confidence": float(finding["confidence"]),
                    "evidence_classification": "COMPUTED",
                    "evidence_reference": quality_ref,
                }
            )

    recommendations = []
    operation_by_id = {
        item.get("operation_id"): item
        for item in preprocessing_operations or []
        if isinstance(item, dict)
    }
    missing_count = sum(
        column["missing_value_count"]
        for table in tables
        for column in table["columns"]
    )
    missing_operation = operation_by_id.get("da.missing_value_handling")
    if (
        missing_count
        and isinstance(missing_operation, dict)
        and missing_operation.get("status") == "implemented"
        and isinstance(missing_operation.get("required_approvals"), list)
        and all(
            isinstance(approval, str)
            for approval in missing_operation["required_approvals"]
        )
    ):
        affected_columns = [
            {
                "table_index": table["table_index"],
                "column_name": column["name"],
                "missing_value_count": column["missing_value_count"],
                "empty_value_count": column["empty_value_count"],
            }
            for table in tables
            for column in table["columns"]
            if column["missing_value_count"] or column["empty_value_count"]
        ]
        consequences = []
        for flag, consequence in (
            ("changes_rows", "May change the number of rows."),
            ("changes_columns", "May change the number of columns."),
            ("changes_values", "May change column values."),
            ("changes_metadata", "May change dataset metadata."),
        ):
            if missing_operation.get(flag) is True:
                consequences.append(consequence)
        recommendations.append(
            {
                "observed_issue": (
                    f"The stored profile observed {missing_count} missing values."
                ),
                "affected_columns": affected_columns,
                "operation_id": missing_operation["operation_id"],
                "operation_status": missing_operation["status"],
                "expected_consequences": consequences,
                "required_approvals": missing_operation["required_approvals"],
                "execution_capability": (
                    f"POST /api/v1/datasets/{dataset_id}/files/{file_id}/preprocess"
                ),
                "follow_up_validation": [
                    "Recompute the profile and quality analysis after an approved execution.",
                    "Confirm the resulting source version and transformation lineage.",
                ],
                "evidence_reference": profile_ref,
            }
        )

    references = [profile_ref, quality_ref]
    if isinstance(schema_analysis, dict):
        references.append(schema_ref)
    handoff_data = {
        "schema_version": 1,
        "handoff_id": "0" * 64,
        "status": "ready" if not unresolved else "partial",
        "dataset_id": dataset_id,
        "file_id": file_id,
        "filename": filename,
        "source_sha256": checksum,
        "source_updated_at": (
            file_record["updated_at"].isoformat()
            if hasattr(file_record.get("updated_at"), "isoformat")
            else file_record.get("updated_at")
        ),
        "inspection_execution_id": None,
        "tables": tables,
        "quality_status": quality_response.get("status", "Unknown"),
        "warnings": list(dict.fromkeys(warnings)),
        "limitations": list(
            dict.fromkeys(
                [
                    *limitations,
                    "No persisted inspection execution ID is available.",
                ]
            )
        ),
        "quality_findings": quality_findings,
        "candidate_keys": candidate_keys,
        "candidate_identifiers": candidate_identifiers,
        "relationships": relationships,
        "preprocessing_recommendations": recommendations,
        "evidence_references": references,
        "unresolved": unresolved,
    }
    validated = InspectionHandoff.model_validate(handoff_data)
    content = validated.model_dump(mode="json", exclude={"handoff_id"})
    content.pop("handoff_id", None)
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    final_data = validated.model_dump(mode="python")
    final_data["handoff_id"] = hashlib.sha256(canonical).hexdigest()
    return InspectionHandoff.model_validate(final_data).model_dump(mode="json")
