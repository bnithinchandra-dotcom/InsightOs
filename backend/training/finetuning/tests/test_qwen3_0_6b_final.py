import json
import zipfile
from pathlib import Path

from insightos_training.qwen3_0_6b_final.pipeline import (
    CURRENT_FINAL_SHA256,
    STORYTELLING_SHA256,
    FinalPipeline,
    compile_dashboard_layout,
    compile_visual_reconstruction,
    latest_extraction,
    validate_output_contract,
)

EXTRACTION_ROOT = Path.home() / ".cache/insightos/powerbi_reference"
ARCHIVE = Path.home() / ".cache/insightos/training_exports/insightos_qwen3_0_6b_training_bundle.zip"
STORYTELLING_ARCHIVE = (
    Path.home()
    / ".cache/insightos/training_exports/insightos_qwen3_0_6b_storytelling_enriched_bundle.zip"
)
CURRENT_FINAL_ARCHIVE = Path.home() / ".cache/insightos/qwen3_0_6b_final/insightos_qwen3_0_6b_final_training_data.zip"


def test_source_inventory_uses_latest_manifest(tmp_path):
    extraction = latest_extraction(EXTRACTION_ROOT)
    manifest = json.loads(
        (extraction / "manifests/extraction_manifest.json").read_text(encoding="utf-8")
    )
    assert extraction.name == "extraction-20261104T101300Z-70727"
    assert manifest["visual_count"] == 632


def test_final_pipeline_is_fail_closed(tmp_path):
    result = FinalPipeline(EXTRACTION_ROOT, tmp_path, ARCHIVE).build()
    assert result["readiness_status"] == "BLOCKED_INSUFFICIENT_VALID_TARGETS"

    with zipfile.ZipFile(result["archive"]) as bundle:
        names = bundle.namelist()
        assert len(names) == len(set(names))
        manifest_name = next(
            name for name in names if name.endswith("dataset_manifest.json")
        )
        manifest = json.loads(bundle.read(manifest_name))
        assert manifest["approved_training_count"] == 0
        assert manifest["approved_evaluation_count"] == 0
        assert manifest["visual_reconstruction_target_count"] == 182
        assert manifest["dashboard_layout_target_count"] == 42
        assert manifest["chart_selection_complete_target_count"] == 0
        assert manifest["visual_storytelling_complete_target_count"] == 0
        assert manifest["readiness_status"] == "BLOCKED_INSUFFICIENT_VALID_TARGETS"


def test_empty_splits_have_no_leakage(tmp_path):
    result = FinalPipeline(EXTRACTION_ROOT, tmp_path, ARCHIVE).build()
    output = Path(result["output"])
    for split in ("train.jsonl", "validation.jsonl", "evaluation.jsonl"):
        assert (output / "training_data" / split).read_text(encoding="utf-8") == ""


def test_output_contract_rejects_unknown_visualization():
    output = {
        "schema_version": 1,
        "visualization_id": "not_a_real_chart",
        "purpose": "Display evidence-backed values.",
        "data_mappings": {},
        "aggregation": {"references": []},
        "formatting": {},
        "legend": {"visible": False},
        "tooltip": {"enabled": False, "fields": []},
        "layout": {"width": 1, "height": 1},
        "accessibility": {
            "text_alternative": "A text alternative describing the values.",
            "color_mode": "color_with_labels",
            "keyboard_interaction": True,
        },
        "rationale": "This uses a supported fallback rationale for validation.",
    }
    valid, errors = validate_output_contract(output)
    assert not valid
    assert errors


def test_notebook_is_static_json(tmp_path):
    result = FinalPipeline(EXTRACTION_ROOT, tmp_path, ARCHIVE).build()
    notebook_path = Path(result["output"]) / "notebooks/qwen3_0_6b_final_training.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    assert notebook["cells"]


def test_source_archive_hashes_and_lineage(tmp_path):
    import hashlib

    def file_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    assert file_hash(ARCHIVE).startswith("caeb2781")
    assert file_hash(STORYTELLING_ARCHIVE) == STORYTELLING_SHA256
    assert file_hash(CURRENT_FINAL_ARCHIVE) == CURRENT_FINAL_SHA256

    result = FinalPipeline(EXTRACTION_ROOT, tmp_path, ARCHIVE).build()
    lineage = json.loads((Path(result["output"]) / "archive_lineage.json").read_text())
    assert len(lineage["archives"]) == 3
    assert all(entry["verified"] for entry in lineage["archives"])
    assert lineage["647_vs_641"]["reconciled_selection_count"] == 647
    assert lineage["50_vs_42_layouts"]["archive_layout_records"] == 50


def test_storytelling_prompts_preserved_and_not_auto_approved(tmp_path):
    result = FinalPipeline(EXTRACTION_ROOT, tmp_path, ARCHIVE).build()
    output = Path(result["output"])
    prompts = [
        json.loads(line)
        for line in (output / "candidates/storytelling_examples.jsonl").read_text().splitlines()
        if line.strip()
    ]
    packet = json.loads(
        (output / "approval/final_training_approval_packet.json").read_text()
    )
    assert len(prompts) == 7
    assert packet["human_approved"] is False
    assert packet["proposed_train_examples"] == 0
    assert len(packet["candidate_content_hashes"]) == 224


def test_d27_task_specific_compilers_create_complete_pending_targets(tmp_path):
    extraction = latest_extraction(EXTRACTION_ROOT)
    candidate_dir = extraction / "candidates"
    configurations = [
        json.loads(line)
        for line in (candidate_dir / "chart_configuration.jsonl").read_text().splitlines()
        if line.strip()
    ]
    layouts = [
        json.loads(line)
        for line in (candidate_dir / "dashboard_layout.jsonl").read_text().splitlines()
        if line.strip()
    ]

    reconstruction = compile_visual_reconstruction(configurations)
    layout_candidates = compile_dashboard_layout(layouts, configurations)
    assert len(reconstruction) == 182
    assert len(layout_candidates) == 42
    assert all(len(row["messages"]) == 3 for row in reconstruction)
    assert all(
        [message["role"] for message in row["messages"]] == ["system", "user", "assistant"]
        for row in reconstruction
    )
    assert all(row["review_status"] == "pending_review" for row in reconstruction)
    assert all(row["human_approved"] is False for row in reconstruction)
    assert all(row["validation"]["dax_required"] is False for row in reconstruction)
    assert all(
        row["assistant_target"]["schema_version"] == 1
        and row["assistant_target"]["visualization_id"] != "unknown"
        for row in reconstruction
    )
    assert all(
        len(row["messages"]) == 3
        and row["messages"][0]["role"] == "system"
        and row["messages"][1]["role"] == "user"
        and row["messages"][2]["role"] == "assistant"
        for row in layout_candidates
    )


def test_d27_packet_contains_real_pairs_without_auto_approval(tmp_path):
    result = FinalPipeline(EXTRACTION_ROOT, tmp_path, ARCHIVE).build()
    output = Path(result["output"])
    packet = json.loads(
        (output / "approval/final_training_approval_packet.json").read_text()
    )
    assert packet["human_approved"] is False
    assert packet["automatic_approval"] is False
    assert packet["proposed_train_examples"] == 0
    assert packet["visual_reconstruction_complete_targets"] == 182
    assert packet["dashboard_layout_complete_targets"] == 42
    assert packet["chart_selection_complete_targets"] == 0
    assert packet["visual_storytelling_complete_targets"] == 0
    assert len(packet["input_output_pairs"]) == 224
    assert all(
        len(pair["messages"]) == 3 and pair["review_status"] == "pending_review"
        for pair in packet["input_output_pairs"]
    )
    assert packet["source_dataset_manifest_sha256"] is not None
