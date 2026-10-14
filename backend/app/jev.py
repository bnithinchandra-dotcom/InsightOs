"""Bounded internal verification policy; never serialized into public API results."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    InvalidOperation,
)
from typing import Any, Literal

from app.execution import ExecutionBudget, ExecutionBudgetExceeded
from app.provenance import canonical_sha256


JEV_POLICY_VERSION = "1"
MAX_EVIDENCE_BYTES = 16 * 1024
MAX_EVIDENCE_DEPTH = 8
MAX_EVIDENCE_ITEMS = 256
MAX_EVIDENCE_NODES = 4096
_SAFE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ROUNDING_MODES = {
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_HALF_DOWN": ROUND_HALF_DOWN,
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_UP": ROUND_UP,
    "ROUND_FLOOR": ROUND_FLOOR,
    "ROUND_CEILING": ROUND_CEILING,
}

VerificationStatus = Literal["verified", "failed", "stale", "unverifiable"]
VerificationDecision = Literal[
    "agreed",
    "correction_applied",
    "discrepancy_preserved",
    "skipped",
]


@dataclass(frozen=True)
class VerificationPolicy:
    policy_id: str
    value_type: str
    absolute_tolerance: Decimal = Decimal(0)
    relative_tolerance: Decimal = Decimal(0)
    correction_boundary: Decimal = Decimal(0)
    correction_allowed: bool = False
    approved: bool = False
    currency: str | None = None
    precision: int | None = None
    output_contract_id: str | None = None
    rounding_mode: str | None = None


@dataclass(frozen=True)
class VerificationEvidence:
    status: VerificationStatus
    expected_value: Any = None
    reference: str | None = None
    independent: bool = False
    authoritative: bool = False
    evidence_kind: str = "deterministic"
    claim_sha256: str | None = None
    currency: str | None = None
    limitation_code: str | None = None
    correction_output_validated: bool = False
    correction_output_contract_id: str | None = None


@dataclass(frozen=True)
class VerificationAssessment:
    policy_id: str
    status: VerificationStatus
    decision: VerificationDecision
    reason_code: str
    claim_sha256: str | None
    evidence_reference: str | None
    selected_value: Any
    correction_metadata: dict[str, Any] | None = None

    def audit_record(self) -> dict[str, Any]:
        """Return bounded internal metadata without raw claim/evidence values."""
        record = {
            "policy_version": JEV_POLICY_VERSION,
            "policy_id": self.policy_id,
            "status": self.status,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "claim_sha256": self.claim_sha256,
            "evidence_reference": self.evidence_reference,
        }
        if self.correction_metadata is not None:
            record["correction"] = {
                key: value
                for key, value in self.correction_metadata.items()
                if key not in {"original_value", "selected_value"}
            }
        return record


def policy_for(
    value_type: str,
    *,
    currency: str | None = None,
    precision: int | None = None,
    rounding_mode: str | None = None,
) -> VerificationPolicy:
    """Return a conservative, type-specific policy; corrections default off."""
    if not isinstance(value_type, str):
        raise ValueError("Unsupported verification value type.")
    exact_types = {"identifier", "boolean", "count", "categorical", "schema"}
    if value_type in exact_types:
        return VerificationPolicy(
            policy_id=f"{value_type}-exact-v1",
            value_type=value_type,
        )
    if value_type == "floating_point":
        return VerificationPolicy(
            policy_id="floating-point-v1",
            value_type=value_type,
            absolute_tolerance=Decimal("1e-12"),
            relative_tolerance=Decimal("1e-9"),
        )
    if value_type == "percentage":
        return VerificationPolicy(
            policy_id="percentage-unrounded-v1",
            value_type=value_type,
            absolute_tolerance=Decimal("1e-12"),
            relative_tolerance=Decimal("1e-9"),
        )
    if value_type == "statistical_summary":
        return VerificationPolicy(
            policy_id="statistical-summary-v1",
            value_type=value_type,
            absolute_tolerance=Decimal("1e-12"),
            relative_tolerance=Decimal("1e-9"),
        )
    if value_type == "financial":
        if (
            not isinstance(currency, str)
            or re.fullmatch(r"[A-Z]{3}", currency) is None
            or isinstance(precision, bool)
            or not isinstance(precision, int)
            or not 0 <= precision <= 9
            or not isinstance(rounding_mode, str)
            or rounding_mode not in _ROUNDING_MODES
        ):
            raise ValueError(
                "Financial verification requires currency, precision, and rounding."
            )
        return VerificationPolicy(
            policy_id=(
                f"financial-{currency}-{precision}-"
                f"{rounding_mode.removeprefix('ROUND_').lower()}-v1"
            ),
            value_type=value_type,
            absolute_tolerance=Decimal(1).scaleb(-precision) / 2,
            currency=currency,
            precision=precision,
            rounding_mode=rounding_mode,
        )
    if value_type in {"forecast", "semantic_interpretation"}:
        return VerificationPolicy(
            policy_id=f"{value_type}-unverifiable-v1",
            value_type=value_type,
        )
    raise ValueError("Unsupported verification value type.")


def approved_correction_policy(
    *,
    policy_id: str,
    value_type: str,
    absolute_tolerance: Decimal,
    relative_tolerance: Decimal,
    correction_boundary: Decimal,
    currency: str | None = None,
    precision: int | None = None,
    output_contract_id: str,
    rounding_mode: str | None = None,
) -> VerificationPolicy:
    """Create an explicit approved numeric policy; callers own its authority."""
    if value_type not in {
        "financial",
        "floating_point",
        "percentage",
        "statistical_summary",
    }:
        raise ValueError("Automatic correction is unsupported for this value type.")
    policy = VerificationPolicy(
        policy_id=policy_id,
        value_type=value_type,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
        correction_boundary=correction_boundary,
        correction_allowed=True,
        approved=True,
        currency=currency,
        precision=precision,
        output_contract_id=output_contract_id,
        rounding_mode=rounding_mode,
    )
    _validate_policy(policy)
    return policy


def evidence_for(
    primary_value: Any,
    expected_value: Any,
    *,
    reference: str,
    evidence_kind: str = "deterministic",
    independent: bool = True,
    authoritative: bool = True,
    status: VerificationStatus = "verified",
    currency: str | None = None,
    limitation_code: str | None = None,
    correction_output_validated: bool = False,
    correction_output_contract_id: str | None = None,
) -> VerificationEvidence:
    """Bind supplied evidence to a claim; callers must establish its trustworthiness."""
    return VerificationEvidence(
        status=status,
        expected_value=expected_value,
        reference=reference,
        independent=independent,
        authoritative=authoritative,
        evidence_kind=evidence_kind,
        claim_sha256=value_fingerprint(primary_value),
        currency=currency,
        limitation_code=limitation_code,
        correction_output_validated=correction_output_validated,
        correction_output_contract_id=correction_output_contract_id,
    )


def value_fingerprint(value: Any) -> str:
    normalized = _bounded_json_value(value)
    return canonical_sha256(normalized)


def verify_claim(
    primary_value: Any,
    evidence: VerificationEvidence | Any,
    policy: VerificationPolicy,
    *,
    budget: ExecutionBudget | None = None,
) -> VerificationAssessment:
    """Assess a primary value against already-obtained independent evidence.

    This function performs no external calls, model inference, recursive
    verification, or data loading. Unavailable or malformed evidence preserves
    the primary value. Correction requires a separate explicit approved policy.
    """
    _validate_policy(policy)
    if budget is not None:
        try:
            budget.consume("jev_checks", 1)
        except ExecutionBudgetExceeded:
            return _assessment(
                policy,
                "unverifiable",
                "skipped",
                "budget_exhausted",
                None,
                None,
                primary_value,
            )

    claim_hash = None
    try:
        claim_hash = value_fingerprint(primary_value)
    except (TypeError, ValueError, OverflowError):
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "malformed_primary_value",
            claim_hash,
            None,
            primary_value,
        )

    if not isinstance(evidence, VerificationEvidence):
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "malformed_evidence",
            claim_hash,
            None,
            primary_value,
        )
    reference = evidence.reference if _valid_reference(evidence.reference) else None
    try:
        evidence_size = len(
            json.dumps(
                _bounded_json_value(evidence.expected_value),
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError):
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "malformed_evidence",
            claim_hash,
            reference,
            primary_value,
        )
    if evidence_size > MAX_EVIDENCE_BYTES:
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "evidence_size_limit",
            claim_hash,
            reference,
            primary_value,
        )
    if budget is not None:
        try:
            budget.consume("jev_evidence_bytes", evidence_size)
        except ExecutionBudgetExceeded:
            return _assessment(
                policy,
                "unverifiable",
                "skipped",
                "budget_exhausted",
                claim_hash,
                reference,
                primary_value,
            )
    if not _valid_reference(evidence.reference):
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "malformed_evidence_reference",
            claim_hash,
            None,
            primary_value,
        )
    if evidence.claim_sha256 != claim_hash:
        return _assessment(
            policy,
            "stale",
            "skipped",
            "evidence_claim_mismatch",
            claim_hash,
            reference,
            primary_value,
        )
    if not isinstance(evidence.status, str) or evidence.status != "verified":
        status = evidence.status if isinstance(evidence.status, str) and evidence.status in {
            "failed",
            "stale",
            "unverifiable",
        } else "unverifiable"
        return _assessment(
            policy,
            status,
            "skipped",
            _safe_limitation_code(evidence.limitation_code),
            claim_hash,
            reference,
            primary_value,
        )
    if policy.value_type in {"forecast", "semantic_interpretation"}:
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "no_approved_independent_oracle",
            claim_hash,
            reference,
            primary_value,
        )
    if (
        not isinstance(evidence.independent, bool)
        or not evidence.independent
        or not isinstance(evidence.authoritative, bool)
        or not evidence.authoritative
        or not _valid_evidence_kind(evidence.evidence_kind)
    ):
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "independent_authoritative_evidence_unavailable",
            claim_hash,
            reference,
            primary_value,
        )
    if policy.value_type == "financial" and evidence.currency != policy.currency:
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "financial_currency_mismatch",
            claim_hash,
            reference,
            primary_value,
        )
    if not _valid_value(primary_value, policy):
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "value_type_validation_failed",
            claim_hash,
            reference,
            primary_value,
        )
    expected_value = evidence.expected_value
    if policy.value_type == "financial":
        if (
            not isinstance(expected_value, Decimal)
            or not expected_value.is_finite()
            or len(expected_value.as_tuple().digits) > 128
        ):
            return _assessment(
                policy,
                "unverifiable",
                "skipped",
                "value_type_validation_failed",
                claim_hash,
                reference,
                primary_value,
            )
        try:
            expected_value = expected_value.quantize(
                Decimal(1).scaleb(-policy.precision),
                rounding=_ROUNDING_MODES[policy.rounding_mode],
            )
        except (InvalidOperation, TypeError, ValueError):
            return _assessment(
                policy,
                "unverifiable",
                "skipped",
                "value_type_validation_failed",
                claim_hash,
                reference,
                primary_value,
            )
    elif not _valid_value(expected_value, policy):
        return _assessment(
            policy,
            "unverifiable",
            "skipped",
            "value_type_validation_failed",
            claim_hash,
            reference,
            primary_value,
        )

    if policy.value_type in {"identifier", "boolean", "count", "categorical", "schema"}:
        agrees = _strictly_equal(primary_value, expected_value)
        difference = Decimal(0) if agrees else None
    else:
        primary_number = _decimal_value(primary_value)
        expected_number = _decimal_value(expected_value)
        difference = abs(primary_number - expected_number)
        tolerance = max(
            policy.absolute_tolerance,
            policy.relative_tolerance * abs(expected_number),
        )
        agrees = difference <= tolerance

    if agrees:
        return _assessment(
            policy,
            "verified",
            "agreed",
            "within_policy_tolerance",
            claim_hash,
            reference,
            primary_value,
        )
    if (
        policy.correction_allowed
        and policy.approved
        and difference is not None
        and difference <= policy.correction_boundary
        and evidence.correction_output_validated is True
        and evidence.correction_output_contract_id
        == policy.output_contract_id
        and _valid_value(expected_value, policy)
    ):
        return _assessment(
            policy,
            "verified",
            "correction_applied",
            "approved_correction_boundary",
            claim_hash,
            reference,
            expected_value,
            {
                "policy_id": policy.policy_id,
                "policy_version": JEV_POLICY_VERSION,
                "reason_code": "approved_correction_boundary",
                "evidence_reference": reference,
                "original_value": primary_value,
                "selected_value": expected_value,
            },
        )
    return _assessment(
        policy,
        "failed",
        "discrepancy_preserved",
        "outside_agreement_or_correction_boundary",
        claim_hash,
        reference,
        primary_value,
    )


def _assessment(
    policy: VerificationPolicy,
    status: VerificationStatus,
    decision: VerificationDecision,
    reason_code: str,
    claim_sha256: str | None,
    evidence_reference: str | None,
    selected_value: Any,
    correction_metadata: dict[str, Any] | None = None,
) -> VerificationAssessment:
    return VerificationAssessment(
        policy_id=policy.policy_id,
        status=status,
        decision=decision,
        reason_code=reason_code,
        claim_sha256=claim_sha256,
        evidence_reference=evidence_reference,
        selected_value=selected_value,
        correction_metadata=correction_metadata,
    )


def _validate_policy(policy: VerificationPolicy) -> None:
    if (
        not isinstance(policy, VerificationPolicy)
        or not _valid_reference(policy.policy_id)
        or not isinstance(policy.value_type, str)
        or not policy.value_type
        or not _valid_tolerance(policy.absolute_tolerance)
        or not _valid_tolerance(policy.relative_tolerance)
        or not _valid_tolerance(policy.correction_boundary)
        or not isinstance(policy.correction_allowed, bool)
        or not isinstance(policy.approved, bool)
        or (
            policy.correction_allowed
            and (
                not policy.approved
                or not _valid_reference(policy.output_contract_id)
                or policy.value_type
                not in {
                    "financial",
                    "floating_point",
                    "percentage",
                    "statistical_summary",
                }
            )
        )
        or (
            policy.value_type == "financial"
            and (
                not isinstance(policy.currency, str)
                or re.fullmatch(r"[A-Z]{3}", policy.currency) is None
                or isinstance(policy.precision, bool)
                or not isinstance(policy.precision, int)
                or not 0 <= policy.precision <= 9
                or not isinstance(policy.rounding_mode, str)
                or policy.rounding_mode not in _ROUNDING_MODES
            )
        )
    ):
        raise ValueError("JEV verification policy is invalid.")


def _valid_tolerance(value: Any) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and value >= 0
        and -128 <= value.adjusted() <= 12
        and abs(value.as_tuple().exponent) <= 128
        and len(value.as_tuple().digits) <= 32
    )


def _valid_value(value: Any, policy: VerificationPolicy) -> bool:
    kind = policy.value_type
    if kind == "boolean":
        return isinstance(value, bool)
    if kind in {"identifier", "count"}:
        if kind == "count":
            return (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            )
        return (
            isinstance(value, (str, int))
            and not isinstance(value, bool)
            and _bounded_value_ok(value)
        )
    if kind == "categorical":
        return isinstance(value, (str, int, bool, type(None))) and _bounded_value_ok(
            value
        )
    if kind == "schema":
        return isinstance(value, (Mapping, list, tuple)) and _bounded_value_ok(value)
    if kind == "financial":
        if not isinstance(value, Decimal) or not value.is_finite():
            return False
        quantizer = Decimal(1).scaleb(-policy.precision)
        try:
            return value.quantize(quantizer) == value
        except InvalidOperation:
            return False
    if kind in {
        "floating_point",
        "percentage",
        "statistical_summary",
    }:
        return (
            isinstance(value, (int, float, Decimal))
            and not isinstance(value, bool)
            and _finite_number(value)
            and _bounded_value_ok(value)
        )
    return False


def _finite_number(value: int | float | Decimal) -> bool:
    if isinstance(value, Decimal):
        return value.is_finite()
    if isinstance(value, float):
        return math.isfinite(value)
    return True


def _decimal_value(value: int | float | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def _bounded_value_ok(value: Any) -> bool:
    try:
        _bounded_json_value(value)
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def _bounded_json_value(
    value: Any,
    depth: int = 0,
    budget: list[int] | None = None,
) -> Any:
    if budget is None:
        budget = [0, 0]
    if depth > MAX_EVIDENCE_DEPTH:
        raise ValueError("Verification evidence is too deeply nested.")
    budget[0] += 1
    if budget[0] > MAX_EVIDENCE_NODES:
        raise ValueError("Verification evidence contains too many values.")
    if value is None or isinstance(value, bool):
        _charge_serialization_budget(budget, 5)
        return value
    if isinstance(value, int):
        if len(str(value)) > 128:
            raise ValueError("Verification integer is too large.")
        _charge_serialization_budget(budget, len(str(value)) + 1)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Verification value is non-finite.")
        _charge_serialization_budget(budget, 32)
        return value
    if isinstance(value, Decimal):
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 128
            or abs(value.as_tuple().exponent) > 128
            or not -128 <= value.adjusted() <= 128
        ):
            raise ValueError("Verification decimal is invalid.")
        text = format(value, "f")
        _charge_serialization_budget(budget, len(text) + 15)
        return {"$decimal": text}
    if isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("Verification text exceeds its limit.")
        encoded_size = len(value.encode("utf-8"))
        escaped_size = sum(
            5 if ord(character) < 0x20 else
            1 if character in {'"', "\\"} else
            0
            for character in value
        )
        _charge_serialization_budget(budget, encoded_size + escaped_size + 2)
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_EVIDENCE_ITEMS:
            raise ValueError("Verification mapping exceeds its limit.")
        _charge_serialization_budget(budget, 2 + max(0, len(value) - 1))
        normalized = {}
        for key, nested in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise ValueError("Verification mapping key is invalid.")
            _charge_serialization_budget(
                budget,
                len(key.encode("utf-8")) + 3,
            )
            normalized[key] = _bounded_json_value(nested, depth + 1, budget)
        return normalized
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        if len(value) > MAX_EVIDENCE_ITEMS:
            raise ValueError("Verification sequence exceeds its limit.")
        _charge_serialization_budget(budget, 2 + max(0, len(value) - 1))
        return [
            _bounded_json_value(item, depth + 1, budget) for item in value
        ]
    raise TypeError("Verification value is not safely serializable.")


def _strictly_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return (
            left.keys() == right.keys()
            and all(_strictly_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _strictly_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


def _charge_serialization_budget(budget: list[int], estimated_bytes: int) -> None:
    budget[1] += estimated_bytes
    if budget[1] > MAX_EVIDENCE_BYTES:
        raise ValueError("Verification evidence exceeds its serialization limit.")


def _valid_reference(value: Any) -> bool:
    return isinstance(value, str) and _SAFE_REFERENCE.fullmatch(value) is not None


def _valid_evidence_kind(value: Any) -> bool:
    return isinstance(value, str) and _valid_reference(value)


def _safe_limitation_code(value: Any) -> str:
    if isinstance(value, str) and value in {
        "verifier_timeout",
        "verifier_error",
        "budget_exhausted",
        "independent_evidence_unavailable",
        "malformed_evidence",
        "unsupported_claim",
    }:
        return value
    return "verification_unavailable"
