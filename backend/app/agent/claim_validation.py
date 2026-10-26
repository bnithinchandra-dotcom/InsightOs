from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError

from app.agent.inspection_handoff import InspectionHandoff


logger = logging.getLogger(__name__)
MAX_CLAIMS = 32
MAX_CLAIM_RESPONSE_BYTES = 32 * 1024
MAX_HANDOFFS = 10
MAX_CLAIM_KEY_LENGTH = 256


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceReference(StrictModel):
    handoff_id: StrictStr = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)


class ClaimDenominator(StrictModel):
    unit: Literal["rows"]
    count: StrictInt = Field(ge=0)


ClaimValue = (
    StrictStr
    | StrictInt
    | StrictFloat
    | StrictBool
    | list[StrictStr]
    | None
)


class DataClaim(StrictModel):
    claim_type: Literal[
        "column_exists",
        "schema_type",
        "count",
        "quality",
        "relationship",
        "causal",
        "forecast",
        "prediction",
        "anomaly",
        "statistical",
        "preprocessing",
        "recommendation",
        "comparative",
        "metric",
    ]
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)
    claim_key: StrictStr = Field(min_length=1, max_length=MAX_CLAIM_KEY_LENGTH)
    value: ClaimValue
    unit: StrictStr | None = Field(default=None, max_length=64)
    denominator: ClaimDenominator | None = None
    evidence_reference: EvidenceReference


class ClaimEnvelope(StrictModel):
    claims: list[DataClaim] = Field(max_length=MAX_CLAIMS)


@dataclass(frozen=True)
class ResolvedEvidence:
    handoff: dict[str, Any]
    value: Any
    evidence_classification: str
    kind: str
    display: dict[str, Any]
    limitation: str | None = None
    expected_unit: str | None = None
    expected_denominator: ClaimDenominator | None = None


def _handoff_content(handoff: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in handoff.items()
        if key != "handoff_id"
    }


def validate_handoff_identity(value: Any) -> dict[str, Any] | None:
    try:
        handoff = InspectionHandoff.model_validate(value).model_dump(mode="json")
    except (TypeError, ValueError):
        return None
    encoded = json.dumps(
        _handoff_content(handoff),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != handoff["handoff_id"]:
        return None
    return handoff


def _available_handoffs(
    structured_results: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    dataset_id: int | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in structured_results[-MAX_HANDOFFS:]:
        if (
            not isinstance(item, dict)
            or item.get("tool") != "get_inspection_handoff"
            or item.get("status") != "succeeded"
            or not isinstance(item.get("data"), dict)
        ):
            continue
        handoff = validate_handoff_identity(item["data"])
        if handoff is None:
            logger.warning("Registered inspection handoff failed identity validation.")
            continue
        if dataset_id is not None and handoff["dataset_id"] != dataset_id:
            continue
        result[handoff["handoff_id"]] = handoff
    return result


def _resolve_claim_key(
    handoff: dict[str, Any],
    claim: DataClaim,
) -> ResolvedEvidence | None:
    key = claim.claim_key
    if claim.claim_type in {"causal", "forecast", "prediction", "anomaly", "statistical", "comparative", "metric"}:
        return None
    if claim.claim_type == "preprocessing":
        return None

    table_match = re.fullmatch(
        r"table\[(\d+)\]\.(row_count|column_count|duplicate_row_count)",
        key,
    )
    column_match = re.fullmatch(
        r"column\[(\d+),(\d+)\]\.(name|physical_types|missing_value_count|empty_value_count|distinct_value_count)",
        key,
    )
    if table_match:
        if claim.claim_type != "count":
            return None
        profile_reference = next(
            (
                reference
                for reference in handoff["evidence_references"]
                if reference["kind"] == "profile"
            ),
            None,
        )
        if (
            profile_reference is None
            or profile_reference["resolution"] != "resolved_persisted_record"
            or not handoff["source_sha256"]
            or profile_reference["source_sha256"] != handoff["source_sha256"]
        ):
            return None
        table_index = int(table_match.group(1))
        field = table_match.group(2)
        if table_index >= len(handoff["tables"]):
            return None
        table = handoff["tables"][table_index]
        if field == "duplicate_row_count" and not table["duplicate_row_count_exact"]:
            return None
        expected_unit = {
            "row_count": "rows",
            "column_count": "columns",
            "duplicate_row_count": "rows",
        }[field]
        expected_denominator = (
            ClaimDenominator(unit="rows", count=table["row_count"])
            if field == "duplicate_row_count"
            else None
        )
        return ResolvedEvidence(
            handoff=handoff,
            value=table[field],
            evidence_classification="COMPUTED",
            kind="count",
            display={"table": table, "field": field},
            expected_unit=expected_unit,
            expected_denominator=expected_denominator,
        )

    if column_match:
        profile_reference = next(
            (
                reference
                for reference in handoff["evidence_references"]
                if reference["kind"] == "profile"
            ),
            None,
        )
        if (
            profile_reference is None
            or profile_reference["resolution"] != "resolved_persisted_record"
            or not handoff["source_sha256"]
            or profile_reference["source_sha256"] != handoff["source_sha256"]
        ):
            return None
        table_index = int(column_match.group(1))
        column_index = int(column_match.group(2))
        field = column_match.group(3)
        if table_index >= len(handoff["tables"]):
            return None
        table = handoff["tables"][table_index]
        if column_index >= len(table["columns"]):
            return None
        column = table["columns"][column_index]
        if field == "distinct_value_count" and not column["distinct_count_exact"]:
            return None
        if claim.claim_type == "column_exists" and field != "name":
            return None
        if claim.claim_type == "schema_type" and field != "physical_types":
            return None
        if claim.claim_type == "count" and field not in {
            "missing_value_count",
            "empty_value_count",
            "distinct_value_count",
        }:
            return None
        units = {
            "name": None,
            "physical_types": None,
            "missing_value_count": "values",
            "empty_value_count": "values",
            "distinct_value_count": "distinct_values",
        }
        expected_denominator = (
            ClaimDenominator(unit="rows", count=table["row_count"])
            if field in {
                "missing_value_count",
                "empty_value_count",
                "distinct_value_count",
            }
            else None
        )
        return ResolvedEvidence(
            handoff=handoff,
            value=(
                True
                if claim.claim_type == "column_exists"
                else column[field]
            ),
            evidence_classification=(
                "OBSERVED" if field in {"name", "physical_types"} else "COMPUTED"
            ),
            kind=field,
            display={"table": table, "column": column, "field": field},
            expected_unit=units[field],
            expected_denominator=expected_denominator,
        )

    quality_match = re.fullmatch(
        r"quality_findings\[([A-Za-z0-9._:-]{1,128})\]",
        key,
    )
    if quality_match and claim.claim_type == "quality":
        finding_id = quality_match.group(1)
        reference = next(
            (
                reference
                for reference in handoff["evidence_references"]
                if reference["kind"] == "quality_report"
            ),
            None,
        )
        if (
            reference is None
            or reference["resolution"] != "resolved_persisted_record"
            or reference["verification_status"] != "verified"
        ):
            return None
        finding = next(
            (
                item
                for item in handoff["quality_findings"]
                if item["finding_id"] == finding_id
            ),
            None,
        )
        if finding is None:
            return None
        return ResolvedEvidence(
            handoff=handoff,
            value=finding_id,
            evidence_classification="COMPUTED",
            kind="quality",
            display={"finding": finding},
        )

    relationship_match = re.fullmatch(r"relationships\[(\d+)\]\.status", key)
    if relationship_match and claim.claim_type == "relationship":
        index = int(relationship_match.group(1))
        reference = next(
            (
                reference
                for reference in handoff["evidence_references"]
                if reference["kind"] == "schema_analysis"
            ),
            None,
        )
        if (
            reference is None
            or reference["resolution"] != "resolved_persisted_record"
            or reference["verification_status"] != "verified"
        ):
            return None
        if index >= len(handoff["relationships"]):
            return None
        relation = handoff["relationships"][index]
        return ResolvedEvidence(
            handoff=handoff,
            value=relation["status"],
            evidence_classification="INFERRED",
            kind="relationship",
            display={"relationship": relation},
            limitation=(
                "This is an evidence-supported relationship hypothesis, not a confirmed join."
            ),
        )

    recommendation_match = re.fullmatch(
        r"recommendations\[([A-Za-z0-9._:-]{1,128})\]",
        key,
    )
    if recommendation_match and claim.claim_type == "recommendation":
        operation_id = recommendation_match.group(1)
        recommendation = next(
            (
                item
                for item in handoff["preprocessing_recommendations"]
                if item["operation_id"] == operation_id
            ),
            None,
        )
        if recommendation is None:
            return None
        return ResolvedEvidence(
            handoff=handoff,
            value="recommended",
            evidence_classification="INFERRED",
            kind="recommendation",
            display={"recommendation": recommendation},
            limitation="The recommendation has not been executed.",
        )
    return None


def _required_evidence_kind(claim: DataClaim) -> str | None:
    if claim.claim_type in {"count", "column_exists", "schema_type"}:
        return "profile"
    if claim.claim_type == "quality":
        return "quality_report"
    if claim.claim_type == "relationship":
        return "schema_analysis"
    return None


def _value_matches(
    proposed: Any,
    actual: Any,
    *,
    kind: str,
) -> bool:
    if kind == "physical_types":
        return (
            isinstance(proposed, str)
            and proposed in actual
            or isinstance(proposed, list)
            and proposed == actual
        )
    if isinstance(actual, bool):
        return isinstance(proposed, bool) and proposed is actual
    if isinstance(actual, int):
        return isinstance(proposed, int) and not isinstance(proposed, bool) and proposed == actual
    if isinstance(actual, float):
        return (
            isinstance(proposed, (float, int))
            and not isinstance(proposed, bool)
            and math.isfinite(float(proposed))
            and float(proposed) == actual
        )
    return type(proposed) is type(actual) and proposed == actual


def _decision(
    index: int,
    outcome: str,
    reason_code: str,
    *,
    claim_type: str | None = None,
    evidence_reference: EvidenceReference | None = None,
    resolved: ResolvedEvidence | None = None,
) -> dict[str, Any]:
    decision: dict[str, Any] = {
        "claim_index": index,
        "outcome": outcome,
        "reason_code": reason_code,
        "claim_type": claim_type,
        "evidence_references": (
            [evidence_reference.model_dump(mode="json")]
            if evidence_reference is not None
            else []
        ),
    }
    if resolved is not None:
        decision["evidence_classification"] = resolved.evidence_classification
        if resolved.limitation:
            decision["limitation"] = resolved.limitation
    return decision


def _render_supported(decision: dict[str, Any], resolved: ResolvedEvidence) -> str:
    if resolved.kind == "count":
        table = resolved.display["table"]
        field = resolved.display["field"]
        labels = {
            "row_count": "rows",
            "column_count": "columns",
            "duplicate_row_count": "exact duplicate rows",
        }
        return (
            f"Table {table['name'] or table['table_index']} has "
            f"{resolved.value} {labels[field]}."
        )
    if resolved.kind in {
        "name",
        "physical_types",
        "missing_value_count",
        "empty_value_count",
        "distinct_value_count",
    }:
        column = resolved.display["column"]
        table = resolved.display["table"]
        field = resolved.display["field"]
        if field == "name":
            return (
                f"Column {column['name']} exists in table "
                f"{table['name'] or table['table_index']}."
            )
        if field == "physical_types":
            return (
                f"Column {column['name']} in table "
                f"{table['name'] or table['table_index']} has observed physical "
                f"types: {', '.join(column['physical_types']) or 'unknown'}."
            )
        labels = {
            "missing_value_count": "missing values",
            "empty_value_count": "empty values",
            "distinct_value_count": "distinct values",
        }
        value = resolved.value
        return (
            f"Column {column['name']} in table "
            f"{table['name'] or table['table_index']} has {value} "
            f"{labels[field]}."
        )
    if resolved.kind == "quality":
        finding = resolved.display["finding"]
        return (
            f"Current quality analysis recorded: {finding['title']} "
            f"({finding['classification']})."
        )
    if resolved.kind == "relationship":
        relation = resolved.display["relationship"]
        source = relation["source"]
        target = relation["target"]
        return (
            f"Schema analysis found an {relation['status']} relationship hypothesis "
            f"from {source['table_name'] or source['table_index']}."
            f"{source['column_name']} to "
            f"{target['table_name'] or target['table_index']}."
            f"{target['column_name']}; this is not a confirmed join."
        )
    if resolved.kind == "recommendation":
        recommendation = resolved.display["recommendation"]
        approvals = recommendation["required_approvals"]
        approval_text = (
            f" Required approval: {', '.join(approvals)}."
            if approvals
            else ""
        )
        return (
            f"Preprocessing operation {recommendation['operation_id']} is available "
            f"as a recommendation and has not been run.{approval_text}"
        )
    return "The claim was supported by current stored evidence."


def validate_claim_envelope(
    response_text: str,
    *,
    structured_results: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    context: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]], bool]:
    if len(response_text.encode("utf-8")) > MAX_CLAIM_RESPONSE_BYTES:
        return (
            "I couldn't validate the data-specific response because its claim envelope exceeded the safe size limit.",
            [
                {
                    "claim_index": None,
                    "outcome": "FAILED",
                    "reason_code": "claim_envelope_too_large",
                    "evidence_references": [],
                }
            ],
            False,
        )
    try:
        payload = json.loads(response_text)
        envelope = ClaimEnvelope.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return (
            "I couldn't validate a data-specific response against structured claims and current evidence.",
            [
                {
                    "claim_index": None,
                    "outcome": "FAILED",
                    "reason_code": "claim_envelope_invalid",
                    "evidence_references": [],
                }
            ],
            False,
        )

    pinned_dataset_id = (
        context.get("dataset_id")
        if isinstance(context, dict)
        and isinstance(context.get("dataset_id"), int)
        and not isinstance(context.get("dataset_id"), bool)
        else None
    )
    handoffs = _available_handoffs(
        structured_results,
        dataset_id=pinned_dataset_id,
    )
    decisions: list[dict[str, Any]] = []
    rendered: list[str] = []
    all_supported = bool(envelope.claims)
    for index, claim in enumerate(envelope.claims):
        reference = claim.evidence_reference
        if (
            claim.dataset_id != reference.dataset_id
            or claim.file_id != reference.file_id
            or pinned_dataset_id is not None
            and claim.dataset_id != pinned_dataset_id
        ):
            decision = _decision(
                index,
                "UNRESOLVED",
                "evidence_scope_mismatch",
                claim_type=claim.claim_type,
                evidence_reference=reference,
            )
            all_supported = False
            decisions.append(decision)
            continue
        handoff = handoffs.get(reference.handoff_id)
        if (
            handoff is None
            or handoff["dataset_id"] != claim.dataset_id
            or handoff["file_id"] != claim.file_id
        ):
            decision = _decision(
                index,
                "UNRESOLVED",
                "evidence_reference_not_resolved",
                claim_type=claim.claim_type,
                evidence_reference=reference,
            )
            all_supported = False
            decisions.append(decision)
            continue
        required_kind = _required_evidence_kind(claim)
        if required_kind is not None:
            evidence_reference = next(
                (
                    item
                    for item in handoff["evidence_references"]
                    if item["kind"] == required_kind
                ),
                None,
            )
            if evidence_reference is None:
                outcome, reason = "UNRESOLVED", "evidence_reference_not_resolved"
            elif evidence_reference["resolution"] == "stale":
                outcome, reason = "STALE", "evidence_reference_stale"
            elif evidence_reference["resolution"] != "resolved_persisted_record":
                outcome, reason = "UNRESOLVED", "evidence_reference_not_resolved"
            elif evidence_reference["verification_status"].lower() == "failed":
                outcome, reason = "FAILED", "evidence_verification_failed"
            elif evidence_reference["verification_status"].lower() != "verified":
                outcome, reason = "UNRESOLVED", "evidence_not_verified"
            else:
                outcome = reason = None
            if outcome is not None:
                decision = _decision(
                    index,
                    outcome,
                    reason,
                    claim_type=claim.claim_type,
                    evidence_reference=reference,
                )
                all_supported = False
                decisions.append(decision)
                continue
        resolved = _resolve_claim_key(handoff, claim)
        if resolved is None:
            outcome = (
                "UNSUPPORTED"
                if claim.claim_type
                in {
                    "causal",
                    "forecast",
                    "prediction",
                    "anomaly",
                    "statistical",
                    "comparative",
                    "metric",
                    "preprocessing",
                }
                else "UNRESOLVED"
            )
            reason = (
                "claim_type_not_supported"
                if outcome == "UNSUPPORTED"
                else "measurement_not_found"
            )
            decision = _decision(
                index,
                outcome,
                reason,
                claim_type=claim.claim_type,
                evidence_reference=reference,
            )
            all_supported = False
            decisions.append(decision)
            continue
        if not _value_matches(
            claim.value,
            resolved.value,
            kind=resolved.kind,
        ):
            decision = _decision(
                index,
                "CONTRADICTED",
                "proposed_value_mismatch",
                claim_type=claim.claim_type,
                evidence_reference=reference,
                resolved=resolved,
            )
            all_supported = False
            decisions.append(decision)
            continue
        if claim.unit != resolved.expected_unit:
            decision = _decision(
                index,
                "CONTRADICTED",
                "unit_mismatch",
                claim_type=claim.claim_type,
                evidence_reference=reference,
                resolved=resolved,
            )
            all_supported = False
            decisions.append(decision)
            continue
        if claim.denominator != resolved.expected_denominator:
            decision = _decision(
                index,
                "CONTRADICTED",
                "denominator_mismatch",
                claim_type=claim.claim_type,
                evidence_reference=reference,
                resolved=resolved,
            )
            all_supported = False
            decisions.append(decision)
            continue
        outcome = (
            "SUPPORTED_WITH_QUALIFICATION"
            if resolved.limitation
            else "SUPPORTED"
        )
        decision = _decision(
            index,
            outcome,
            "evidence_matched",
            claim_type=claim.claim_type,
            evidence_reference=reference,
            resolved=resolved,
        )
        decision["resolved_value"] = resolved.value
        decisions.append(decision)
        rendered.append(_render_supported(decision, resolved))

    for decision in decisions:
        if decision["outcome"] in {
            "UNSUPPORTED",
            "UNRESOLVED",
            "STALE",
            "CONTRADICTED",
            "FAILED",
        }:
            message = {
                "UNSUPPORTED": (
                    "A requested claim type is not supported by an available deterministic evidence record."
                ),
                "UNRESOLVED": (
                    "I couldn't resolve one or more claims to current persisted evidence."
                ),
                "STALE": "Evidence for one or more claims is stale.",
                "CONTRADICTED": (
                    "A proposed value or scope conflicts with the current stored evidence."
                ),
                "FAILED": (
                    "Evidence verification failed or claim validation could not be completed."
                ),
            }[decision["outcome"]]
            if message not in rendered:
                rendered.append(message)
    if not rendered:
        rendered.append(
            "No data-specific claims were provided in a verifiable form."
        )
    return "\n".join(rendered), decisions, all_supported
