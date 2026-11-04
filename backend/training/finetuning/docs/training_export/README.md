# D2.5 Portable Visualization Training Exporter

## Build

```bash
uv run python -m insightos_training.training_export build \
  --source /mnt/c/Users/NITHIN/Desktop/data \
  --extraction-root ~/.cache/insightos/powerbi_reference \
  --destination ~/.cache/insightos/training_exports
```

The generated bundle is:

```text
~/.cache/insightos/training_exports/insightos_qwen3_0_6b_training_bundle.zip
```

## Review

Generate a review queue from a bundle:

```bash
uv run python -m insightos_training.training_export review-template \
  --bundle ~/.cache/insightos/training_exports/insightos_qwen3_0_6b_training_bundle.zip \
  --output /tmp/insightos_review.csv
```

The default build contains no approved training records. Human reviewers must edit only a copied
decision file and set an exact candidate ID, candidate content hash, decision, reviewer identity,
timestamp, and rationale.

Apply explicit decisions:

```bash
uv run python -m insightos_training.training_export apply-decisions \
  --decisions /path/to/review_decisions.csv
```

Stale hashes and unknown candidate IDs are rejected. The exporter does not approve records and has
no default approval path.

## Verify

```bash
uv run python -m insightos_training.training_export verify \
  --bundle ~/.cache/insightos/training_exports/insightos_qwen3_0_6b_training_bundle.zip
```

Verification checks ZIP integrity, manifest file hashes, source provenance, JSONL syntax, canonical
candidate IDs, split absence of unapproved rows, and zero-approved readiness status.

## D2.5.1 classification and integrity

The export separates chart selection, chart alternatives, complete configurations, negative
examples, dashboard layouts, and incomplete Power BI visual metadata. Complete configurations
are never inferred from partial source fields. A configuration is exported only when it contains
the required InsightOS visualization output contract.

`review_queue.csv` and `pending_review.csv` contain actionable pending records only. Excluded
records are available in `excluded_records.csv` and `excluded_records.jsonl`. Incomplete Power BI
metadata is explicitly identified with missing business-question, schema, semantic-type,
aggregation, context, configuration, tooltip, and formatting classifications.

## Kaggle

After verification, calculate SHA-256 and upload the ZIP only to a private Kaggle dataset:

```bash
sha256sum ~/.cache/insightos/training_exports/insightos_qwen3_0_6b_training_bundle.zip
```

The archive also has an adjacent `.zip.sha256` sidecar. The archive checksum is external and must
not be inferred from `manifest.json`, which contains internal file hashes. The included
`kaggle/load_dataset.py` opens and verifies the ZIP, validates internal hashes and conversation
structure, refuses blocked bundles, and reports exact split counts.

Do not upload original PBIX, PDF, or confidential source documents. Do not start training or download
the Qwen model as part of this exporter.
