from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from training.reference_eda.build_dataset import (
    SPLIT_FILENAMES,
    build_dataset,
)
from training.reference_eda.review_workflow import (
    QUEUE_VERSION,
    ReviewWorkflowError,
    generate_queue,
    import_decisions,
    inspect_record,
    inspect_record,
    rebuild_approved_artifacts,
    review_report,
    validate_approved_artifacts,
    validate_reviews,
)
from training.reference_eda.train_lora import CONFIG_DIR, preflight


def _decision_artifact(
    dataset_dir: Path,
    history_path: Path,
    category: str,
    record_id: str,
    decision: str,
    *,
    comments: str = "Reviewed against source evidence.",
    **updates: Any,
) -> dict[str, Any]:
    record = inspect_record(category, record_id, dataset_dir, history_path)
    entry = {
        **record["review_entry_template"],
        "reviewer_id": "reviewer-01",
        "decision": decision,
        "timestamp_utc": "2026-11-04T00:00:00+00:00",
        "comments": comments,
        **updates,
    }
    return {
        "queue_version": QUEUE_VERSION,
        "record_category": category,
        "decisions": [entry],
    }


def _write_artifact(path: Path, artifact: dict[str, Any]) -> Path:
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


def _first_training_record(dataset_dir: Path) -> dict[str, Any]:
    path = dataset_dir / SPLIT_FILENAMES["train"]
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def _first_evaluation_case(dataset_dir: Path) -> dict[str, Any]:
    suite = json.loads(
        (dataset_dir / "human_review_suite.json").read_text(encoding="utf-8")
    )
    return suite["cases"][0]


def test_queue_and_inspection_keep_training_and_evaluation_separate(
    tmp_path: Path,
) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)

    queue = generate_queue(dataset_dir, history)
    inspected = inspect_record(
        "training", "eda-marketing-qualified-leads-schema", dataset_dir, history
    )
    evaluation_id = _first_evaluation_case(dataset_dir)["id"]
    evaluation = inspect_record("evaluation", evaluation_id, dataset_dir, history)

    assert len(queue["records"]) == 270
    assert sum(item["record_category"] == "training" for item in queue["records"]) == 250
    assert sum(item["record_category"] == "evaluation" for item in queue["records"]) == 20
    assert inspected["source_record"]["messages"]
    assert inspected["scenario"] == "marketing"
    assert evaluation["record_category"] == "evaluation"
    assert evaluation["source_record"]["acceptance_criteria"]
    with pytest.raises(ReviewWorkflowError, match="Unknown evaluation record"):
        inspect_record("evaluation", "unknown-id", dataset_dir, history)
    with pytest.raises(ReviewWorkflowError, match="history must be separate"):
        validate_reviews(dataset_dir, dataset_dir / SPLIT_FILENAMES["train"])


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("approved", "approved"),
        ("rejected", "rejected"),
        ("needs_revision", "needs_revision"),
    ],
)
def test_individual_review_decisions_do_not_change_source(
    tmp_path: Path,
    decision: str,
    expected: str,
) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    source_path = dataset_dir / SPLIT_FILENAMES["train"]
    source_before = source_path.read_bytes()
    artifact = _decision_artifact(
        dataset_dir,
        history,
        "training",
        "eda-marketing-qualified-leads-schema",
        decision,
        comments="Explicit reviewer decision.",
    )
    artifact_path = _write_artifact(tmp_path / "decision.json", artifact)

    assert import_decisions(artifact_path, dataset_dir, history)["imported"] == 1
    assert source_path.read_bytes() == source_before
    report = review_report(dataset_dir, history)
    assert report["status_counts"][expected] == 1
    assert report["status_counts"]["pending"] == 269
    assert validate_reviews(dataset_dir, history)["review_events"] == 1


def test_correction_requires_revision_then_a_separate_approval(
    tmp_path: Path,
) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    original = _first_training_record(dataset_dir)
    corrected = json.loads(json.dumps(original))
    corrected["messages"][-1]["content"] += " Corrected and independently rechecked."
    record_id = original["id"]
    first = _decision_artifact(
        dataset_dir,
        history,
        "training",
        record_id,
        "needs_revision",
        comments="Correct the unsupported final statement.",
        corrected_record=corrected,
        correction_reference="review-ticket-7",
        requested_corrections=["Replace the unsupported final statement."],
    )
    import_decisions(_write_artifact(tmp_path / "revision.json", first), dataset_dir, history)
    assert inspect_record("training", record_id, dataset_dir, history)["status"] == "needs_revision"

    second = _decision_artifact(
        dataset_dir,
        history,
        "training",
        record_id,
        "approved",
        reason_for_change="The corrected answer was reviewed independently.",
    )
    import_decisions(_write_artifact(tmp_path / "approval.json", second), dataset_dir, history)
    latest = inspect_record("training", record_id, dataset_dir, history)
    assert latest["status"] == "approved"
    assert latest["current_content"] == corrected
    assert validate_reviews(dataset_dir, history)["review_events"] == 2


def test_changed_decision_appends_a_reasoned_history_event(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    record_id = _first_training_record(dataset_dir)["id"]
    approved = _decision_artifact(
        dataset_dir, history, "training", record_id, "approved"
    )
    import_decisions(_write_artifact(tmp_path / "approve.json", approved), dataset_dir, history)
    rejected = _decision_artifact(
        dataset_dir,
        history,
        "training",
        record_id,
        "rejected",
        comments="A factual error was found.",
        reason_for_change="The first decision was made before a source calculation was checked.",
        issues=["The stated result conflicts with the fixture."],
    )
    import_decisions(_write_artifact(tmp_path / "reject.json", rejected), dataset_dir, history)

    events = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    assert [event["decision"] for event in events] == ["approved", "rejected"]
    assert events[1]["previous_decision"] == "approved"
    assert events[1]["revision"] == 2
    assert events[1]["reason_for_change"]


@pytest.mark.parametrize(
    ("decision", "updates", "message"),
    [
        ("invalid", {}, "invalid decision"),
        ("approved", {"reviewer_id": ""}, "reviewer_id is required"),
        ("rejected", {"comments": ""}, "requires comments"),
        ("approved", {"timestamp_utc": "2026-11-04T00:00:00"}, "must include a timezone"),
    ],
)
def test_invalid_decision_metadata_is_rejected(
    tmp_path: Path,
    decision: str,
    updates: dict[str, Any],
    message: str,
) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    artifact = _decision_artifact(
        dataset_dir,
        history,
        "training",
        _first_training_record(dataset_dir)["id"],
        decision,
        **updates,
    )
    with pytest.raises(ReviewWorkflowError, match=message):
        import_decisions(_write_artifact(tmp_path / "invalid.json", artifact), dataset_dir, history)


def test_approved_artifacts_include_only_explicitly_approved_records(
    tmp_path: Path,
) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    training_id = "eda-marketing-qualified-leads-schema"
    evaluation_id = _first_evaluation_case(dataset_dir)["id"]
    for category, record_id in (("training", training_id), ("evaluation", evaluation_id)):
        artifact = _decision_artifact(
            dataset_dir, history, category, record_id, "approved"
        )
        import_decisions(
            _write_artifact(tmp_path / f"{category}.json", artifact),
            dataset_dir,
            history,
        )

    output_dir = tmp_path / "approved"
    result = rebuild_approved_artifacts(dataset_dir, history, output_dir)
    assert result["ready"] is False
    training = [
        json.loads(line)
        for line in (output_dir / SPLIT_FILENAMES["train"]).read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    suite = json.loads((output_dir / "human_review_suite.json").read_text(encoding="utf-8"))
    assert [record["id"] for record in training] == [training_id]
    assert all(record["validation_status"] == "approved" for record in training)
    assert [case["id"] for case in suite["cases"]] == [evaluation_id]
    state = validate_approved_artifacts(output_dir, dataset_dir, history)
    assert state["ready"] is False
    assert any("incomplete" in blocker for blocker in state["blockers"])
    with pytest.raises(ReviewWorkflowError, match="must not overwrite"):
        rebuild_approved_artifacts(dataset_dir, history, dataset_dir)


def test_complete_review_history_builds_a_valid_approved_snapshot(
    tmp_path: Path,
) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    queue = generate_queue(dataset_dir, history)
    for category in ("training", "evaluation"):
        entries = []
        for item in queue["records"]:
            if item["record_category"] != category:
                continue
            entries.append(
                {
                    **item["review_entry_template"],
                    "reviewer_id": "test-reviewer",
                    "decision": "approved",
                    "timestamp_utc": "2026-11-04T00:00:00+00:00",
                    "comments": "Approved in an isolated workflow test.",
                }
            )
        artifact = {
            "queue_version": QUEUE_VERSION,
            "record_category": category,
            "decisions": entries,
        }
        import_decisions(
            _write_artifact(tmp_path / f"{category}-all.json", artifact),
            dataset_dir,
            history,
        )

    approved_dir = tmp_path / "approved"
    result = rebuild_approved_artifacts(dataset_dir, history, approved_dir)
    state = validate_approved_artifacts(approved_dir, dataset_dir, history)

    assert result["ready"] is True
    assert result["approved_records"] == 270
    assert state["ready"] is True
    assert state["blockers"] == []
    assert state["manifest"]["approved_training_count"] == 250
    assert state["manifest"]["approved_evaluation_count"] == 20


def test_stale_source_hash_and_duplicate_decisions_are_rejected(
    tmp_path: Path,
) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    artifact = _decision_artifact(
        dataset_dir,
        history,
        "training",
        "eda-marketing-qualified-leads-schema",
        "approved",
    )
    duplicate = json.loads(json.dumps(artifact))
    duplicate["decisions"].append(duplicate["decisions"][0])
    with pytest.raises(ReviewWorkflowError, match="Duplicate decision"):
        import_decisions(_write_artifact(tmp_path / "duplicate.json", duplicate), dataset_dir, history)
    assert not history.exists()

    decision = artifact["decisions"][0]
    decision["timestamp_utc"] = "not-a-date"
    with pytest.raises(ReviewWorkflowError, match="invalid timestamp"):
        import_decisions(_write_artifact(tmp_path / "bad-time.json", artifact), dataset_dir, history)

    artifact = _decision_artifact(
        dataset_dir,
        history,
        "training",
        "eda-marketing-qualified-leads-schema",
        "approved",
    )
    path = dataset_dir / SPLIT_FILENAMES["train"]
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["messages"][-1]["content"] += "Changed after queue export."
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )
    with pytest.raises(ReviewWorkflowError, match="source record changed"):
        import_decisions(_write_artifact(tmp_path / "stale.json", artifact), dataset_dir, history)


def test_source_edit_invalidates_an_existing_approval_without_erasing_history(
    tmp_path: Path,
) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    record_id = _first_training_record(dataset_dir)["id"]
    approved = _decision_artifact(
        dataset_dir, history, "training", record_id, "approved"
    )
    import_decisions(_write_artifact(tmp_path / "approved.json", approved), dataset_dir, history)
    history_before = history.read_bytes()

    source_path = dataset_dir / SPLIT_FILENAMES["train"]
    records = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines()]
    records[0]["messages"][-1]["content"] += " Source revision."
    source_path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )

    report = validate_reviews(dataset_dir, history)
    assert history.read_bytes() == history_before
    assert report["stale_reviews_invalidated"] == [
        {"category": "training", "record_id": record_id}
    ]
    assert inspect_record("training", record_id, dataset_dir, history)["status"] == "pending"


def test_unknown_ids_and_malformed_corrections_are_rejected(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "source"
    history = tmp_path / "review_history.jsonl"
    build_dataset(dataset_dir)
    record_id = _first_training_record(dataset_dir)["id"]
    artifact = _decision_artifact(
        dataset_dir, history, "training", record_id, "approved"
    )
    artifact["decisions"][0]["record_id"] = "eda-marketing-not-a-record-schema"
    with pytest.raises(ReviewWorkflowError, match="Unknown training record ID"):
        import_decisions(_write_artifact(tmp_path / "unknown.json", artifact), dataset_dir, history)

    original = _first_training_record(dataset_dir)
    corrected = json.loads(json.dumps(original))
    corrected["id"] = "eda-marketing-replacement-id"
    artifact = _decision_artifact(
        dataset_dir,
        history,
        "training",
        record_id,
        "needs_revision",
        corrected_record=corrected,
    )
    with pytest.raises(ReviewWorkflowError, match="may not change id"):
        import_decisions(
            _write_artifact(tmp_path / "malformed-correction.json", artifact),
            dataset_dir,
            history,
        )


def test_dataset_rebuild_leaves_separate_review_history_untouched(
    tmp_path: Path,
) -> None:
    history = tmp_path / "review_history.jsonl"
    build_dataset(tmp_path / "source")
    history.write_text('{"audit":"preserve me"}\n', encoding="utf-8")
    contents_before = history.read_bytes()

    build_dataset(tmp_path / "source")

    assert history.read_bytes() == contents_before


def test_rebuild_does_not_preserve_embedded_approval_metadata(
    tmp_path: Path,
) -> None:
    build_dataset(tmp_path)
    train_path = tmp_path / SPLIT_FILENAMES["train"]
    records = [json.loads(line) for line in train_path.read_text(encoding="utf-8").splitlines()]
    records[0]["validation_status"] = "approved"
    records[0]["reviewer_notes"] = "reviewer=someone; reviewed_on=2026-11-04"
    train_path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )
    suite_path = tmp_path / "human_review_suite.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    suite["cases"][0]["validation_status"] = "approved"
    suite["cases"][0]["reviewer_notes"] = "reviewer=someone; reviewed_on=2026-11-04"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")

    build_dataset(tmp_path)

    rebuilt = _first_training_record(tmp_path)
    rebuilt_suite = json.loads(suite_path.read_text(encoding="utf-8"))
    assert rebuilt["validation_status"] == "draft"
    assert "Draft only" in rebuilt["reviewer_notes"]
    assert rebuilt_suite["cases"][0]["validation_status"] == "draft"


def test_fine_tuning_is_blocked_without_authoritative_review_history(
    tmp_path: Path,
) -> None:
    readiness = preflight(CONFIG_DIR / "qwen3-0.6b.json")
    assert readiness["ready"] is False
    assert any("authoritative review workflow" in blocker for blocker in readiness["blockers"])
