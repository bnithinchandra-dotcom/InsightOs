import hashlib
import json
import zipfile
from pathlib import Path

from insightos_training.training_export.storytelling_enrichment import build_enrichment

ARCHIVE = Path.home() / ".cache/insightos/training_exports/insightos_qwen3_0_6b_training_bundle.zip"
EXTRACTION_ROOT = Path.home() / ".cache/insightos/powerbi_reference"


def test_enriched_bundle_is_additive_and_keep_splits_empty(tmp_path):
    result = build_enrichment(ARCHIVE, EXTRACTION_ROOT, tmp_path)

    assert result["project_count"] == 7
    assert result["configuration_count"] == 609
    assert result["selection_count"] == 647
    with zipfile.ZipFile(result["archive"]) as bundle:
        names = bundle.namelist()
        canonical = json.loads(
            bundle.read(
                "insightos_qwen3_0_6b_storytelling_enriched_bundle/"
                "storytelling_enrichment/canonical_knowledge.json"
            )
        )
        assert canonical["source_authority"] == "first_party_owner_authored"
        assert canonical["status_reconciliation"]["record_ids_reconstructed"] == 11
        assert any(name.endswith("/candidates/chart_selection.jsonl") for name in names)
        for split in ("train.jsonl", "validation.jsonl", "evaluation.jsonl"):
            member = f"insightos_qwen3_0_6b_storytelling_enriched_bundle/training/{split}"
            assert bundle.read(member) == b""


def test_enrichment_preserves_owner_attribution_and_does_not_approve(tmp_path):
    result = build_enrichment(ARCHIVE, EXTRACTION_ROOT, tmp_path)
    with zipfile.ZipFile(result["archive"]) as bundle:
        canonical = json.loads(
            bundle.read(
                "insightos_qwen3_0_6b_storytelling_enriched_bundle/"
                "storytelling_enrichment/canonical_knowledge.json"
            )
        )
        derived = [
            json.loads(line)
            for line in bundle.read(
                "insightos_qwen3_0_6b_storytelling_enriched_bundle/"
                "storytelling_enrichment/derived_training_prompts.jsonl"
            ).decode("utf-8").splitlines()
            if line
        ]

    assert canonical["author_attestation"] == "user_confirmed"
    assert canonical["owner_estimated_accuracy"] == 0.95
    assert canonical["independent_verification"] == "not_established"
    assert len(derived) == 7
    assert all(row["human_review_status"] == "not_approved" for row in derived)
    assert all(row["prompt_classification"] == "derived_with_evidence" for row in derived)


def test_configuration_recoverability_is_complete_without_fabricated_targets(tmp_path):
    result = build_enrichment(ARCHIVE, EXTRACTION_ROOT, tmp_path)
    with zipfile.ZipFile(result["archive"]) as bundle:
        rows = [
            json.loads(line)
            for line in bundle.read(
                "insightos_qwen3_0_6b_storytelling_enriched_bundle/"
                "storytelling_enrichment/configuration_recoverability.jsonl"
            ).decode("utf-8").splitlines()
            if line
        ]

    assert len(rows) == 609
    assert all(row["missing_complete_visual_configuration"] is True for row in rows)
    assert all(
        row["quality_dimensions"]["human_review_status"] == "not_approved"
        for row in rows
    )


def test_enriched_archive_checksum_matches_sidecar(tmp_path):
    result = build_enrichment(ARCHIVE, EXTRACTION_ROOT, tmp_path)
    bundle = Path(result["archive"])
    sidecar = Path(str(bundle) + ".sha256").read_text(encoding="utf-8")

    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert sidecar.split()[0] == result["sha256"] == digest
