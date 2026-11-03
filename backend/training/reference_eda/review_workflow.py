from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from training.reference_eda.build_dataset import DEFAULT_OUTPUT_DIR, SPLIT_FILENAMES


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_HISTORY_PATH = PACKAGE_DIR / "review_history.jsonl"
DECISIONS = {"pending", "approved", "rejected", "needs_revision"}
CATEGORIES = {"training", "evaluation"}
QUEUE_VERSION = "insightos-human-review-queue-v1"
APPROVAL_VERSION = "insightos-approved-dataset-v1"
QUALITY_CHECKLIST = {
    "business_and_analytical_correctness": [
        "Business question and stakeholder context are clear.",
        "Analytical intent, KPI definition, units, denominator, grain, and aggregation are correct.",
        "Comparisons and recommendations follow from supplied evidence without overclaiming.",
    ],
    "tool_and_execution_correctness": [
        "Tool names and arguments match registered contracts.",
        "Dataset/file references are grounded in the conversation or synthetic fixture.",
        "Tool results match the serialized contract and no unexecuted work is described as completed.",
    ],
    "evidence_and_claim_integrity": [
        "Numeric claims are reproducible and traceable to evidence.",
        "Assumptions, unavailable evidence, uncertainty, and limitations are stated.",
        "Unsupported causal, forecast, prediction, statistical, or preprocessing claims are not presented as facts.",
    ],
}
SCENARIO_CHECKLISTS = {
    "marketing": [
        "Compare qualified leads, CPA, ROAS, and stated attribution assumptions; guard zero denominators.",
        "Keep budget recommendations proportional to evidence and consider a controlled creative/channel A/B test.",
    ],
    "maintenance": [
        "Check sensor coverage, rolling/lag feature cutoffs, time-aware failure labels, and prediction horizon.",
        "Distinguish risk from confirmed failure; inspect precision/recall and recommend safe human inspection.",
    ],
    "ecommerce": [
        "Check net revenue, average order value, category share, discounts, refunds, and margin definitions at line/order grain.",
        "Require auditable SQL reconciliation and guard zero-revenue margins.",
    ],
    "fraud": [
        "Check transaction profiling, anomaly evidence, threshold selection, confirmed labels, precision/recall, and false-positive costs.",
        "Keep alerts as investigation signals; do not state suspicious transactions are confirmed fraud.",
    ],
    "healthcare": [
        "Check eligibility and 30-day readmission SQL definitions, follow-up, and planned/unplanned outcomes.",
        "Review Excel/EDA and logistic-regression interpretation for leakage, privacy, uncertainty, and human clinical oversight.",
    ],
}


class ReviewWorkflowError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ReviewWorkflowError(f"Cannot read source file {path}: {error}") from error
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ReviewWorkflowError(f"{path.name}:{line_number}: blank line")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: invalid JSON ({error.msg})"
            ) from error
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ReviewWorkflowError(f"{path.name}:{line_number}: invalid record")
        records.append(record)
    return records


def _load_sources(
    dataset_dir: Path,
) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for split, filename in SPLIT_FILENAMES.items():
        for record in _read_jsonl(dataset_dir / filename):
            key = ("training", record["id"])
            if key in records:
                raise ReviewWorkflowError(f"Duplicate training ID: {record['id']}")
            if record.get("split") != split:
                raise ReviewWorkflowError(
                    f"{record['id']}: split assignment differs from {filename}"
                )
            records[key] = record

    suite_path = dataset_dir / "human_review_suite.json"
    try:
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewWorkflowError(f"Cannot load evaluation suite: {error}") from error
    cases = suite.get("cases") if isinstance(suite, dict) else None
    if not isinstance(cases, list):
        raise ReviewWorkflowError("Evaluation suite must contain a cases list")
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ReviewWorkflowError("Evaluation suite contains an invalid case")
        key = ("evaluation", case["id"])
        if key in records:
            raise ReviewWorkflowError(f"Duplicate evaluation ID: {case['id']}")
        records[key] = case
    return records


def _ensure_history_is_separate(dataset_dir: Path, history_path: Path) -> None:
    source_paths = {
        (dataset_dir / filename).resolve()
        for filename in (
            *SPLIT_FILENAMES.values(),
            "human_review_suite.json",
            "synthetic_fixtures.json",
            "dataset_report.json",
        )
    }
    if history_path.resolve() in source_paths:
        raise ReviewWorkflowError(
            "Review history must be separate from source dataset artifacts"
        )


def _scenario(record_id: str) -> str:
    parts = record_id.split("-")
    if len(parts) < 3:
        raise ReviewWorkflowError(f"Record ID does not encode a scenario: {record_id}")
    return parts[1]


def _read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    history: list[dict[str, Any]] = []
    seen_review_ids: set[str] = set()
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ReviewWorkflowError(f"{path.name}:{line_number}: blank line")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: invalid JSON ({error.msg})"
            ) from error
        if not isinstance(event, dict):
            raise ReviewWorkflowError(f"{path.name}:{line_number}: expected an object")
        required = {
            "review_id",
            "record_id",
            "record_category",
            "scenario",
            "reviewer_id",
            "decision",
            "timestamp_utc",
            "comments",
            "issues",
            "requested_corrections",
            "source_sha256",
            "content_sha256",
            "revision",
            "previous_decision",
            "reason_for_change",
            "corrected_record",
            "correction_reference",
        }
        if set(event) != required:
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: malformed review event fields"
            )
        if (
            not isinstance(event["review_id"], str)
            or not isinstance(event["record_category"], str)
            or not isinstance(event["record_id"], str)
            or not isinstance(event["scenario"], str)
        ):
            raise ReviewWorkflowError(f"{path.name}:{line_number}: invalid record identity")
        key = (event["record_category"], event["record_id"])
        if key[0] not in CATEGORIES:
            raise ReviewWorkflowError(f"{path.name}:{line_number}: invalid record key")
        if event["review_id"] in seen_review_ids:
            raise ReviewWorkflowError(f"{path.name}:{line_number}: duplicate review_id")
        seen_review_ids.add(event["review_id"])
        if not isinstance(event["decision"], str) or event["decision"] not in DECISIONS:
            raise ReviewWorkflowError(f"{path.name}:{line_number}: invalid decision")
        if not isinstance(event["reviewer_id"], str) or not event["reviewer_id"].strip():
            raise ReviewWorkflowError(f"{path.name}:{line_number}: reviewer is required")
        try:
            timestamp = datetime.fromisoformat(
                event["timestamp_utc"].replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: invalid ISO timestamp"
            ) from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: timestamp must include a timezone"
            )
        if not isinstance(event["issues"], list) or not all(
            isinstance(item, str) and item.strip() for item in event["issues"]
        ):
            raise ReviewWorkflowError(f"{path.name}:{line_number}: issues must be strings")
        if not isinstance(event["requested_corrections"], list) or not all(
            isinstance(item, str) and item.strip()
            for item in event["requested_corrections"]
        ):
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: requested_corrections must be strings"
            )
        if event["decision"] != "pending" and not str(event["comments"]).strip():
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: a review decision requires comments"
            )
        if not isinstance(event["comments"], str):
            raise ReviewWorkflowError(f"{path.name}:{line_number}: comments must be text")
        if not isinstance(event["revision"], int) or event["revision"] < 1:
            raise ReviewWorkflowError(f"{path.name}:{line_number}: invalid revision")
        previous = latest.get(key)
        expected_revision = previous["revision"] + 1 if previous else 1
        expected_decision = previous["decision"] if previous else None
        if event["revision"] != expected_revision:
            raise ReviewWorkflowError(f"{path.name}:{line_number}: inconsistent revision")
        if event["previous_decision"] != expected_decision:
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: previous decision does not match history"
            )
        if previous is not None and not str(event["reason_for_change"]).strip():
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: re-review requires a reason"
            )
        if (
            not isinstance(event["source_sha256"], str)
            or len(event["source_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in event["source_sha256"])
        ):
            raise ReviewWorkflowError(f"{path.name}:{line_number}: invalid source hash")
        if (
            not isinstance(event["content_sha256"], str)
            or len(event["content_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in event["content_sha256"])
        ):
            raise ReviewWorkflowError(f"{path.name}:{line_number}: invalid content hash")
        if event["corrected_record"] is not None and not isinstance(
            event["corrected_record"], dict
        ):
            raise ReviewWorkflowError(
                f"{path.name}:{line_number}: corrected_record must be an object"
            )
        history.append(event)
        latest[key] = event
    return history


def _latest_by_key(
    history: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for event in history:
        latest[(event["record_category"], event["record_id"])] = event
    return latest


def _current_state(
    key: tuple[str, str],
    source: dict[str, Any],
    latest: dict[tuple[str, str], dict[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, Any] | None, int]:
    source_hash = _digest(source)
    event = latest.get(key)
    if event is None:
        return "pending", source, None, 0
    revision = event["revision"]
    if event["source_sha256"] != source_hash:
        return "pending", source, event, revision
    content = event["corrected_record"] or source
    if _digest(content) != event["content_sha256"]:
        raise ReviewWorkflowError(
            f"{key[0]} record {key[1]} content hash conflicts with review history"
        )
    return event["decision"], content, event, revision


def _review_packet(
    key: tuple[str, str],
    source: dict[str, Any],
    latest: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    status, content, event, revision = _current_state(key, source, latest)
    split = source.get("split") if key[0] == "training" else "evaluation"
    messages = source.get("messages", [])
    tool_calls = [
        call
        for message in messages
        if isinstance(message, dict)
        for call in message.get("tool_calls", [])
    ]
    tool_results = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            result = json.loads(message.get("content", ""))
        except (json.JSONDecodeError, TypeError):
            result = message.get("content")
        tool_results.append(
            {
                "tool_name": message.get("name"),
                "tool_call_id": message.get("tool_call_id"),
                "result": result,
            }
        )
    try:
        from training.reference_eda.scenario_catalogue import SCENARIOS

        scenario_definition = next(
            item for item in SCENARIOS if item.key == _scenario(key[1])
        )
        scenario_checks = {
            "definitions": scenario_definition.definitions,
            "topic_cautions": [topic.pitfall for topic in scenario_definition.topics],
        }
    except StopIteration:
        scenario_checks = {}
    evaluation_checks = (
        source.get("dataset_profile", {})
        if key[0] == "evaluation"
        else {}
    )
    return {
        "record_id": key[1],
        "record_category": key[0],
        "scenario": _scenario(key[1]),
        "split": split,
        "source_sha256": _digest(source),
        "content_sha256": _digest(content),
        "status": status,
        "revision": revision,
        "source_record": source,
        "current_content": content if content != source else None,
        "inspection_view": {
            "conversation": messages if key[0] == "training" else None,
            "user_request": source.get("prompt")
            if key[0] == "evaluation"
            else next(
                (
                    message.get("content")
                    for message in messages
                    if message.get("role") == "user"
                ),
                None,
            ),
            "analytical_intent": source.get("purpose")
            or source.get("category"),
            "tool_selection_and_arguments": tool_calls,
            "tool_results": tool_results,
            "expected_response": source.get("expected_behavior")
            if key[0] == "evaluation"
            else next(
                (
                    message.get("content")
                    for message in reversed(messages)
                    if message.get("role") == "assistant"
                ),
                None,
            ),
            "evidence_and_provenance": {
                "source": source.get("source"),
                "license": source.get("license"),
                "dataset_profile": evaluation_checks,
            },
            "scenario_specific_checks": {
                **scenario_checks,
                "reviewer_checklist": SCENARIO_CHECKLISTS.get(_scenario(key[1]), []),
            },
            "review_checklist": QUALITY_CHECKLIST,
        },
        "latest_review": event,
        "review_entry_template": {
            "record_id": key[1],
            "record_category": key[0],
            "scenario": _scenario(key[1]),
            "source_sha256": _digest(source),
            "content_sha256": _digest(content),
            "reviewer_id": "",
            "decision": "pending",
            "timestamp_utc": "",
            "comments": "",
            "issues": [],
            "requested_corrections": [],
            "reason_for_change": "",
            "correction_reference": "",
            "corrected_record": None,
        },
    }


def generate_queue(
    dataset_dir: Path = DEFAULT_OUTPUT_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    _ensure_history_is_separate(dataset_dir, history_path)
    sources = _load_sources(dataset_dir)
    latest = _latest_by_key(_read_history(history_path))
    records = [
        _review_packet(key, source, latest)
        for key, source in sorted(sources.items())
        if _current_state(key, source, latest)[0] in {"pending", "needs_revision"}
    ]
    return {
        "queue_version": QUEUE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "instructions": (
            "Review source_record without editing it. Complete review_entry_template "
            "and import it as a decision; source data and annotations are separate."
        ),
        "records": records,
    }


def inspect_record(
    category: str,
    record_id: str,
    dataset_dir: Path = DEFAULT_OUTPUT_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    _ensure_history_is_separate(dataset_dir, history_path)
    if category not in CATEGORIES:
        raise ReviewWorkflowError(f"Unknown record category: {category}")
    sources = _load_sources(dataset_dir)
    key = (category, record_id)
    if key not in sources:
        raise ReviewWorkflowError(f"Unknown {category} record ID: {record_id}")
    latest = _latest_by_key(_read_history(history_path))
    return _review_packet(key, sources[key], latest)


def _validate_corrected_record(
    category: str,
    source: dict[str, Any],
    corrected: dict[str, Any],
) -> None:
    if set(corrected) != set(source):
        raise ReviewWorkflowError("Correction must retain the source record field set")
    immutable = (
        (
            "id",
            "scenario_group",
            "split",
            "category",
            "source",
            "license",
            "validation_status",
            "reviewer_notes",
        )
        if category == "training"
        else ("id", "category", "scorer", "validation_status", "reviewer_notes")
    )
    for field in immutable:
        if corrected.get(field) != source.get(field):
            raise ReviewWorkflowError(f"Correction may not change {field}")
    if category == "training":
        from app.agent.tools import TOOL_DEFINITIONS
        from training.validate_sft_dataset import _validate_example

        tool_models = {
            definition.name: definition.arguments_model
            for definition in TOOL_DEFINITIONS
        }
        tool_schemas = {
            definition.name: definition.provider_schema()
            for definition in TOOL_DEFINITIONS
        }
        _, _, _, errors = _validate_example(
            corrected,
            1,
            tool_models,
            tool_schemas,
            require_approved=False,
        )
        if errors:
            raise ReviewWorkflowError("Invalid training correction: " + "; ".join(errors))
    else:
        for field in (
            "prompt",
            "expected_behavior",
            "acceptance_criteria",
            "rejection_criteria",
            "case_rubric",
            "scoring_rubric",
            "dataset_profile",
        ):
            if field not in corrected:
                raise ReviewWorkflowError(f"Evaluation correction missing {field}")
        if not isinstance(corrected["prompt"], str) or not corrected["prompt"].strip():
            raise ReviewWorkflowError("Evaluation prompt must be non-empty text")
        if not isinstance(corrected["expected_behavior"], str) or not corrected[
            "expected_behavior"
        ].strip():
            raise ReviewWorkflowError("Evaluation expected behavior must be non-empty")
        if not isinstance(corrected["case_rubric"], dict) or sum(
            corrected["case_rubric"].values()
        ) != 100:
            raise ReviewWorkflowError("Evaluation case rubric must total 100 points")


def import_decisions(
    artifact_path: Path,
    dataset_dir: Path = DEFAULT_OUTPUT_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    _ensure_history_is_separate(dataset_dir, history_path)
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewWorkflowError(f"Cannot read review artifact: {error}") from error
    if not isinstance(artifact, dict) or artifact.get("queue_version") != QUEUE_VERSION:
        raise ReviewWorkflowError("Review artifact has an unsupported queue_version")
    category = artifact.get("record_category")
    decisions = artifact.get("decisions")
    if decisions is None and isinstance(artifact.get("records"), list):
        decisions = [
            item.get("review_entry_template")
            for item in artifact["records"]
            if isinstance(item, dict)
        ]
    if (
        not isinstance(category, str)
        or category not in CATEGORIES
        or not isinstance(decisions, list)
        or not decisions
    ):
        raise ReviewWorkflowError("Artifact requires a category and non-empty decisions")
    sources = _load_sources(dataset_dir)
    history = _read_history(history_path)
    latest = _latest_by_key(history)
    seen: set[str] = set()
    pending_events: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ReviewWorkflowError("Each decision must be an object")
        allowed_fields = {
            "record_id",
            "record_category",
            "scenario",
            "source_sha256",
            "content_sha256",
            "reviewer_id",
            "decision",
            "timestamp_utc",
            "comments",
            "issues",
            "requested_corrections",
            "reason_for_change",
            "correction_reference",
            "corrected_record",
        }
        if set(decision) - allowed_fields:
            raise ReviewWorkflowError("Decision contains unsupported fields")
        record_id = decision.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ReviewWorkflowError("Decision requires a stable record_id")
        if record_id in seen:
            raise ReviewWorkflowError(f"Duplicate decision for {record_id}")
        seen.add(record_id)
        key = (category, record_id)
        if key not in sources:
            raise ReviewWorkflowError(f"Unknown {category} record ID: {record_id}")
        source = sources[key]
        source_hash = _digest(source)
        status, content, previous, revision = _current_state(key, source, latest)
        if decision.get("source_sha256") != source_hash:
            raise ReviewWorkflowError(f"{record_id}: source record changed since queue export")
        content_hash = _digest(content)
        if decision.get("content_sha256") != content_hash:
            raise ReviewWorkflowError(f"{record_id}: content changed since queue export")
        if decision.get("record_category") != category:
            raise ReviewWorkflowError(f"{record_id}: record category mismatch")
        if decision.get("scenario") != _scenario(record_id):
            raise ReviewWorkflowError(f"{record_id}: scenario mismatch")
        reviewer = decision.get("reviewer_id")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ReviewWorkflowError(f"{record_id}: reviewer_id is required")
        new_status = decision.get("decision")
        if not isinstance(new_status, str) or new_status not in DECISIONS:
            raise ReviewWorkflowError(f"{record_id}: invalid decision")
        timestamp = decision.get("timestamp_utc")
        try:
            parsed_timestamp = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise ReviewWorkflowError(f"{record_id}: invalid timestamp_utc") from error
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
            raise ReviewWorkflowError(
                f"{record_id}: timestamp_utc must include a timezone"
            )
        comments = decision.get("comments", "")
        if not isinstance(comments, str):
            raise ReviewWorkflowError(f"{record_id}: comments must be text")
        if new_status != "pending" and not comments.strip():
            raise ReviewWorkflowError(f"{record_id}: a review decision requires comments")
        issues = decision.get("issues", [])
        requested = decision.get("requested_corrections", [])
        for name, value in (("issues", issues), ("requested_corrections", requested)):
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise ReviewWorkflowError(f"{record_id}: {name} must be non-empty strings")
        reason = decision.get("reason_for_change", "")
        if previous is not None:
            if not isinstance(reason, str) or not reason.strip():
                raise ReviewWorkflowError(
                    f"{record_id}: reason_for_change is required for re-review"
                )
        corrected = decision.get("corrected_record")
        correction_reference = decision.get("correction_reference", "")
        if correction_reference and not isinstance(correction_reference, str):
            raise ReviewWorkflowError(f"{record_id}: invalid correction_reference")
        effective_record = content
        if corrected is not None:
            if not isinstance(corrected, dict):
                raise ReviewWorkflowError(f"{record_id}: corrected_record must be an object")
            _validate_corrected_record(category, source, corrected)
            if _digest(corrected) != content_hash and new_status == "approved":
                raise ReviewWorkflowError(
                    f"{record_id}: corrected content must receive a separate review "
                    "before approval"
                )
            effective_record = corrected
        if new_status == "approved" and status == "needs_revision" and corrected is None:
            # The prior corrected content is the content under review; it remains
            # attached to this new audit event via effective_record.
            effective_record = content
        if not isinstance(correction_reference, str):
            raise ReviewWorkflowError(f"{record_id}: correction_reference must be text")
        if previous is not None and previous["decision"] != new_status and not reason.strip():
            raise ReviewWorkflowError(f"{record_id}: decision changes require a reason")
        pending_events.append(
            {
                "review_id": uuid.uuid4().hex,
                "record_id": record_id,
                "record_category": category,
                "scenario": _scenario(record_id),
                "reviewer_id": reviewer.strip(),
                "decision": new_status,
                "timestamp_utc": parsed_timestamp.astimezone(timezone.utc).isoformat(),
                "comments": comments,
                "issues": issues,
                "requested_corrections": requested,
                "source_sha256": source_hash,
                "content_sha256": _digest(effective_record),
                "revision": revision + 1,
                "previous_decision": previous["decision"] if previous else None,
                "reason_for_change": reason.strip() if isinstance(reason, str) else "",
                "corrected_record": effective_record if effective_record != source else None,
                "correction_reference": correction_reference,
            }
        )

    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8", newline="\n") as output:
        for event in pending_events:
            output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return {"imported": len(pending_events), "history_path": str(history_path)}


def validate_reviews(
    dataset_dir: Path = DEFAULT_OUTPUT_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    _ensure_history_is_separate(dataset_dir, history_path)
    sources = _load_sources(dataset_dir)
    history = _read_history(history_path)
    latest = _latest_by_key(history)
    unknown = [
        (event["record_category"], event["record_id"])
        for event in history
        if (event["record_category"], event["record_id"]) not in sources
    ]
    if unknown:
        raise ReviewWorkflowError(f"History contains unknown record IDs: {unknown[:5]}")
    stale = []
    for key, event in latest.items():
        if event["source_sha256"] != _digest(sources[key]):
            stale.append({"category": key[0], "record_id": key[1]})
        elif _digest(event["corrected_record"] or sources[key]) != event["content_sha256"]:
            raise ReviewWorkflowError(f"History content integrity failure for {key[1]}")
    return {
        "source_records": len(sources),
        "review_events": len(history),
        "current_status_counts": dict(
            sorted(Counter(_current_state(k, s, latest)[0] for k, s in sources.items()).items())
        ),
        "stale_reviews_invalidated": stale,
        "unknown_records": 0,
    }


def review_report(
    dataset_dir: Path = DEFAULT_OUTPUT_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    _ensure_history_is_separate(dataset_dir, history_path)
    sources = _load_sources(dataset_dir)
    history = _read_history(history_path)
    latest = _latest_by_key(history)
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    by_scenario: dict[str, Counter[str]] = defaultdict(Counter)
    by_split: dict[str, Counter[str]] = defaultdict(Counter)
    outstanding: list[dict[str, Any]] = []
    for key, source in sources.items():
        status, _, _, _ = _current_state(key, source, latest)
        by_category[key[0]][status] += 1
        by_scenario[_scenario(key[1])][status] += 1
        split = source.get("split", "evaluation")
        by_split[split][status] += 1
        if status in {"rejected", "needs_revision"}:
            outstanding.append(
                {
                    "record_id": key[1],
                    "record_category": key[0],
                    "decision": status,
                    "issues": latest[key]["issues"],
                    "requested_corrections": latest[key]["requested_corrections"],
                }
            )
    totals = Counter(_current_state(k, s, latest)[0] for k, s in sources.items())
    return {
        "report_version": "insightos-human-review-report-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_records": len(sources),
        "status_counts": {status: totals[status] for status in sorted(DECISIONS)},
        "progress_by_category": {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(by_category.items())
        },
        "progress_by_scenario": {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(by_scenario.items())
        },
        "progress_by_split": {
            name: dict(sorted(counts.items()))
            for name, counts in sorted(by_split.items())
        },
        "outstanding_quality_issues": outstanding,
        "approval_blockers_by_decision": {
            status: [
                f"{key[0]}:{key[1]}"
                for key, source in sources.items()
                if _current_state(key, source, latest)[0] == status
            ]
            for status in ("pending", "rejected", "needs_revision")
        },
        "history_events": len(history),
    }


def _approval_sources(
    dataset_dir: Path,
    history_path: Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    _ensure_history_is_separate(dataset_dir, history_path)
    sources = _load_sources(dataset_dir)
    latest = _latest_by_key(_read_history(history_path))
    approved: dict[tuple[str, str], dict[str, Any]] = {}
    for key, source in sources.items():
        status, content, event, _ = _current_state(key, source, latest)
        if status == "approved" and event is not None:
            approved[key] = {**content, "validation_status": "approved",
                             "reviewer_notes": (
                                 f"reviewer={event['reviewer_id']}; reviewed_on="
                                 f"{event['timestamp_utc'][:10]}"
                             )}
    return sources, approved


def rebuild_approved_artifacts(
    dataset_dir: Path = DEFAULT_OUTPUT_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    output_dir = output_dir or dataset_dir / "approved_only"
    if output_dir.resolve() == dataset_dir.resolve():
        raise ReviewWorkflowError(
            "Approved-only output must not overwrite the source dataset directory"
        )
    sources, approved = _approval_sources(dataset_dir, history_path)
    for split, filename in SPLIT_FILENAMES.items():
        selected = [
            record
            for (category, _), record in approved.items()
            if category == "training" and record.get("split") == split
        ]
        path = output_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in selected
            ),
            encoding="utf-8",
        )
    suite_path = dataset_dir / "human_review_suite.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite["cases"] = [
        approved[("evaluation", case["id"])]
        for case in suite["cases"]
        if ("evaluation", case["id"]) in approved
    ]
    _write_json(output_dir / "human_review_suite.json", suite)
    fixture_path = dataset_dir / "synthetic_fixtures.json"
    (output_dir / "synthetic_fixtures.json").write_bytes(fixture_path.read_bytes())
    expected_count = len(sources)
    all_approved = len(approved) == expected_count
    manifest = {
        "approval_version": APPROVAL_VERSION,
        "ready": all_approved,
        "source_dataset_dir": str(dataset_dir.resolve()),
        "source_record_count": expected_count,
        "approved_record_count": len(approved),
        "approved_training_count": sum(key[0] == "training" for key in approved),
        "approved_evaluation_count": sum(key[0] == "evaluation" for key in approved),
        "source_file_sha256": {
            name: _file_digest(dataset_dir / name)
            for name in (*SPLIT_FILENAMES.values(), "human_review_suite.json")
        },
        "review_history_sha256": (
            _file_digest(history_path) if history_path.exists() else None
        ),
        "approved_ids": sorted(f"{key[0]}:{key[1]}" for key in approved),
        "output_file_sha256": {
            name: _file_digest(output_dir / name)
            for name in (
                *SPLIT_FILENAMES.values(),
                "human_review_suite.json",
                "synthetic_fixtures.json",
            )
        },
    }
    _write_json(output_dir / "approval_manifest.json", manifest)
    return {
        "ready": all_approved,
        "approved_records": len(approved),
        "total_records": expected_count,
        "output_dir": str(output_dir),
        "pending_or_not_approved": expected_count - len(approved),
    }


def validate_approved_artifacts(
    output_dir: Path,
    dataset_dir: Path = DEFAULT_OUTPUT_DIR,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    try:
        _ensure_history_is_separate(dataset_dir, history_path)
    except ReviewWorkflowError as error:
        return {"ready": False, "blockers": [str(error)]}
    manifest_path = output_dir / "approval_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"ready": False, "blockers": [f"Approval manifest unavailable: {error}"]}
    if not isinstance(manifest, dict):
        return {"ready": False, "blockers": ["Approval manifest must be a JSON object"]}
    blockers: list[str] = []
    if manifest.get("approval_version") != APPROVAL_VERSION or manifest.get("ready") is not True:
        blockers.append("Approved-only artifacts are incomplete")
    source_path = manifest.get("source_dataset_dir")
    if not isinstance(source_path, str) or Path(source_path).resolve() != dataset_dir.resolve():
        blockers.append("Approval manifest references a different source dataset")
    try:
        expected_source_hashes = {
            name: _file_digest(dataset_dir / name)
            for name in (*SPLIT_FILENAMES.values(), "human_review_suite.json")
        }
    except OSError as error:
        return {
            "ready": False,
            "blockers": [f"Cannot read source dataset for approval verification: {error}"],
        }
    if manifest.get("source_file_sha256") != expected_source_hashes:
        blockers.append("Source dataset changed after approved artifacts were built")
    try:
        expected_history_hash = _file_digest(history_path) if history_path.exists() else None
    except OSError as error:
        return {
            "ready": False,
            "blockers": [f"Cannot read review history for approval verification: {error}"],
        }
    if manifest.get("review_history_sha256") != expected_history_hash:
        blockers.append("Review history changed after approved artifacts were built")
    try:
        sources, approved = _approval_sources(dataset_dir, history_path)
        expected_ids = sorted(f"{key[0]}:{key[1]}" for key in approved)
        if manifest.get("approved_ids") != expected_ids:
            blockers.append("Approval manifest IDs differ from current review decisions")
        if len(approved) != len(sources) or manifest.get("approved_record_count") != len(
            approved
        ):
            blockers.append("Every source record must have a current explicit approval")
        for split, filename in SPLIT_FILENAMES.items():
            actual_rows = _read_jsonl(output_dir / filename)
            expected_rows = [
                record
                for (category, _), record in approved.items()
                if category == "training" and record.get("split") == split
            ]
            if actual_rows != expected_rows:
                blockers.append(f"Approved-only split differs from review history: {filename}")
        source_suite = json.loads(
            (dataset_dir / "human_review_suite.json").read_text(encoding="utf-8")
        )
        approved_suite = json.loads(
            (output_dir / "human_review_suite.json").read_text(encoding="utf-8")
        )
        expected_cases = [
            approved[("evaluation", case["id"])]
            for case in source_suite["cases"]
            if ("evaluation", case["id"]) in approved
        ]
        if {
            key: value for key, value in approved_suite.items() if key != "cases"
        } != {key: value for key, value in source_suite.items() if key != "cases"}:
            blockers.append("Approved evaluation suite metadata differs from source")
        if approved_suite.get("cases") != expected_cases:
            blockers.append("Approved evaluation cases differ from review history")
    except (OSError, ReviewWorkflowError, ValueError, json.JSONDecodeError) as error:
        blockers.append(f"Cannot verify current review decisions: {error}")
    output_hashes = manifest.get("output_file_sha256", {})
    if not isinstance(output_hashes, dict):
        blockers.append("Approval manifest output hashes must be an object")
        output_hashes = {}
    for name, expected in output_hashes.items():
        path = output_dir / name
        if not path.is_file() or _file_digest(path) != expected:
            blockers.append(f"Approved artifact integrity check failed: {name}")
    if not blockers:
        from training.reference_eda.validate_dataset import validate_dataset

        try:
            validate_dataset(
                output_dir,
                require_approved=True,
                require_evaluation_review=True,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            blockers.append(f"Approved dataset validation failed: {error}")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "manifest": manifest,
    }


def _write_queue(output_dir: Path, queue: dict[str, Any]) -> None:
    training = [item for item in queue["records"] if item["record_category"] == "training"]
    evaluation = [item for item in queue["records"] if item["record_category"] == "evaluation"]
    for category, records in (("training", training), ("evaluation", evaluation)):
        _write_json(
            output_dir / f"{category}.json",
            {
                "queue_version": QUEUE_VERSION,
                "record_category": category,
                "generated_at_utc": queue["generated_at_utc"],
                "records": records,
            },
        )
    _write_json(
        output_dir / "queue_manifest.json",
        {
            "queue_version": QUEUE_VERSION,
            "training_pending": len(training),
            "evaluation_pending": len(evaluation),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Human review workflow for the reference EDA corpus.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    queue_parser = commands.add_parser("queue", help="Export pending reviewer queues.")
    queue_parser.add_argument("--output-dir", type=Path, default=PACKAGE_DIR / "review_queue")
    inspect_parser = commands.add_parser("inspect", help="Print one source record and review status.")
    inspect_parser.add_argument("--category", choices=sorted(CATEGORIES), required=True)
    inspect_parser.add_argument("--id", required=True)
    import_parser = commands.add_parser("import", help="Import explicit reviewer decisions.")
    import_parser.add_argument("--file", type=Path, required=True)
    commands.add_parser("validate", help="Validate sources and append-only review history.")
    report_parser = commands.add_parser("report", help="Summarize review progress.")
    report_parser.add_argument("--output", type=Path)
    approved_parser = commands.add_parser("approved", help="Build approved-only artifacts.")
    approved_parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "queue":
            queue = generate_queue(args.dataset_dir, args.history)
            _write_queue(args.output_dir, queue)
            result = {
                "training_pending": sum(
                    item["record_category"] == "training" for item in queue["records"]
                ),
                "evaluation_pending": sum(
                    item["record_category"] == "evaluation" for item in queue["records"]
                ),
                "output_dir": str(args.output_dir),
            }
        elif args.command == "inspect":
            result = inspect_record(
                args.category, args.id, args.dataset_dir, args.history
            )
        elif args.command == "import":
            result = import_decisions(args.file, args.dataset_dir, args.history)
        elif args.command == "validate":
            result = validate_reviews(args.dataset_dir, args.history)
        elif args.command == "report":
            result = review_report(args.dataset_dir, args.history)
            if args.output:
                _write_json(args.output, result)
        else:
            result = rebuild_approved_artifacts(
                args.dataset_dir, args.history, args.output_dir
            )
    except (ReviewWorkflowError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Review workflow failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
