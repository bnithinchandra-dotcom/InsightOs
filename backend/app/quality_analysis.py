import hashlib
import json
import math
import re
from datetime import datetime, timezone


QUALITY_REPORT_SCHEMA_VERSION = 1
QUALITY_ANALYZER_VERSION = "1"
QUALITY_RULESET_VERSION = "1"
MAX_QUALITY_REPORT_BYTES = 1024 * 1024
MAX_QUALITY_FINDINGS = 500
MAX_QUALITY_CHECKS = 256
MAX_QUALITY_EXAMPLES_PER_FINDING = 5
MAX_QUALITY_TEXT_LENGTH = 512
REQUIRED_QUALITY_CHECK_IDS = {"quality_rules.enabled"}

QUALITY_ANALYSIS_CONFIGURATION = {
    "batch_size": 8192,
    "candidate_comparisons": 10000,
    "max_examples_per_finding": MAX_QUALITY_EXAMPLES_PER_FINDING,
    "max_findings": MAX_QUALITY_FINDINGS,
    "max_report_bytes": MAX_QUALITY_REPORT_BYTES,
}

_CLASSIFICATIONS = {
    "confirmed_violation",
    "suspected_issue",
    "unusual_candidate",
}
_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_COVERAGE_STATES = {
    "Completed",
    "Skipped",
    "NotApplicable",
    "BudgetExhausted",
    "Failed",
}
_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_TOP_LEVEL_KEYS = {
    "schema_version",
    "analysis",
    "completion_status",
    "summary",
    "coverage",
    "findings",
    "scores",
}
_ANALYSIS_KEYS = {
    "analysis_key",
    "source_sha256",
    "normalized_outputs",
    "analyzer_version",
    "ruleset_version",
    "configuration_sha256",
    "started_at",
    "completed_at",
}
_SUMMARY_KEYS = {
    "tables_available",
    "rows_available",
    "columns_available",
    "findings_total",
    "findings_by_classification",
    "findings_by_severity",
}
_CLASSIFICATION_KEYS = _CLASSIFICATIONS
_SEVERITY_KEYS = _SEVERITIES
_COVERAGE_KEYS = {"status", "checks"}
_CHECK_KEYS = {
    "check_id",
    "status",
    "required",
    "scope",
    "rows_considered",
    "budget",
    "reason",
}
_FINDING_KEYS = {
    "finding_id",
    "rule_id",
    "rule_version",
    "classification",
    "title",
    "description",
    "scope",
    "evidence",
    "denominator",
    "severity",
    "confidence",
    "confidence_basis",
    "recommended_investigation",
}
_SCOPE_KEYS = {
    "table_index",
    "table_name",
    "column_position",
    "column_name",
}


class QualityAnalysisError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _canonical_json(value) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise QualityAnalysisError(
            "invalid_quality_report",
            "Quality analysis produced invalid JSON data.",
        ) from error


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_text(value, *, nonempty: bool = True) -> None:
    if (
        not isinstance(value, str)
        or len(value) > MAX_QUALITY_TEXT_LENGTH
        or (nonempty and not value.strip())
    ):
        raise ValueError("Invalid bounded text.")


def _require_count(value) -> None:
    if not _is_int(value) or value < 0:
        raise ValueError("Invalid count.")


def _require_sha256(value) -> None:
    if not isinstance(value, str) or _HEX_256.fullmatch(value) is None:
        raise ValueError("Invalid SHA-256 value.")


def _require_timestamp(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("Timestamps must be UTC and timezone-aware.")
    return parsed


def normalized_output_provenance(normalization_result: dict) -> list[dict]:
    tables = normalization_result.get("tables")
    if not isinstance(tables, list):
        raise QualityAnalysisError(
            "normalized_output_invalid",
            "Validated normalized output metadata is unavailable.",
        )
    outputs = []
    for index, table in enumerate(tables):
        if (
            not isinstance(table, dict)
            or not _is_int(table.get("table_index"))
            or table.get("table_index") != index
        ):
            raise QualityAnalysisError(
                "normalized_output_invalid",
                "Validated normalized output metadata is unavailable.",
            )
        checksum = table.get("sha256")
        if checksum is not None:
            try:
                _require_sha256(checksum)
            except ValueError as error:
                raise QualityAnalysisError(
                    "normalized_output_invalid",
                    "Validated normalized output metadata is unavailable.",
                ) from error
        outputs.append({"table_index": index, "sha256": checksum})
    return outputs


def normalized_summary_totals(normalization_result: dict) -> dict[str, int]:
    tables = normalization_result.get("tables")
    if not isinstance(tables, list) or not tables:
        raise QualityAnalysisError(
            "normalized_output_invalid",
            "Validated normalized output metadata is unavailable.",
        )
    totals = {
        "tables_available": len(tables),
        "rows_available": 0,
        "columns_available": 0,
    }
    for table in tables:
        if not isinstance(table, dict):
            raise QualityAnalysisError(
                "normalized_output_invalid",
                "Validated normalized output metadata is unavailable.",
            )
        for field, summary_field in (
            ("row_count", "rows_available"),
            ("column_count", "columns_available"),
        ):
            value = table.get(field)
            if not _is_int(value) or value < 0:
                raise QualityAnalysisError(
                    "normalized_output_invalid",
                    "Validated normalized output metadata is unavailable.",
                )
            totals[summary_field] += value
    for field, summary_field in (
        ("row_count", "rows_available"),
        ("column_count", "columns_available"),
    ):
        value = normalization_result.get(field)
        if value is not None:
            if not _is_int(value) or value < 0 or value != totals[summary_field]:
                raise QualityAnalysisError(
                    "normalized_output_invalid",
                    "Normalized aggregate counts are inconsistent.",
                )
    return totals


def quality_analysis_key(
    source_sha256: str,
    normalization_result: dict,
    configuration: dict | None = None,
) -> tuple[str, str]:
    try:
        _require_sha256(source_sha256)
    except ValueError as error:
        raise QualityAnalysisError(
            "source_metadata_invalid",
            "Validated source checksum metadata is unavailable.",
        ) from error
    config = (
        QUALITY_ANALYSIS_CONFIGURATION
        if configuration is None
        else configuration
    )
    if config != QUALITY_ANALYSIS_CONFIGURATION:
        raise QualityAnalysisError(
            "invalid_configuration",
            "Quality analysis configuration is unsupported.",
        )
    outputs = normalized_output_provenance(normalization_result)
    configuration_sha256 = hashlib.sha256(_canonical_json(config)).hexdigest()
    identity = {
        "analyzer_version": QUALITY_ANALYZER_VERSION,
        "configuration_sha256": configuration_sha256,
        "normalized_outputs": outputs,
        "ruleset_version": QUALITY_RULESET_VERSION,
        "schema_version": QUALITY_REPORT_SCHEMA_VERSION,
        "source_sha256": source_sha256,
    }
    analysis_key = hashlib.sha256(_canonical_json(identity)).hexdigest()
    return analysis_key, configuration_sha256


def finding_id_for(analysis_key: str, rule_id: str, rule_version: str, scope: dict) -> str:
    identity = {
        "analysis_key": analysis_key,
        "rule_id": rule_id,
        "rule_version": rule_version,
        "scope": scope,
    }
    return "qf_" + hashlib.sha256(_canonical_json(identity)).hexdigest()[:24]


def build_contract_report(
    source_sha256: str,
    normalization_result: dict,
    analysis_key: str,
    configuration_sha256: str,
    started_at: datetime,
    completed_at: datetime,
) -> dict:
    outputs = normalized_output_provenance(normalization_result)
    report = {
        "schema_version": QUALITY_REPORT_SCHEMA_VERSION,
        "analysis": {
            "analysis_key": analysis_key,
            "source_sha256": source_sha256,
            "normalized_outputs": outputs,
            "analyzer_version": QUALITY_ANALYZER_VERSION,
            "ruleset_version": QUALITY_RULESET_VERSION,
            "configuration_sha256": configuration_sha256,
            "started_at": started_at.astimezone(timezone.utc).isoformat(),
            "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
        },
        "completion_status": "PartiallyCompleted",
        "summary": {
            **normalized_summary_totals(normalization_result),
            "findings_total": 0,
            "findings_by_classification": {
                classification: 0 for classification in sorted(_CLASSIFICATIONS)
            },
            "findings_by_severity": {
                severity: 0 for severity in sorted(_SEVERITIES)
            },
        },
        "coverage": {
            "status": "PartiallyCompleted",
            "checks": [
                {
                    "check_id": "quality_rules.enabled",
                    "status": "Skipped",
                    "required": True,
                    "scope": None,
                    "rows_considered": 0,
                    "budget": None,
                    "reason": (
                        "No quality-detection rules are implemented in this "
                        "contract-only release."
                    ),
                }
            ],
        },
        "findings": [],
        "scores": [],
    }
    return validate_quality_report(
        report,
        expected_summary_totals=normalized_summary_totals(normalization_result),
        expected_source_sha256=source_sha256,
        expected_analysis_key=analysis_key,
        expected_normalized_outputs=outputs,
    )


def _validate_scope(scope) -> None:
    if scope is None:
        return
    if not isinstance(scope, dict) or set(scope) != _SCOPE_KEYS:
        raise ValueError("Invalid scope.")
    for key in ("table_index", "column_position"):
        value = scope[key]
        if value is not None:
            _require_count(value)
    for key in ("table_name", "column_name"):
        value = scope[key]
        if value is not None:
            _require_text(value, nonempty=False)


def _expected_completion_status(checks: list[dict]) -> str:
    checks_by_id = {check["check_id"]: check for check in checks}
    if not REQUIRED_QUALITY_CHECK_IDS <= checks_by_id.keys():
        raise ValueError("Required quality checks are missing.")
    if any(
        checks_by_id[check_id]["required"] is not True
        for check_id in REQUIRED_QUALITY_CHECK_IDS
    ):
        raise ValueError("Required quality checks are incorrectly classified.")
    if any(
        check["required"] and check["check_id"] not in REQUIRED_QUALITY_CHECK_IDS
        for check in checks
    ):
        raise ValueError("Unexpected required quality check.")
    incomplete = any(
        check["status"] in {"BudgetExhausted", "Failed"}
        or check["required"]
        and check["status"] != "Completed"
        for check in checks
    )
    return "PartiallyCompleted" if incomplete else "Completed"


def validate_quality_report(
    report,
    *,
    expected_summary_totals: dict[str, int] | None = None,
    expected_source_sha256: str | None = None,
    expected_analysis_key: str | None = None,
    expected_normalized_outputs: list[dict] | None = None,
) -> dict:
    try:
        if not isinstance(report, dict) or set(report) != _TOP_LEVEL_KEYS:
            raise ValueError("Invalid report fields.")
        if (
            not _is_int(report["schema_version"])
            or report["schema_version"] != QUALITY_REPORT_SCHEMA_VERSION
            or report["completion_status"] not in {"Completed", "PartiallyCompleted"}
            or not isinstance(report["findings"], list)
            or len(report["findings"]) > MAX_QUALITY_FINDINGS
            or report["scores"] != []
        ):
            raise ValueError("Invalid report version or completion metadata.")

        analysis = report["analysis"]
        if not isinstance(analysis, dict) or set(analysis) != _ANALYSIS_KEYS:
            raise ValueError("Invalid analysis provenance.")
        _require_sha256(analysis["analysis_key"])
        _require_sha256(analysis["source_sha256"])
        _require_sha256(analysis["configuration_sha256"])
        for key in ("analyzer_version", "ruleset_version"):
            _require_text(analysis[key])
        if (
            analysis["analyzer_version"] != QUALITY_ANALYZER_VERSION
            or analysis["ruleset_version"] != QUALITY_RULESET_VERSION
        ):
            raise ValueError("Unsupported analyzer or ruleset version.")
        started = _require_timestamp(analysis["started_at"])
        completed = _require_timestamp(analysis["completed_at"])
        if completed < started:
            raise ValueError("Invalid analysis time range.")

        outputs = analysis["normalized_outputs"]
        if not isinstance(outputs, list) or not outputs:
            raise ValueError("Missing normalized output provenance.")
        last_index = -1
        for output in outputs:
            if (
                not isinstance(output, dict)
                or set(output) != {"table_index", "sha256"}
                or not _is_int(output["table_index"])
                or output["table_index"] <= last_index
            ):
                raise ValueError("Invalid normalized output provenance.")
            last_index = output["table_index"]
            if output["sha256"] is not None:
                _require_sha256(output["sha256"])

        expected_key, expected_configuration_sha256 = quality_analysis_key(
            analysis["source_sha256"],
            {
                "tables": [
                    {
                        "table_index": output["table_index"],
                        "sha256": output["sha256"],
                    }
                    for output in outputs
                ]
            },
        )
        if (
            analysis["analysis_key"] != expected_key
            or analysis["configuration_sha256"] != expected_configuration_sha256
        ):
            raise ValueError("Analysis identity does not match its provenance.")

        if expected_source_sha256 is not None:
            _require_sha256(expected_source_sha256)
            if analysis["source_sha256"] != expected_source_sha256:
                raise ValueError("Source provenance mismatch.")
        if (
            expected_analysis_key is not None
            and analysis["analysis_key"] != expected_analysis_key
        ):
            raise ValueError("Analysis key mismatch.")
        if (
            expected_normalized_outputs is not None
            and outputs != expected_normalized_outputs
        ):
            raise ValueError("Normalized output provenance mismatch.")

        summary = report["summary"]
        if not isinstance(summary, dict) or set(summary) != _SUMMARY_KEYS:
            raise ValueError("Invalid summary.")
        for key in ("tables_available", "rows_available", "columns_available"):
            _require_count(summary[key])
        if expected_summary_totals is not None:
            if (
                not isinstance(expected_summary_totals, dict)
                or set(expected_summary_totals)
                != {"tables_available", "rows_available", "columns_available"}
            ):
                raise ValueError("Invalid trusted normalized summary totals.")
            for key, expected_value in expected_summary_totals.items():
                _require_count(expected_value)
                if summary[key] != expected_value:
                    raise ValueError("Summary totals do not match normalized metadata.")
        _require_count(summary["findings_total"])
        classifications = summary["findings_by_classification"]
        severities = summary["findings_by_severity"]
        if (
            not isinstance(classifications, dict)
            or set(classifications) != _CLASSIFICATION_KEYS
            or not isinstance(severities, dict)
            or set(severities) != _SEVERITY_KEYS
        ):
            raise ValueError("Invalid summary categories.")
        for count in (*classifications.values(), *severities.values()):
            _require_count(count)

        coverage = report["coverage"]
        if not isinstance(coverage, dict) or set(coverage) != _COVERAGE_KEYS:
            raise ValueError("Invalid coverage.")
        checks = coverage["checks"]
        if (
            not isinstance(checks, list)
            or len(checks) > MAX_QUALITY_CHECKS
            or len({check.get("check_id") for check in checks if isinstance(check, dict)})
            != len(checks)
        ):
            raise ValueError("Invalid coverage checks.")
        for check in checks:
            if not isinstance(check, dict) or set(check) != _CHECK_KEYS:
                raise ValueError("Invalid coverage check.")
            _require_text(check["check_id"])
            if (
                check["status"] not in _COVERAGE_STATES
                or not isinstance(check["required"], bool)
            ):
                raise ValueError("Invalid coverage status.")
            _validate_scope(check["scope"])
            _require_count(check["rows_considered"])
            if check["budget"] is not None:
                budget = check["budget"]
                if not isinstance(budget, dict) or set(budget) != {"limit", "used"}:
                    raise ValueError("Invalid coverage budget.")
                _require_count(budget["limit"])
                _require_count(budget["used"])
                if budget["used"] > budget["limit"]:
                    raise ValueError("Coverage budget exceeded its limit.")
            if check["reason"] is not None:
                _require_text(check["reason"])
            if check["status"] in {
                "Skipped",
                "NotApplicable",
                "BudgetExhausted",
                "Failed",
            } and not check["reason"]:
                raise ValueError("Coverage reason is required.")
        expected_status = _expected_completion_status(checks)
        if (
            coverage["status"] != expected_status
            or report["completion_status"] != expected_status
        ):
            raise ValueError("Completion status does not match coverage.")

        counts_by_classification = {key: 0 for key in _CLASSIFICATIONS}
        counts_by_severity = {key: 0 for key in _SEVERITIES}
        finding_ids = set()
        for finding in report["findings"]:
            if not isinstance(finding, dict) or set(finding) != _FINDING_KEYS:
                raise ValueError("Invalid finding fields.")
            for key in (
                "finding_id",
                "rule_id",
                "rule_version",
                "title",
                "description",
                "confidence_basis",
                "recommended_investigation",
            ):
                _require_text(finding[key])
            if (
                finding["classification"] not in _CLASSIFICATIONS
                or finding["severity"] not in _SEVERITIES
            ):
                raise ValueError("Invalid finding classification or severity.")
            confidence = finding["confidence"]
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence)
                or confidence < 0
                or confidence > 1
            ):
                raise ValueError("Invalid finding confidence.")
            _validate_scope(finding["scope"])
            evidence = finding["evidence"]
            if (
                not isinstance(evidence, dict)
                or set(evidence) != {"observed", "example_refs"}
                or not isinstance(evidence["observed"], dict)
                or not isinstance(evidence["example_refs"], list)
                or len(evidence["example_refs"]) > MAX_QUALITY_EXAMPLES_PER_FINDING
            ):
                raise ValueError("Invalid finding evidence.")
            for reference in evidence["example_refs"]:
                if (
                    not isinstance(reference, dict)
                    or set(reference) != {"row_index"}
                ):
                    raise ValueError("Invalid example reference.")
                _require_count(reference["row_index"])
            denominator = finding["denominator"]
            if (
                not isinstance(denominator, dict)
                or not {"basis", "count"} <= set(denominator)
                or set(denominator) - {"basis", "count", "reason", "details"}
            ):
                raise ValueError("Invalid finding denominator.")
            _require_text(denominator["basis"])
            if denominator["count"] is None:
                if "reason" not in denominator:
                    raise ValueError("A missing denominator needs a reason.")
                _require_text(denominator["reason"])
            else:
                _require_count(denominator["count"])
                if "reason" in denominator:
                    raise ValueError("A counted denominator cannot include a reason.")
            if "details" in denominator and not isinstance(
                denominator["details"], dict
            ):
                raise ValueError("Invalid denominator details.")
            expected_id = finding_id_for(
                analysis["analysis_key"],
                finding["rule_id"],
                finding["rule_version"],
                finding["scope"],
            )
            if finding["finding_id"] != expected_id or expected_id in finding_ids:
                raise ValueError("Finding ID is invalid or duplicated.")
            finding_ids.add(expected_id)
            counts_by_classification[finding["classification"]] += 1
            counts_by_severity[finding["severity"]] += 1

        if (
            summary["findings_total"] != len(report["findings"])
            or summary["findings_by_classification"] != counts_by_classification
            or summary["findings_by_severity"] != counts_by_severity
        ):
            raise ValueError("Finding counts do not match the summary.")
        serialized = _canonical_json(report)
        if len(serialized) > MAX_QUALITY_REPORT_BYTES:
            raise ValueError("Quality report exceeds the configured size limit.")
        return json.loads(serialized)
    except QualityAnalysisError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise QualityAnalysisError(
            "invalid_quality_report",
            "Quality analysis returned incomplete or inconsistent report metadata.",
        ) from error
