# Qwen3 0.6B Final Visualization Specialist Preparation

This namespace implements the blocked human-review and training-data preparation pipeline for the
`unsloth/Qwen3-0.6B-unsloth-bnb-4bit` visualization specialist.

## What it does

- Identifies the latest PBIX extraction from extraction manifests.
- Preserves first-party source authority and original source hashes.
- Converts recoverable PBIX layout and visual metadata into canonical knowledge records.
- Keeps complete visualization targets separate from incomplete metadata.
- Generates empty fail-closed training splits.
- Rejects unsupported positive visualization targets.
- Creates one consolidated review package and exact candidate content hashes.
- Creates a fail-closed Kaggle notebook for later approved execution.
- Packages a portable training-data archive without the existing smoke adapter.

## Build

```bash
uv run python -m insightos_training.qwen3_0_6b_final
```

The default source is the current D2.5.1 bundle and the latest timestamped Power BI extraction.
The exact declared base bundle SHA-256 is:

```text
caeb27815f7bb27c1dca502ec5e4a7c53cdc9081a1ffd4310a69a45a695d33a6
```

## Approval

No records are automatically approved. A consolidated approval must reference exact candidate
content hashes and the final training-data archive SHA-256. Any source or archive change invalidates
the approval.

## Training status

The current status is always `TRAINING_NOT_STARTED_APPROVAL_REQUIRED` until explicit approval,
independent evaluation records, complete targets, and leakage-safe grouped splits are present.

## D2.7 task-specific evidence

The D2.7 compiler uses independent contracts:

- PBIX native visual projections and mappings produce visual reconstruction pairs.
- Directly observed PBIX page dimensions and visual geometry produce dashboard layout pairs.
- Chart selection and full configuration outputs require evidence-backed business requests,
  datasets, and field-role mappings; these are not invented.
- Visual storytelling requires source-supported page purpose, hierarchy, titles and sequence;
  presentation prompts remain pending when complete evidence is absent.

Complete candidate pairs remain pending review. They are never written into the approved
`train.jsonl`, `validation.jsonl`, or `evaluation.jsonl` splits and never receive automatic
approval. The approval packet includes every complete pair, exact content hashes, and the
dataset-manifest SHA-256.
