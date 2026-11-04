import pytest

from insightos_training.exchange import validate_exchange_manifest


def manifest(synthetic=True, approved=False, revision=None, sha256=None):
    return {
        "model": {
            "identifier": "unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
            "revision": revision,
            "revision_status": "not_recorded",
        },
        "dataset": {"synthetic": synthetic, "approved": approved, "sha256": sha256, "hash_status": "not_recorded"},
        "production_ready": False,
    }


def test_synthetic_unknown_metadata_allowed():
    assert validate_exchange_manifest(manifest())["valid"]


def test_synthetic_cannot_be_approved():
    with pytest.raises(ValueError, match="Synthetic"):
        validate_exchange_manifest(manifest(approved=True))


def test_real_missing_hash_rejected():
    with pytest.raises(ValueError, match="Missing dataset hash|real"):
        validate_exchange_manifest(manifest(synthetic=False, approved=True))


def test_exact_model_identity_required():
    item = manifest()
    item["model"]["identifier"] = "Qwen3-0.6B"
    with pytest.raises(ValueError, match="base model"):
        validate_exchange_manifest(item)
