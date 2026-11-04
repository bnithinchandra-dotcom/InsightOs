import hashlib
import json
from pathlib import Path

import pytest

import insightos_training.governance as governance_module
from insightos_training.governance import (
    _cleanup_staging,
    export_approved_dataset,
    generate_qwen_chat_prompt,
    load_candidates,
    record_review,
    source_content_hash,
    validate_candidate,
    validate_provenance,
    verify_source_hash,
    write_candidates,
)


def spec(identifier="ranked_bar", measure="revenue"):
    return {
        "schema_version": 1,
        "visualization_id": identifier,
        "purpose": "Compare category performance",
        "data_mappings": {"category": "region", "measure": measure},
        "aggregation": {"references": [{"field": measure, "operation": "sum"}]},
        "formatting": {"number_format": "0.0", "decimal_places": 1},
        "legend": {"visible": True, "position": "bottom"},
        "tooltip": {"enabled": True, "fields": ["region", measure], "show_aggregation": True},
        "layout": {"width": 600, "height": 360, "title": "Regional revenue"},
        "accessibility": {
            "text_alternative": "Region revenue ranking",
            "color_mode": "color_with_labels",
            "keyboard_interaction": True,
        },
        "rationale": "A ranked bar makes category comparison explicit and readable.",
        "fallback": False,
    }


def candidate(example_id="one", split="train", template="template-one", status="draft"):
    source = {"question": example_id}
    output = spec()
    output["rationale"] = example_id + ": ranked category comparison is explicit and readable."
    return {
        "id": example_id,
        "source_dataset_id": "source-" + template,
        "source_record_id": "record-" + example_id,
        "source_content": source,
        "source_content_hash": source_content_hash(source),
        "scenario_group": "regional_performance",
        "template_group": template,
        "visualization_category": "comparison",
        "registry_version": 1,
        "output_schema_version": 1,
        "creation_timestamp": "2026-01-01T00:00:00+00:00",
        "review_status": status,
        "reviewer_identity": "reviewer-1" if status == "approved" else None,
        "approval_timestamp": "2026-01-02T00:00:00+00:00" if status == "approved" else None,
        "business_question": "Which region ranks highest?",
        "available_fields": [{"name": "region", "type": "string"}, {"name": "revenue", "type": "number"}],
        "visualization_profile": {"objective": "comparison", "category_count": 6},
        "output": output,
        "split": split,
    }


def test_provenance_and_validation():
    item = candidate()
    assert validate_provenance(item)["valid"]
    assert validate_candidate(item)["valid"]


def test_hash_mismatch_and_missing_metadata():
    item = candidate()
    item["source_content"]["question"] = "changed"
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_source_hash(item)
    item = candidate()
    item.pop("source_record_id")
    with pytest.raises(ValueError, match="provenance fields"):
        validate_provenance(item)


def test_review_transitions_and_terminal_state(tmp_path):
    store = write_candidates(tmp_path / "candidates.jsonl", [candidate()])
    item = record_review(store, example_id="one", decision="pending_review", reviewer="reviewer")
    assert item["review_status"] == "pending_review"
    item = record_review(store, example_id="one", decision="approved", reviewer="reviewer")
    assert item["review_status"] == "approved"
    with pytest.raises(ValueError, match="approval-state"):
        record_review(store, example_id="one", decision="rejected", reviewer="reviewer")


def test_invalid_registry_mapping_and_negative(tmp_path):
    item = candidate()
    item["output"] = spec("unsupported_chart")
    with pytest.raises(ValueError):
        validate_candidate(item)
    item = candidate()
    item["output"] = spec(measure="invented_profit")
    with pytest.raises(ValueError):
        validate_candidate(item)
    item = candidate()
    item["record_type"] = "negative"
    with pytest.raises(ValueError, match="Negative candidates"):
        validate_candidate(item)


def test_duplicate_ids_and_hashes(tmp_path):
    store = tmp_path / "candidates.jsonl"
    first = candidate("first")
    write_candidates(store, [first])
    store.write_text(store.read_text() + json.dumps(first) + "\n")
    with pytest.raises(ValueError, match="Duplicate candidate example id"):
        load_candidates(store)


def test_prompt_deterministic():
    item = candidate()
    prompt = generate_qwen_chat_prompt(item)
    assert prompt == generate_qwen_chat_prompt(item)
    assert [m["role"] for m in prompt["messages"]] == ["system", "user", "assistant"]
    assert "prompt_template_version" in prompt


def test_zero_approved_and_draft_rejection(tmp_path):
    store = write_candidates(tmp_path / "candidates.jsonl", [candidate(status="draft")])
    with pytest.raises(ValueError, match="zero explicitly approved"):
        export_approved_dataset(store, tmp_path / "export")


def test_successful_export_checksum_and_leakage(tmp_path):
    items = [
        candidate("train", "train", "template-train", "approved"),
        candidate("validation", "validation", "template-validation", "approved"),
        candidate("evaluation", "evaluation", "template-evaluation", "approved"),
    ]
    store = write_candidates(tmp_path / "candidates.jsonl", items)
    destination = tmp_path / "export"
    report = export_approved_dataset(store, destination)
    assert report["approved_only"] and report["example_count"] == 3
    assert report["split_counts"] == {"train": 1, "validation": 1, "evaluation": 1}
    assert (destination / "manifest.json").is_file()
    assert report["manifest_sha256"]


def test_template_leakage(tmp_path):
    items = [candidate("one", "train", "same", "approved"), candidate("two", "validation", "same", "approved")]
    store = write_candidates(tmp_path / "candidates.jsonl", items)
    with pytest.raises(ValueError, match="Template leakage"):
        export_approved_dataset(store, tmp_path / "export")


def test_cleanup_removes_only_current_staging_directory(tmp_path):
    export_parent = tmp_path / "exports"
    export_parent.mkdir()
    staging = export_parent / ".visualization-export-test"
    staging.mkdir()
    (staging / "partial.jsonl").write_text("partial")
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "registry.json"
    marker.write_text("preserve")

    _cleanup_staging(staging, export_parent)
    assert not staging.exists()
    assert marker.read_text() == "preserve"

    _cleanup_staging(outside, export_parent)
    assert outside.exists()
    assert marker.exists()


def test_d1_assets_unchanged_after_failed_export(tmp_path):
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "visualization" / "registry.json",
        root / "visualization" / "corpus" / "train.jsonl",
        root / "visualization" / "corpus" / "validation.jsonl",
        root / "visualization" / "corpus" / "evaluation.jsonl",
    ]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    store = write_candidates(tmp_path / "candidates.jsonl", [candidate(status="draft")])
    with pytest.raises(ValueError, match="zero explicitly approved"):
        export_approved_dataset(store, tmp_path / "export")
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert after == before


def test_failed_approval_does_not_corrupt_candidate_store(tmp_path):
    item = candidate()
    store = write_candidates(tmp_path / "candidates.jsonl", [item])
    original = store.read_bytes()
    item["output"]["visualization_id"] = "unsupported_chart"
    write_candidates(store, [item])
    invalid_store = store.read_bytes()
    with pytest.raises(ValueError):
        record_review(store, example_id="one", decision="pending_review", reviewer="reviewer")
    assert store.read_bytes() == invalid_store
    assert store.read_bytes() != original


def approved_export_candidates():
    return [
        candidate("train-item", "train", "template-train", "approved"),
        candidate("validation-item", "validation", "template-validation", "approved"),
        candidate("evaluation-item", "evaluation", "template-evaluation", "approved"),
    ]


def test_export_failure_immediately_after_staging_creation_cleans_only_staging(tmp_path, monkeypatch):
    store = write_candidates(tmp_path / "candidates.jsonl", approved_export_candidates())
    destination = tmp_path / "exports" / "approved"
    original_generate = governance_module.generate_qwen_chat_prompt

    def fail_after_staging(item):
        raise RuntimeError("injected staging failure")

    monkeypatch.setattr(governance_module, "generate_qwen_chat_prompt", fail_after_staging)
    with pytest.raises(RuntimeError, match="injected staging failure"):
        export_approved_dataset(store, destination)
    monkeypatch.setattr(governance_module, "generate_qwen_chat_prompt", original_generate)

    assert not destination.exists()
    assert list(destination.parent.glob(".visualization-export-*")) == []
    assert store.is_file()


def test_export_file_writing_failure_preserves_registry_and_candidate_store(tmp_path, monkeypatch):
    store = write_candidates(tmp_path / "candidates.jsonl", approved_export_candidates())
    store_before = store.read_bytes()
    root = Path(__file__).resolve().parents[1]
    registry = root / "visualization" / "registry.json"
    registry_before = registry.read_bytes()
    original_write_text = Path.write_text

    def fail_train_write(path, *args, **kwargs):
        if path.name == "train.jsonl":
            raise OSError("injected file write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_train_write)
    with pytest.raises(OSError, match="injected file write failure"):
        export_approved_dataset(store, tmp_path / "exports" / "approved")
    monkeypatch.setattr(Path, "write_text", original_write_text)

    assert not (tmp_path / "exports" / "approved").exists()
    assert list((tmp_path / "exports").glob(".visualization-export-*")) == []
    assert registry.read_bytes() == registry_before
    assert store.read_bytes() == store_before


def test_export_failure_before_atomic_publication_preserves_destination(tmp_path, monkeypatch):
    store = write_candidates(tmp_path / "candidates.jsonl", approved_export_candidates())
    destination = tmp_path / "exports" / "approved"
    original_destination = b"existing-marker"
    destination.parent.mkdir()
    destination.mkdir()
    (destination / "marker.txt").write_bytes(original_destination)
    with pytest.raises(ValueError, match="cannot be overwritten"):
        export_approved_dataset(store, destination)
    assert (destination / "marker.txt").read_bytes() == original_destination


def test_export_rename_failure_cleans_staging(tmp_path, monkeypatch):
    store = write_candidates(tmp_path / "candidates.jsonl", approved_export_candidates())
    destination = tmp_path / "exports" / "approved"

    def fail_rename(source, target):
        raise OSError("injected publication failure")

    monkeypatch.setattr(governance_module.os, "rename", fail_rename)
    with pytest.raises(OSError, match="injected publication failure"):
        export_approved_dataset(store, destination)

    assert not destination.exists()
    assert list(destination.parent.glob(".visualization-export-*")) == []


def test_symlink_outside_staging_root_is_never_deleted(tmp_path):
    staging_parent = tmp_path / "exports"
    staging_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink = staging_parent / ".visualization-export-link"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks unavailable")

    _cleanup_staging(symlink, staging_parent)
    assert outside.exists()
    assert symlink.is_symlink()


def test_successful_export_is_atomic_and_complete(tmp_path):
    store = write_candidates(tmp_path / "candidates.jsonl", approved_export_candidates())
    destination = tmp_path / "exports" / "approved"
    report = export_approved_dataset(store, destination)

    assert report["example_count"] == 3
    assert {p.name for p in destination.iterdir()} == {
        "train.jsonl",
        "validation.jsonl",
        "evaluation.jsonl",
        "manifest.json",
    }
    assert list(destination.parent.glob(".visualization-export-*")) == []
