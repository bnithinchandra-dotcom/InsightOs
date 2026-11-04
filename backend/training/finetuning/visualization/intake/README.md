# Visualization Dataset Intake

The intake framework creates review-only bundles for future visualization datasets. It does
not approve examples, alter the D1 development corpus, or create approved training exports.

## Supported inputs

CSV, JSON object/array, and JSON Lines files are parsed with the Python standard library.
XLSX, Parquet, database connections, remote downloads, and raw model access are intentionally
unsupported.

## Review bundle

`python -m insightos_training.intake candidates --source PATH --output ROOT` creates an
operation-local staging directory and atomically publishes a bundle containing:

- `source_manifest.json`
- `dataset_profile.json`
- `quality_report.json`
- `privacy_report.json`
- `visualization_candidates.jsonl`
- `intake_report.json`

The bundle never copies source data or raw sensitive values into prompt suggestions. Privacy
detection is heuristic and candidates remain `pending_review`. Registry identifiers are loaded
read-only and are hypotheses, not expert approvals.

## Commands

- `inspect`: preview format, size, checksum, and columns without publishing.
- `candidates` / `profile`: publish one review-ready bundle.
- `validate-bundle`: verify required files, candidate state, and recorded checksums.
- `inspect-report`: print a bundle report.

## Safety

Temporary directories must be created under the configured output root and are removed only
by the operation that created them. Symbolic-link sources and duplicate source bundles are
rejected. Successful publication uses atomic directory rename. A dataset is never relabeled as
approved and never enters a training split.
